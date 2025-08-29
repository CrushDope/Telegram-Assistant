import re
import logging
import os
import asyncio
from collections import defaultdict
from telethon import events
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
from .telegram_handler import TelegramHandler
from .youtube_handler import YouTubeHandler
from .douyin_handler import CustomDouyinHandler
from .bilibili_handler import BilibiliHandler

logger = logging.getLogger(__name__)


class EventHandler:
    def __init__(self, config):
        self.config = config
        self.telegram_handler = TelegramHandler(config)
        self.youtube_handler = YouTubeHandler(config)
        self.douyin_handler = CustomDouyinHandler(
            config.get("douyin", {}).get("cookie")
        )
        self.bilibili_handler = BilibiliHandler(config.get("bilibili", {}))
        self.send_file = config.get("send_file", False)
        self.transfer_config = config.get("transfer_message", [])

        # 创建临时目录
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
        self.media_group_delay = config.get("media_group_delay", 3.0)  # 媒体组等待时间

    async def send_video_to_user(self, event, file_path):
        """统一的发送文件方法"""
        if self.send_file:
            # 判断是视频还是音频
            is_audio = file_path.lower().endswith(
                (".mp3", ".m4a", ".ogg", ".wav", ".flac")
            )

            if is_audio:
                # 音频文件
                await event.client.send_file(
                    event.chat_id,
                    file_path,
                    force_document=False,
                    attributes=[],  # 音频属性
                )
            else:
                # 视频或其他文件
                await event.client.send_file(
                    event.chat_id,
                    file_path,
                    supports_streaming=True,
                    force_document=False,
                )

    def register_message_transfer(self, client):
        """注册消息转发处理程序（适用于用户客户端）"""
        if not self.transfer_config:
            logger.info("未配置消息转发规则，跳过注册转发处理程序")
            return

        logger.info(
            f"正在注册消息转发处理程序，共有 {len(self.transfer_config)} 条规则"
        )

        @client.on(events.NewMessage)
        async def handle_message_transfer(event):
            """处理来自任何聊天的新消息并进行转发"""
            try:
                # 获取当前聊天的ID
                chat = await event.get_chat()
                group_id = event.chat_id
                chat_username = getattr(chat, "username", None)

                # 遍历转发配置
                for transfer in self.transfer_config:
                    source_chat = transfer.get("source_chat")

                    # 检查是否匹配源聊天（通过ID或用户名）
                    source_match = False
                    if source_chat and (
                        str(group_id) == str(source_chat)
                        or (chat_username and f"@{chat_username}" == source_chat)
                    ):
                        source_match = True

                    if source_match:
                        target_chat = transfer.get("target_chat")
                        include_keywords = transfer.get("include_keywords", [])
                        direct = transfer.get("direct", False)
                        # 检查是否需要根据关键词过滤
                        should_transfer = True
                        if include_keywords:
                            message_text = (
                                event.message.text if event.message.text else ""
                            )
                            # 如果指定了关键词，至少匹配一个关键词才转发
                            should_transfer = any(
                                keyword in message_text for keyword in include_keywords
                            )

                        if should_transfer:
                            try:
                                if direct:
                                    logger.info(f"直接转发消息: {event.message.text}")
                                    # 检查消息是否包含photo
                                    if event.message.photo:
                                        # 如果有照片，下载到临时文件再发送
                                        temp_file_path = os.path.join(
                                            self.temp_dir,
                                            f"photo_{event.message.id}.jpg",
                                        )
                                        await event.message.download_media(
                                            temp_file_path
                                        )

                                        # 发送文本和照片
                                        await client.send_message(
                                            target_chat,
                                            (
                                                event.message.text
                                                if event.message.text
                                                else ""
                                            ),
                                            file=temp_file_path,
                                        )

                                        # 删除临时文件
                                        if os.path.exists(temp_file_path):
                                            os.remove(temp_file_path)
                                    else:
                                        # 没有照片，只发送文本
                                        await client.send_message(
                                            target_chat, event.message.text
                                        )
                                else:
                                    # 转发消息
                                    await client.forward_messages(
                                        target_chat, event.message
                                    )
                                    logger.info(
                                        f"已将消息从 {source_chat} 转发到 {target_chat}"
                                    )
                            except Exception as e:
                                logger.error(f"转发消息时出错: {str(e)}")

            except Exception as e:
                logger.error(f"处理消息转发时出错: {str(e)}")

    def register_handlers(self, client):
        """注册所有事件处理器"""

        @client.on(events.NewMessage(pattern="/start"))
        async def start(event):
            """处理 /start 命令"""
            await event.reply("你好！请转发视频给我，我会自动下载到指定文件夹。")

        @client.on(events.NewMessage)
        async def handle_message(event):
            """处理新消息"""
            try:
                # 先检查是否需要转发消息
                await self._handle_message_transfer(event)

                # 检查是否是媒体组消息
                if event.message.grouped_id:
                    await self._handle_media_group(event)
                    return

                message_text = event.message.text if event.message.text else ""

                # 检查是否是YouTube链接
                youtube_pattern = r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/.*|(https?://)?(m\.)?(youtube\.com|youtu\.be)/.*"
                douyin_pattern = r"https://v\.douyin\.com/.*?/"
                is_youtube = bool(re.match(youtube_pattern, message_text))
                is_douyin = bool(re.search(douyin_pattern, message_text))
                is_bilibili = (
                    "bilibili.com" in event.message.text
                    or "b23.tv" in event.message.text
                )
                if is_youtube:
                    await self._handle_youtube_message(event)
                elif is_douyin:
                    await self._handle_douyin_message(event)
                elif is_bilibili:
                    await self.handle_bilibili_message(event)
                elif event.message.media:
                    await self._handle_telegram_media(event)

            except Exception as e:
                logger.error(f"处理消息时出错: {str(e)}")
                await event.reply(f"处理消息时出错: {str(e)}")

    async def _handle_media_group(self, event):
        """处理媒体组（相册）消息"""
        try:
            group_id = event.message.grouped_id
            logger.info(f"收到媒体组消息，组ID: {group_id}")

            # 添加消息到媒体组
            self.media_groups[group_id].append(event.message)

            # 如果这个组还没有处理任务，创建一个延迟任务
            if group_id not in self.group_tasks:
                self.group_tasks[group_id] = asyncio.create_task(
                    self._process_media_group_with_delay(group_id)
                )

            await event.reply(f"📸 检测到媒体组消息，正在等待所有媒体到达...")

        except Exception as e:
            logger.error(f"处理媒体组消息时出错: {str(e)}")
            await event.reply(f"处理媒体组时出错: {str(e)}")

    async def _process_media_group_with_delay(self, group_id):
        """等待一段时间后处理完整的媒体组"""
        try:
            # 等待指定时间，确保所有媒体消息都到达
            await asyncio.sleep(self.media_group_delay)

            messages = self.media_groups.get(group_id, [])
            if not messages:
                return

            # 获取第一条消息的文字作为整个媒体组的标题
            caption = messages[0].text or "无标题媒体组"
            chat_id = messages[0].chat_id

            logger.info(f"开始处理媒体组 {group_id}, 包含 {len(messages)} 个媒体")

            # 下载所有媒体文件到临时目录
            downloaded_files = []
            for i, message in enumerate(messages):
                if message.media:
                    try:
                        # 下载媒体文件到临时目录
                        temp_file_path = await message.download_media(file=self.temp_dir)
                        
                        # 获取原始文件名
                        original_filename = os.path.basename(temp_file_path)
                        
                        # 确定媒体类型
                        if isinstance(message.media, MessageMediaPhoto):
                            media_type = 'photo'
                        elif isinstance(message.media, MessageMediaDocument):
                            # 检查是否是视频
                            document = message.media.document
                            if hasattr(document, 'mime_type') and document.mime_type.startswith('video/'):
                                media_type = 'video'
                            else:
                                media_type = 'other'
                        else:
                            media_type = 'other'
                        
                        downloaded_files.append({
                            'temp_path': temp_file_path,
                            'original_filename': original_filename,
                            'type': media_type,
                            'message_id': message.id,
                            'index': i
                        })
                        logger.info(f"媒体组文件下载成功: {temp_file_path} (类型: {media_type})")
                    except Exception as e:
                        logger.error(f"下载媒体组文件时出错: {str(e)}")

            # 处理媒体组文件
            if downloaded_files:
                # 统计文件信息
                total_files = len(downloaded_files)
                photo_count = sum(1 for f in downloaded_files if f['type'] == 'photo')
                video_count = sum(1 for f in downloaded_files if f['type'] == 'video')
                other_count = total_files - photo_count - video_count
                
                logger.info(f"媒体组 {group_id} 统计: {total_files}个文件, {photo_count}张图片, {video_count}个视频, {other_count}个其他文件")
                
                success, result = await self.telegram_handler.process_media_group(
                    group_id, downloaded_files, caption
                )
                
                if success:
                    # 发送处理完成的通知
                    first_message = messages[0]
                    try:
                        summary_msg = (
                            f"✅ 媒体组处理完成！\n"
                            f"📁 目录: {os.path.basename(result['directory'])}\n"
                            f"📊 统计: {total_files}个文件\n"
                            f"🖼️ 图片: {photo_count}张\n"
                            f"🎬 视频: {video_count}个"
                        )
                        if other_count > 0:
                            summary_msg += f"\n📎 其他: {other_count}个"
                        
                        await first_message.reply(summary_msg)
                        logger.info(f"媒体组 {group_id} 处理完成: {result}")
                    except Exception as e:
                        logger.error(f"发送媒体组完成通知时出错: {str(e)}")
                else:
                    logger.error(f"媒体组 {group_id} 处理失败: {result}")
                    # 发送失败通知
                    first_message = messages[0]
                    try:
                        await first_message.reply(f"❌ 媒体组处理失败: {result}")
                    except Exception as e:
                        logger.error(f"发送媒体组失败通知时出错: {str(e)}")

            # 清理
            if group_id in self.media_groups:
                del self.media_groups[group_id]
            if group_id in self.group_tasks:
                del self.group_tasks[group_id]

        except Exception as e:
            logger.error(f"处理媒体组延迟任务时出错: {str(e)}")
            # 清理出错的组
            if group_id in self.media_groups:
                del self.media_groups[group_id]
            if group_id in self.group_tasks:
                del self.group_tasks[group_id]
            
            # 尝试发送错误通知
            try:
                if messages and len(messages) > 0:
                    await messages[0].reply(f"❌ 处理媒体组时发生错误: {str(e)}")
            except:
                pass

    async def _handle_message_transfer(self, event):
        """处理消息转发（适用于机器人客户端）"""
        if not self.transfer_config:
            return

        # 跳过媒体组消息的转发，避免重复处理
        if event.message.grouped_id:
            return

        # 获取当前聊天的ID
        chat_id = event.chat_id

        # 遍历转发配置
        for transfer in self.transfer_config:
            source_chat = transfer.get("source_chat")
            target_chat = transfer.get("target_chat")
            include_keywords = transfer.get("include_keywords", [])

            # 检查是否匹配源聊天
            if str(chat_id) == str(source_chat):
                # 检查是否需要根据关键词过滤
                should_transfer = True
                if include_keywords:
                    message_text = event.message.text if event.message.text else ""
                    # 如果指定了关键词，至少匹配一个关键词才转发
                    should_transfer = any(
                        keyword in message_text for keyword in include_keywords
                    )

                if should_transfer:
                    try:
                        # 检查消息是否包含photo
                        if event.message.photo:
                            # 如果有照片，下载到临时文件再发送
                            temp_file_path = os.path.join(
                                self.temp_dir, f"photo_{event.message.id}.jpg"
                            )
                            await event.message.download_media(temp_file_path)

                            # 发送文本和照片
                            await event.client.send_message(
                                target_chat,
                                (event.message.text if event.message.text else ""),
                                file=temp_file_path,
                            )

                            # 删除临时文件
                            if os.path.exists(temp_file_path):
                                os.remove(temp_file_path)

                            logger.info(
                                f"已将图文消息从 {source_chat} 发送到 {target_chat}"
                            )
                        else:
                            # 转发消息
                            await event.client.forward_messages(
                                target_chat, event.message
                            )
                            logger.info(
                                f"已将消息从 {source_chat} 转发到 {target_chat}"
                            )
                    except Exception as e:
                        logger.error(f"转发消息时出错: {str(e)}")

    async def _handle_douyin_message(self, event):
        try:
            match = re.findall(r"https?://v\.douyin\.com/.*?/", event.message.text)
            if match:
                await event.reply(f"开始下载抖音视频: {match[0]}")
                url = match[0]
                video = await self.douyin_handler.download_video(url)
                if video:
                    await event.reply(
                        f"✅ 抖音视频下载完成！\n"
                        f"标题: {video.get('desc')}\n"
                        f"保存位置: {video.get('dest_path')}"
                    )
                else:
                    await event.reply("无法下载该抖音视频，请检查链接是否有效。")
            else:
                await event.reply("无法下载该抖音视频，请检查链接是否有效。")
        except Exception as e:
            await event.reply(f"下载抖音视频时出错: {str(e)}")

    async def _handle_youtube_message(self, event):
        """处理YouTube链接消息"""
        status_message = await event.reply("开始解析YouTube下载链接...")
        try:
            success, result = await self.youtube_handler.download_video(
                event.message.text,
                lambda msg: status_message.edit(msg) if status_message else None,
            )

            if success:
                # 判断下载的文件类型
                file_type = "视频"
                if result.lower().endswith((".mp3", ".m4a", ".ogg", ".wav", ".flac")):
                    file_type = "音频"

                await event.reply(
                    f"✅ YouTube{file_type}下载完成！\n" f"保存位置: {result}"
                )
            else:
                await event.reply(f"❌ YouTube视频下载失败！\n" f"错误: {result}")
        except Exception as e:
            error_msg = str(e)
            if "Sign in to confirm you're not a bot" in error_msg:
                await event.reply(
                    "YouTube下载失败: 需要验证。\n"
                    "请检查配置文件中的 youtube_download.cookies 是否正确设置。"
                )
            else:
                await event.reply(f"YouTube下载失败: {error_msg}")

    async def _handle_telegram_media(self, event):
        """处理Telegram媒体消息"""
        status_message = await event.reply("开始下载媒体文件...")
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

    async def handle_bilibili_message(self, message):
        """处理B站消息"""
        try:
            await message.reply("正在下载B站视频，请稍候...")
            url = re.findall(
                r"https://www\.bilibili\.com/video/.*|https://b23\.tv/.*", message.text
            )
            if url:
                video = await self.bilibili_handler.download_video(url[0])
                if video:
                    await message.reply(
                        f"✅ B站视频下载完成！\n"
                        f"标题: {video.get('title')}\n"
                        f"保存位置: {video.get('path')}"
                    )
                    return True
            else:
                await message.reply("下载B站视频失败,请检查链接是否有效")
                return False

        except Exception as e:
            await message.reply(f"处理B站视频失败: {str(e)}")
            return False
