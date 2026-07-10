import logging
import os
import asyncio
from collections import defaultdict
from telethon import events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from .telegram_handler import TelegramHandler

logger = logging.getLogger(__name__)


class EventHandler:
    def __init__(self, config):
        self.config = config
        self.telegram_handler = TelegramHandler(config)
        self.send_file = config.get("send_file", False)

        self.temp_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "temp",
        )
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

        self.media_groups = defaultdict(list)
        self.group_tasks = {}
        self.media_group_delay = config.get("media_group_delay", 3.0)
        self.user_client = None

    def register_handlers(self, client, user_client=None):
        """注册所有事件处理器"""
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
                    logger.info(f"[handle_message] chat={chat_id} msg={msg_id} 检测到转发媒体消息，尝试拉取原始媒体组")
                    handled = await self._handle_forwarded_media(event)
                    if handled:
                        return

                if event.message.media:
                    logger.info(f"[handle_message] chat={chat_id} msg={msg_id} 处理单条媒体消息")
                    await self._handle_telegram_media(event)
                else:
                    logger.debug(f"[handle_message] chat={chat_id} msg={msg_id} 无媒体内容，忽略")

            except Exception as e:
                logger.exception(f"[handle_message] chat={chat_id} msg={msg_id} 处理异常: {e}")
                await event.reply(f"处理消息时出错: {str(e)}")

    async def _handle_media_group(self, event):
        """处理媒体组（相册）消息"""
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

            await event.reply("📸 检测到媒体组消息，正在等待所有媒体到达...")

        except Exception as e:
            logger.exception(f"[_handle_media_group] group={group_id} 异常: {e}")
            await event.reply(f"处理媒体组时出错: {str(e)}")

    async def _process_media_group_with_delay(self, group_id):
        """等待一段时间后处理完整的媒体组"""
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

            downloaded_files = await self._download_messages_concurrently(messages, tag=f"group={group_id}")

            if not downloaded_files:
                logger.error(f"[_process_media_group_with_delay] group={group_id} 所有文件下载失败")
                return

            total_files = len(downloaded_files)
            photo_count = sum(1 for f in downloaded_files if f["type"] == "photo")
            video_count = sum(1 for f in downloaded_files if f["type"] == "video")
            other_count = total_files - photo_count - video_count
            logger.info(f"[_process_media_group_with_delay] group={group_id} 下载完成: {total_files}个 ({photo_count}图/{video_count}视频/{other_count}其他)")

            success, result = await self.telegram_handler.process_media_group(group_id, downloaded_files, caption)

            first_message = messages[0]
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
                logger.error(f"[_process_media_group_with_delay] group={group_id} process_media_group 返回失败: {result}")
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
            logger.debug(f"[_process_media_group_with_delay] group={group_id} 状态已清理")

    async def _handle_telegram_media(self, event):
        """处理单条 Telegram 媒体消息"""
        msg_id = event.message.id
        chat_id = event.chat_id
        logger.info(f"[_handle_telegram_media] chat={chat_id} msg={msg_id} 开始下载")
        await event.reply("开始下载媒体文件...")
        try:
            success, result = await self.telegram_handler.process_media(event)

            if success:
                logger.info(f"[_handle_telegram_media] chat={chat_id} msg={msg_id} 下载成功: {result['path']}")
                await event.reply(
                    f"✅ {result['type']} 文件下载完成！\n"
                    f"文件名: {result['filename']}\n"
                    f"保存位置: {result['path']}"
                )
            else:
                logger.error(f"[_handle_telegram_media] chat={chat_id} msg={msg_id} 下载失败: {result}")
                await event.reply(f"❌ 下载失败: {result}")
        except Exception as e:
            logger.exception(f"[_handle_telegram_media] chat={chat_id} msg={msg_id} 异常: {e}")
            await event.reply(f"处理媒体文件时出错: {str(e)}")

    async def _handle_forwarded_media(self, event):
        """
        处理转发的媒体消息。
        如果 user client 可用，尝试从原始来源拉取完整媒体组。
        返回 True 表示已处理，False 表示降级到普通单文件下载。
        """
        msg_id = event.message.id
        if not self.user_client:
            logger.debug(f"[_handle_forwarded_media] msg={msg_id} user client 不可用，降级")
            return False

        fwd = event.message.fwd_from
        if not fwd or not fwd.from_id or not fwd.channel_post:
            logger.debug(f"[_handle_forwarded_media] msg={msg_id} fwd_from 信息不完整，降级")
            return False

        original_msg_id = fwd.channel_post
        logger.info(f"[_handle_forwarded_media] msg={msg_id} 原始来源 peer={fwd.from_id} original_msg_id={original_msg_id}")

        try:
            original_chat = await self.user_client.get_entity(fwd.from_id)
            logger.info(f"[_handle_forwarded_media] msg={msg_id} 获取原始来源成功: {getattr(original_chat, 'title', original_chat.id)}")
        except Exception as e:
            logger.warning(f"[_handle_forwarded_media] msg={msg_id} 获取原始来源失败，降级: {e}")
            return False

        album_messages = await self._fetch_original_album(original_chat, original_msg_id)
        logger.info(f"[_handle_forwarded_media] msg={msg_id} 原始媒体组共 {len(album_messages)} 条消息")

        if len(album_messages) <= 1:
            logger.info(f"[_handle_forwarded_media] msg={msg_id} 原始消息非媒体组，降级为单文件下载")
            return False

        await event.reply(f"🔍 检测到原始消息包含 {len(album_messages)} 个文件，开始下载完整媒体组...")

        downloaded_files = await self._download_messages_concurrently(album_messages, client=self.user_client, tag=f"fwd_msg={msg_id}")

        if not downloaded_files:
            logger.error(f"[_handle_forwarded_media] 所有文件下载失败")
            await event.reply("❌ 媒体组文件下载失败")
            return True

        caption = album_messages[0].message or ""
        group_id = album_messages[0].grouped_id or original_msg_id
        logger.info(f"[_handle_forwarded_media] 开始处理媒体组 group={group_id} caption='{caption[:50]}'")

        success, result = await self.telegram_handler.process_media_group(group_id, downloaded_files, caption)

        total = len(downloaded_files)
        photo_count = sum(1 for f in downloaded_files if f["type"] == "photo")
        video_count = sum(1 for f in downloaded_files if f["type"] == "video")
        other_count = total - photo_count - video_count

        if success:
            logger.info(f"[_handle_forwarded_media] group={group_id} 处理完成: {result['directory']}")
            summary = (
                f"✅ 媒体组处理完成！\n"
                f"📁 目录: {os.path.basename(result['directory'])}\n"
                f"📊 统计: {total}个文件\n"
                f"🖼️ 图片: {photo_count}张\n"
                f"🎬 视频: {video_count}个"
            )
            if other_count > 0:
                summary += f"\n📎 其他: {other_count}个"
            await event.reply(summary)
        else:
            logger.error(f"[_handle_forwarded_media] group={group_id} 处理失败: {result}")
            await event.reply(f"❌ 媒体组处理失败: {result}")

        return True

    async def _download_messages_concurrently(self, messages, client=None, tag=""):
        """
        并发下载消息列表中的所有媒体文件。
        client 为 None 时每条消息用自身的 download_media（bot client 路径）。
        """
        media_messages = [(i, msg) for i, msg in enumerate(messages) if msg.media]
        if not media_messages:
            return []

        logger.info(f"[_download_messages_concurrently] {tag} 并发下载 {len(media_messages)} 个文件")

        async def download_one(index, msg):
            try:
                if client:
                    path = await client.download_media(msg, file=self.temp_dir)
                else:
                    path = await msg.download_media(file=self.temp_dir)

                if not path:
                    logger.warning(f"[_download_messages_concurrently] {tag} msg={msg.id} 返回 None，跳过")
                    return None

                if isinstance(msg.media, MessageMediaPhoto):
                    media_type = "photo"
                elif isinstance(msg.media, MessageMediaDocument):
                    doc = msg.media.document
                    media_type = "video" if (hasattr(doc, "mime_type") and doc.mime_type.startswith("video/")) else "other"
                else:
                    media_type = "other"

                logger.info(f"[_download_messages_concurrently] {tag} msg={msg.id} 下载完成: {path} 类型={media_type}")
                return {
                    "temp_path": path,
                    "original_filename": os.path.basename(path),
                    "type": media_type,
                    "message_id": msg.id,
                    "index": index,
                }
            except Exception as e:
                logger.error(f"[_download_messages_concurrently] {tag} msg={msg.id} 下载失败: {e}")
                return None

        results = await asyncio.gather(*(download_one(i, msg) for i, msg in media_messages))

        downloaded = [r for r in results if r is not None]
        # 保持原始消息顺序
        downloaded.sort(key=lambda f: f["index"])

        failed = len(media_messages) - len(downloaded)
        if failed:
            logger.warning(f"[_download_messages_concurrently] {tag} {failed} 个文件下载失败")
        logger.info(f"[_download_messages_concurrently] {tag} 完成: {len(downloaded)}/{len(media_messages)} 个文件下载成功")
        return downloaded

    async def _fetch_original_album(self, chat, msg_id):
        """从原始来源获取完整媒体组"""
        logger.info(f"[_fetch_original_album] chat={getattr(chat, 'id', chat)} msg_id={msg_id} 获取窗口消息")
        try:
            window = await self.user_client.get_messages(
                chat,
                min_id=max(0, msg_id - 10),
                max_id=msg_id + 10,
                limit=20,
            )
            logger.debug(f"[_fetch_original_album] 获取到 {len(window)} 条消息")

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
            logger.exception(f"[_fetch_original_album] 获取媒体组消息异常: {e}")
            return []
