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

        # 临时目录
        self.temp_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "temp",
        )
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

        # 媒体组处理相关
        self.media_groups = defaultdict(list)
        self.group_tasks = {}
        self.media_group_delay = config.get("media_group_delay", 3.0)

    def register_handlers(self, client):
        """注册所有事件处理器"""

        @client.on(events.NewMessage(pattern="/start"))
        async def start(event):
            await event.reply("你好！请转发媒体文件给我，我会自动下载到指定文件夹。")

        @client.on(events.NewMessage)
        async def handle_message(event):
            try:
                if event.message.grouped_id:
                    await self._handle_media_group(event)
                    return

                if event.message.media:
                    await self._handle_telegram_media(event)

            except Exception as e:
                logger.error(f"处理消息时出错: {str(e)}")
                await event.reply(f"处理消息时出错: {str(e)}")

    async def _handle_media_group(self, event):
        """处理媒体组（相册）消息"""
        try:
            group_id = event.message.grouped_id
            logger.info(f"收到媒体组消息，组ID: {group_id}")

            self.media_groups[group_id].append(event.message)

            if group_id not in self.group_tasks:
                self.group_tasks[group_id] = asyncio.create_task(
                    self._process_media_group_with_delay(group_id)
                )

            await event.reply("📸 检测到媒体组消息，正在等待所有媒体到达...")

        except Exception as e:
            logger.error(f"处理媒体组消息时出错: {str(e)}")
            await event.reply(f"处理媒体组时出错: {str(e)}")

    async def _process_media_group_with_delay(self, group_id):
        """等待一段时间后处理完整的媒体组"""
        messages = []
        try:
            await asyncio.sleep(self.media_group_delay)

            messages = self.media_groups.get(group_id, [])
            if not messages:
                return

            caption = messages[0].text or "无标题媒体组"
            logger.info(f"开始处理媒体组 {group_id}, 包含 {len(messages)} 个媒体")

            downloaded_files = []
            for i, message in enumerate(messages):
                if message.media:
                    try:
                        temp_file_path = await message.download_media(file=self.temp_dir)

                        if isinstance(message.media, MessageMediaPhoto):
                            media_type = "photo"
                        elif isinstance(message.media, MessageMediaDocument):
                            document = message.media.document
                            if hasattr(document, "mime_type") and document.mime_type.startswith("video/"):
                                media_type = "video"
                            else:
                                media_type = "other"
                        else:
                            media_type = "other"

                        downloaded_files.append({
                            "temp_path": temp_file_path,
                            "original_filename": os.path.basename(temp_file_path),
                            "type": media_type,
                            "message_id": message.id,
                            "index": i,
                        })
                        logger.info(f"媒体组文件下载成功: {temp_file_path} (类型: {media_type})")
                    except Exception as e:
                        logger.error(f"下载媒体组文件时出错: {str(e)}")

            if downloaded_files:
                total_files = len(downloaded_files)
                photo_count = sum(1 for f in downloaded_files if f["type"] == "photo")
                video_count = sum(1 for f in downloaded_files if f["type"] == "video")
                other_count = total_files - photo_count - video_count

                success, result = await self.telegram_handler.process_media_group(
                    group_id, downloaded_files, caption
                )

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
                        logger.error(f"发送媒体组完成通知时出错: {str(e)}")
                else:
                    try:
                        await first_message.reply(f"❌ 媒体组处理失败: {result}")
                    except Exception as e:
                        logger.error(f"发送媒体组失败通知时出错: {str(e)}")

        except Exception as e:
            logger.error(f"处理媒体组延迟任务时出错: {str(e)}")
            try:
                if messages:
                    await messages[0].reply(f"❌ 处理媒体组时发生错误: {str(e)}")
            except Exception:
                pass
        finally:
            self.media_groups.pop(group_id, None)
            self.group_tasks.pop(group_id, None)

    async def _handle_telegram_media(self, event):
        """处理Telegram媒体消息"""
        await event.reply("开始下载媒体文件...")
        try:
            success, result = await self.telegram_handler.process_media(event)

            if success:
                await event.reply(
                    f"✅ {result['type']} 文件下载完成！\n"
                    f"文件名: {result['filename']}\n"
                    f"保存位置: {result['path']}"
                )
            else:
                await event.reply(f"❌ 下载失败: {result}")
        except Exception as e:
            await event.reply(f"处理媒体文件时出错: {str(e)}")
