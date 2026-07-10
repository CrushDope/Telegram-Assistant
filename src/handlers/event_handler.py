import logging
import os
import asyncio
from collections import defaultdict
from telethon import events
from telethon.tl.types import (
    MessageMediaPhoto, MessageMediaDocument,
    PeerChannel, PeerChat, PeerUser,
)
from .telegram_handler import TelegramHandler
from ..constants import TELEGRAM_TEMP_DIR
from ..services.task_queue import TaskQueue, TASK_SINGLE, TASK_FORWARDED

logger = logging.getLogger(__name__)


class EventHandler:
    def __init__(self, config):
        self.config = config
        self.telegram_handler = TelegramHandler(config)
        self.send_file = config.get("send_file", False)

        self.media_groups = defaultdict(list)
        self.group_tasks = {}
        self.media_group_delay = config.get("media_group_delay", 3.0)
        self.user_client = None
        self.bot_client = None

        max_concurrent = config.get("max_concurrent_downloads", 2)
        self._download_semaphore = asyncio.Semaphore(max_concurrent)
        logger.info(f"[EventHandler] 最大并发下载数: {max_concurrent}")

        self.task_queue = TaskQueue()

    def register_handlers(self, client, user_client=None):
        """注册所有事件处理器"""
        self.bot_client = client
        self.user_client = user_client
        if user_client:
            logger.info("[EventHandler] user client 已启用，支持原始媒体组拉取")
        else:
            logger.info("[EventHandler] user client 未配置，转发消息将降级为单文件下载")

        @client.on(events.NewMessage(pattern="/start"))
        async def start(event):
            logger.info(f"[start] chat={event.chat_id} 收到 /start 命令")
            await event.reply("你好！请转发媒体文件给我，我会自动下载到指定文件夹。")

        @client.on(events.NewMessage)
        async def handle_message(event):
            msg_id = event.message.id
            chat_id = event.chat_id
            try:
                if event.message.grouped_id:
                    logger.info(f"[handle_message] chat={chat_id} msg={msg_id} 检测到媒体组消息 grouped_id={event.message.grouped_id}")
                    await self._handle_media_group(event)
                    return

                if event.message.media and event.message.fwd_from:
                    fwd = event.message.fwd_from
                    if fwd and fwd.from_id and fwd.channel_post:
                        peer_id, peer_type = self._extract_peer(fwd.from_id)
                        task_id = self.task_queue.add_forwarded(
                            chat_id, msg_id, peer_id, peer_type, fwd.channel_post
                        )
                    else:
                        task_id = self.task_queue.add_single(chat_id, msg_id)
                    logger.info(f"[handle_message] chat={chat_id} msg={msg_id} 转发媒体任务入队 task_id={task_id}")
                    await event.reply(f"📥 收到转发媒体，任务已入队\ntask_id: `{task_id}`")
                    asyncio.create_task(self._run_task(task_id, event=event))
                    return

                if event.message.media:
                    task_id = self.task_queue.add_single(chat_id, msg_id)
                    logger.info(f"[handle_message] chat={chat_id} msg={msg_id} 单文件任务入队 task_id={task_id}")
                    await event.reply(f"📥 收到媒体文件，任务已入队\ntask_id: `{task_id}`")
                    asyncio.create_task(self._run_task(task_id, event=event))
                else:
                    logger.debug(f"[handle_message] chat={chat_id} msg={msg_id} 无媒体内容，忽略")

            except Exception as e:
                logger.exception(f"[handle_message] chat={chat_id} msg={msg_id} 处理异常: {e}")
                await event.reply(f"处理消息时出错: {str(e)}")

    # ------------------------------------------------------------------
    # 任务执行入口
    # ------------------------------------------------------------------

    async def _run_task(self, task_id: str, event=None, is_resume: bool = False):
        """统一任务执行入口，支持新任务和重启恢复"""
        task = self.task_queue.get(task_id)
        if not task:
            logger.error(f"[_run_task] task_id={task_id} 不存在")
            return

        chat_id = task["chat_id"]

        waiting = self._download_semaphore._value == 0
        if waiting:
            logger.info(f"[_run_task] task_id={task_id} 等待下载槽位...")
            await self.bot_client.send_message(chat_id, f"⏳ 任务排队等待中，当前下载已满\ntask_id: `{task_id}`")

        async with self._download_semaphore:
            self.task_queue.set_processing(task_id)
            await self.bot_client.send_message(chat_id, f"🚀 开始执行任务\ntask_id: `{task_id}`")
            try:
                if task["task_type"] == TASK_SINGLE:
                    await self._exec_single_task(task, event=event, is_resume=is_resume)
                elif task["task_type"] == TASK_FORWARDED:
                    await self._exec_forwarded_task(task, event=event, is_resume=is_resume)
                self.task_queue.set_done(task_id)
            except Exception as e:
                logger.exception(f"[_run_task] task_id={task_id} 执行异常: {e}")
                self.task_queue.set_failed(task_id, str(e))
                try:
                    await self.bot_client.send_message(chat_id, f"❌ 任务失败\ntask_id: `{task_id}`\n错误: {str(e)}")
                except Exception:
                    pass

    async def _exec_single_task(self, task: dict, event=None, is_resume: bool = False):
        """执行单文件下载"""
        chat_id = task["chat_id"]
        msg_id = task["message_id"]

        if is_resume:
            msg = await self._refetch_message(chat_id, msg_id)
            if not msg:
                raise RuntimeError(f"无法重新获取消息 chat={chat_id} msg={msg_id}")
            await self.bot_client.send_message(chat_id, f"🔄 恢复下载任务\nmsg_id: {msg_id}")
        else:
            msg = event.message
            await self.bot_client.send_message(chat_id, f"⬇️ 开始下载文件\nmsg_id: {msg_id}")

        logger.info(f"[_exec_single_task] chat={chat_id} msg={msg_id} is_resume={is_resume}")
        success, result = await self.telegram_handler.process_media_from_message(msg)

        if success:
            logger.info(f"[_exec_single_task] chat={chat_id} msg={msg_id} 完成: {result['path']}")
            await self.bot_client.send_message(
                chat_id,
                f"✅ 下载完成！\n"
                f"类型: {result['type']}\n"
                f"文件名: {result['filename']}\n"
                f"保存位置: {result['path']}"
            )
        else:
            raise RuntimeError(result)

    async def _exec_forwarded_task(self, task: dict, event=None, is_resume: bool = False):
        """执行转发消息的原始媒体组下载，降级时走单文件"""
        chat_id = task["chat_id"]
        msg_id = task["message_id"]

        if not self.user_client:
            logger.info(f"[_exec_forwarded_task] task={task['task_id']} user client 不可用，降级单文件")
            await self.bot_client.send_message(chat_id, "⚠️ user client 未启用，降级为单文件下载")
            await self._exec_single_task(task, event=event, is_resume=is_resume)
            return

        original_peer_id = task.get("original_peer_id")
        original_peer_type = task.get("original_peer_type")
        original_msg_id = task.get("original_message_id")

        if not original_peer_id or not original_msg_id:
            logger.info(f"[_exec_forwarded_task] task={task['task_id']} 缺少原始来源信息，降级单文件")
            await self.bot_client.send_message(chat_id, "⚠️ 缺少原始来源信息，降级为单文件下载")
            await self._exec_single_task(task, event=event, is_resume=is_resume)
            return

        peer = self._build_peer(original_peer_id, original_peer_type)
        try:
            original_chat = await self.user_client.get_entity(peer)
            chat_title = getattr(original_chat, 'title', str(original_chat.id))
            logger.info(f"[_exec_forwarded_task] 获取原始来源: {chat_title}")
            await self.bot_client.send_message(chat_id, f"🔍 获取原始来源: {chat_title}\n正在拉取媒体组...")
        except Exception as e:
            logger.warning(f"[_exec_forwarded_task] 获取原始来源失败，降级单文件: {e}")
            await self.bot_client.send_message(chat_id, f"⚠️ 获取原始来源失败: {e}\n降级为单文件下载")
            await self._exec_single_task(task, event=event, is_resume=is_resume)
            return

        album_messages = await self._fetch_original_album(original_chat, original_msg_id)
        logger.info(f"[_exec_forwarded_task] 原始媒体组共 {len(album_messages)} 条")

        if len(album_messages) <= 1:
            logger.info(f"[_exec_forwarded_task] 非媒体组，降级单文件")
            await self.bot_client.send_message(chat_id, "ℹ️ 原始消息为单文件，降级为单文件下载")
            await self._exec_single_task(task, event=event, is_resume=is_resume)
            return

        resume_hint = "🔄 恢复下载任务 — " if is_resume else ""
        await self.bot_client.send_message(
            chat_id,
            f"{resume_hint}📦 检测到完整媒体组，共 {len(album_messages)} 个文件\n开始并发下载..."
        )

        downloaded_files = await self._download_messages_concurrently(
            album_messages, client=self.user_client,
            tag=f"fwd_task={task['task_id']}", notify_chat_id=chat_id
        )
        if not downloaded_files:
            raise RuntimeError("所有文件下载失败")

        caption = album_messages[0].message or ""
        group_id = album_messages[0].grouped_id or original_msg_id
        await self.bot_client.send_message(chat_id, f"📂 所有文件下载完成，开始整理归档...")
        success, result = await self.telegram_handler.process_media_group(group_id, downloaded_files, caption)

        total = len(downloaded_files)
        photo_count = sum(1 for f in downloaded_files if f["type"] == "photo")
        video_count = sum(1 for f in downloaded_files if f["type"] == "video")
        other_count = total - photo_count - video_count

        if success:
            summary = (
                f"✅ 媒体组处理完成！\n"
                f"📁 目录: {os.path.basename(result['directory'])}\n"
                f"📊 统计: {total}个文件\n"
                f"🖼️ 图片: {photo_count}张\n"
                f"🎬 视频: {video_count}个"
            )
            if other_count > 0:
                summary += f"\n📎 其他: {other_count}个"
            await self.bot_client.send_message(chat_id, summary)
        else:
            raise RuntimeError(result)

    # ------------------------------------------------------------------
    # 重启恢复
    # ------------------------------------------------------------------

    async def resume_pending_tasks(self):
        """启动时调用，恢复所有未完成任务"""
        pending = self.task_queue.pending_tasks()
        if not pending:
            logger.info("[resume_pending_tasks] 没有未完成任务")
            return

        logger.info(f"[resume_pending_tasks] 发现 {len(pending)} 个未完成任务，开始恢复")
        for task in pending:
            chat_id = task["chat_id"]
            task_id = task["task_id"]
            logger.info(f"[resume_pending_tasks] 恢复任务 {task_id} type={task['task_type']}")
            try:
                await self.bot_client.send_message(
                    chat_id,
                    f"🔄 检测到未完成任务，正在恢复\ntask_id: `{task_id}`\n类型: {task['task_type']}"
                )
            except Exception:
                pass
            asyncio.create_task(self._run_task(task_id, is_resume=True))

    # ------------------------------------------------------------------
    # 媒体组（直接转发相册，非历史拉取路径）
    # ------------------------------------------------------------------

    async def _handle_media_group(self, event):
        group_id = event.message.grouped_id
        msg_id = event.message.id
        try:
            self.media_groups[group_id].append(event.message)
            count = len(self.media_groups[group_id])
            logger.info(f"[_handle_media_group] group={group_id} 已收到第 {count} 条消息 msg={msg_id}")

            if group_id not in self.group_tasks:
                logger.info(f"[_handle_media_group] group={group_id} 创建延迟处理任务 (等待 {self.media_group_delay}s)")
                self.group_tasks[group_id] = asyncio.create_task(
                    self._process_media_group_with_delay(group_id)
                )

            await event.reply(f"📸 检测到媒体组消息，正在等待所有媒体到达...\ngroup_id: `{group_id}`")

        except Exception as e:
            logger.exception(f"[_handle_media_group] group={group_id} 异常: {e}")
            await event.reply(f"处理媒体组时出错: {str(e)}")

    async def _process_media_group_with_delay(self, group_id):
        messages = []
        try:
            logger.debug(f"[_process_media_group_with_delay] group={group_id} 等待 {self.media_group_delay}s")
            await asyncio.sleep(self.media_group_delay)

            messages = self.media_groups.get(group_id, [])
            if not messages:
                logger.warning(f"[_process_media_group_with_delay] group={group_id} 等待后消息列表为空")
                return

            caption = messages[0].text or "无标题媒体组"
            logger.info(f"[_process_media_group_with_delay] group={group_id} 共 {len(messages)} 条消息，caption='{caption[:50]}'")

            first_message = messages[0]
            chat_id = first_message.chat_id if hasattr(first_message, 'chat_id') else None

            await first_message.reply(
                f"📦 收集完毕，共 {len(messages)} 个文件\n开始并发下载..."
            )

            async with self._download_semaphore:
                downloaded_files = await self._download_messages_concurrently(
                    messages, tag=f"group={group_id}",
                    notify_chat_id=chat_id, reply_to=first_message
                )

            if not downloaded_files:
                logger.error(f"[_process_media_group_with_delay] group={group_id} 所有文件下载失败")
                await first_message.reply("❌ 所有文件下载失败")
                return

            total_files = len(downloaded_files)
            photo_count = sum(1 for f in downloaded_files if f["type"] == "photo")
            video_count = sum(1 for f in downloaded_files if f["type"] == "video")
            other_count = total_files - photo_count - video_count

            await first_message.reply(f"📂 所有文件下载完成，开始整理归档...")
            success, result = await self.telegram_handler.process_media_group(group_id, downloaded_files, caption)

            if success:
                summary_msg = (
                    f"✅ 媒体组处理完成！\n"
                    f"📁 目录: {os.path.basename(result['directory'])}\n"
                    f"📊 统计: {total_files}个文件\n"
                    f"🖼️ 图片: {photo_count}张\n"
                    f"🎬 视频: {video_count}个"
                )
                if other_count > 0:
                    summary_msg += f"\n📎 其他: {other_count}个"
                try:
                    await first_message.reply(summary_msg)
                except Exception as e:
                    logger.error(f"[_process_media_group_with_delay] group={group_id} 发送完成通知失败: {e}")
            else:
                try:
                    await first_message.reply(f"❌ 媒体组处理失败: {result}")
                except Exception as e:
                    logger.error(f"[_process_media_group_with_delay] group={group_id} 发送失败通知异常: {e}")

        except Exception as e:
            logger.exception(f"[_process_media_group_with_delay] group={group_id} 异常: {e}")
            try:
                if messages:
                    await messages[0].reply(f"❌ 处理媒体组时发生错误: {str(e)}")
            except Exception:
                pass
        finally:
            self.media_groups.pop(group_id, None)
            self.group_tasks.pop(group_id, None)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    async def _refetch_message(self, chat_id: int, msg_id: int):
        """重启后通过 bot client 重新获取消息对象"""
        try:
            messages = await self.bot_client.get_messages(chat_id, ids=msg_id)
            return messages
        except Exception as e:
            logger.error(f"[_refetch_message] chat={chat_id} msg={msg_id} 获取失败: {e}")
            return None

    def _extract_peer(self, peer) -> tuple[int, str]:
        """从 Telethon Peer 对象提取 id 和类型字符串"""
        if isinstance(peer, PeerChannel):
            return peer.channel_id, "channel"
        if isinstance(peer, PeerChat):
            return peer.chat_id, "chat"
        if isinstance(peer, PeerUser):
            return peer.user_id, "user"
        return int(str(peer)), "unknown"

    def _build_peer(self, peer_id: int, peer_type: str):
        """从持久化数据重建 Peer 对象"""
        if peer_type == "channel":
            return PeerChannel(peer_id)
        if peer_type == "chat":
            return PeerChat(peer_id)
        return PeerUser(peer_id)

    async def _download_messages_concurrently(self, messages, client=None, tag="",
                                               notify_chat_id=None, reply_to=None):
        media_messages = [(i, msg) for i, msg in enumerate(messages) if msg.media]
        if not media_messages:
            return []

        total = len(media_messages)
        logger.info(f"[_download_messages_concurrently] {tag} 并发下载 {total} 个文件")

        async def download_one(index, msg):
            try:
                if client:
                    path = await client.download_media(msg, file=TELEGRAM_TEMP_DIR)
                else:
                    path = await msg.download_media(file=TELEGRAM_TEMP_DIR)

                if not path:
                    logger.warning(f"[_download_messages_concurrently] {tag} msg={msg.id} 返回 None，跳过")
                    if notify_chat_id:
                        try:
                            await self.bot_client.send_message(
                                notify_chat_id, f"⚠️ 第 {index+1}/{total} 个文件下载返回空，跳过"
                            )
                        except Exception:
                            pass
                    return None

                if isinstance(msg.media, MessageMediaPhoto):
                    media_type = "photo"
                elif isinstance(msg.media, MessageMediaDocument):
                    doc = msg.media.document
                    media_type = "video" if (hasattr(doc, "mime_type") and doc.mime_type.startswith("video/")) else "other"
                else:
                    media_type = "other"

                filename = os.path.basename(path)
                logger.info(f"[_download_messages_concurrently] {tag} msg={msg.id} 下载完成: {path} 类型={media_type}")

                if notify_chat_id:
                    try:
                        await self.bot_client.send_message(
                            notify_chat_id,
                            f"⬇️ 第 {index+1}/{total} 个文件下载完成\n文件名: {filename}\n类型: {media_type}"
                        )
                    except Exception:
                        pass

                return {
                    "temp_path": path,
                    "original_filename": filename,
                    "type": media_type,
                    "message_id": msg.id,
                    "index": index,
                }
            except Exception as e:
                logger.error(f"[_download_messages_concurrently] {tag} msg={msg.id} 下载失败: {e}")
                if notify_chat_id:
                    try:
                        await self.bot_client.send_message(
                            notify_chat_id, f"❌ 第 {index+1}/{total} 个文件下载失败: {e}"
                        )
                    except Exception:
                        pass
                return None

        results = await asyncio.gather(*(download_one(i, msg) for i, msg in media_messages))
        downloaded = [r for r in results if r is not None]
        downloaded.sort(key=lambda f: f["index"])

        failed = len(media_messages) - len(downloaded)
        if failed:
            logger.warning(f"[_download_messages_concurrently] {tag} {failed} 个文件下载失败")
        logger.info(f"[_download_messages_concurrently] {tag} 完成: {len(downloaded)}/{total} 个")
        return downloaded

    async def _fetch_original_album(self, chat, msg_id):
        logger.info(f"[_fetch_original_album] chat={getattr(chat, 'id', chat)} msg_id={msg_id}")
        try:
            window = await self.user_client.get_messages(
                chat,
                min_id=max(0, msg_id - 10),
                max_id=msg_id + 10,
                limit=20,
            )
            target_grouped_id = None
            for msg in window:
                if msg.id == msg_id:
                    target_grouped_id = msg.grouped_id
                    break

            if not target_grouped_id:
                logger.info(f"[_fetch_original_album] msg_id={msg_id} 不属于任何媒体组")
                return [msg for msg in window if msg.id == msg_id]

            album = [msg for msg in window if msg.grouped_id == target_grouped_id]
            logger.info(f"[_fetch_original_album] grouped_id={target_grouped_id} 找到 {len(album)} 条消息")
            return album
        except Exception as e:
            logger.exception(f"[_fetch_original_album] 异常: {e}")
            return []
