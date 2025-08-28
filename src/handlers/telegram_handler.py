import os
import re
import logging
from datetime import datetime
from telethon.tl.types import Document
from ..utils.file_utils import move_file
from ..constants import (
    TELEGRAM_TEMP_DIR,
    TELEGRAM_VIDEOS_DIR,
    TELEGRAM_AUDIOS_DIR,
    TELEGRAM_PHOTOS_DIR,
    TELEGRAM_OTHERS_DIR,
    DOUYIN_DEST_DIR,
)

logger = logging.getLogger(__name__)


class TelegramHandler:
    def __init__(self, config):
        self.config = config
        self._ensure_directories()
        # 用于跟踪每个消息的媒体文件处理状态
        self.message_media_count = {}

    def _ensure_directories(self):
        """确保所有必要的目录存在"""
        for directory in [
            TELEGRAM_TEMP_DIR,
            TELEGRAM_VIDEOS_DIR,
            TELEGRAM_AUDIOS_DIR,
            TELEGRAM_PHOTOS_DIR,
            TELEGRAM_OTHERS_DIR,
            DOUYIN_DEST_DIR,
        ]:
            os.makedirs(directory, exist_ok=True)

    def _extract_title_and_description(self, message_text):
        """从消息文本中提取标题和简介"""
        if not message_text or not message_text.strip():
            return None, None

        title_pattern = r'【([^】]+)】(.+?)(?=\n|$)'
        match = re.search(title_pattern, message_text)

        if match:
            brand = match.group(1)  # 品牌部分
            title_content = match.group(2).strip()
            description_start = match.end()
            description = message_text[description_start:].strip()
            title_content = re.sub(r'\s+', ' ', title_content)
            title = self._sanitize_filename(title_content)
            return title, description

        # 如果没有匹配到标准格式，使用第一行作为标题
        lines = message_text.strip().split('\n')
        if lines:
            title = self._sanitize_filename(lines[0].strip())
            description = '\n'.join(lines[1:]).strip() if len(lines) > 1 else ""
            return title, description

        return None, None

    def _sanitize_filename(self, filename):
        """清理文件名，移除非法字符"""
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = filename.strip('. ')
        if len(filename) > 100:
            filename = filename[:100]
        return filename

    def _should_download_file(self, media):
        """判断是否应该下载该文件"""
        if hasattr(media, "document"):
            for attr in media.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    if attr.file_name.startswith('photo_'):
                        logger.info(f"跳过以photo_开头的文件: {attr.file_name}")
                        return False
            return True
        elif hasattr(media, "photo"):
            return True
        return True

    def _get_target_directory(self, title):
        """获取统一的目标目录（视频和图片都放这里）"""
        target_dir = os.path.join(TELEGRAM_VIDEOS_DIR, title)
        os.makedirs(target_dir, exist_ok=True)
        return target_dir

    def _get_file_extension(self, media):
        """获取文件扩展名"""
        if hasattr(media, "document") and hasattr(media.document, "mime_type"):
            mime_type = media.document.mime_type
            if mime_type:
                mime_to_ext = {
                    'video/mp4': '.mp4',
                    'video/quicktime': '.mov',
                    'video/x-msvideo': '.avi',
                    'video/x-matroska': '.mkv',
                    'video/webm': '.webm',
                    'audio/mpeg': '.mp3',
                    'audio/x-wav': '.wav',
                    'audio/x-flac': '.flac',
                    'audio/m4a': '.m4a',
                    'image/jpeg': '.jpg',
                    'image/png': '.png',
                    'image/gif': '.gif',
                    'image/webp': '.webp',
                }
                return mime_to_ext.get(mime_type, f".{mime_type.split('/')[-1]}")
        elif hasattr(media, "photo"):
            return '.jpg'
        return '.bin'

    def _get_filename(self, media, title, message_id):
        """根据媒体类型生成文件名"""
        if message_id not in self.message_media_count:
            self.message_media_count[message_id] = {
                'photo_count': 0,
                'video_count': 0
            }

        media_info = self.message_media_count[message_id]

        if hasattr(media, "document") and hasattr(media.document, "mime_type"):
            mime_type = media.document.mime_type
            if mime_type and mime_type.startswith("video/"):
                media_info['video_count'] += 1
                ext = self._get_file_extension(media)
                return f"{title}{ext}"

        elif hasattr(media, "photo"):
            media_info['photo_count'] += 1
            photo_count = media_info['photo_count']
            ext = self._get_file_extension(media)
            if photo_count == 1:
                return "fanart.jpg"
            else:
                return f"fanart{photo_count-1}{ext}"

        ext = self._get_file_extension(media)
        return f"file_{datetime.now().strftime('%H%M%S')}{ext}"

    async def process_media(self, event, progress_callback=None):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            message_id = event.message.id
            message_text = event.message.text or ""

            if not self._should_download_file(media):
                return False, "跳过以photo_开头的文件"

            title, description = self._extract_title_and_description(message_text)

            if not title:
                logger.warning(f"消息 {message_id} 中未提取到标题，使用备用命名")
                title = f"media_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                title = self._sanitize_filename(title)

            target_dir = self._get_target_directory(title)
            filename = self._get_filename(media, title, message_id)

            downloaded_file = None
            if hasattr(media, "document") and isinstance(media.document, Document):
                document = media.document
                temp_file = os.path.join(
                    TELEGRAM_TEMP_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.tmp"
                )
                await event.client.download_file(
                    document,
                    file=temp_file,
                    part_size_kb=512,
                    progress_callback=progress_callback,
                )
                downloaded_file = temp_file
            else:
                downloaded_file = await event.message.download_media(
                    file=TELEGRAM_TEMP_DIR,
                    progress_callback=progress_callback,
                )

            if not downloaded_file:
                return False, "文件下载失败"

            target_path = os.path.join(target_dir, filename)

            if filename != "fanart.jpg":
                counter = 1
                name, ext = os.path.splitext(target_path)
                while os.path.exists(target_path):
                    target_path = f"{name}_{counter}{ext}"
                    counter += 1

            success, result = move_file(downloaded_file, target_path)

            if success:
                media_type = "other"
                if hasattr(media, "document") and hasattr(media.document, "mime_type"):
                    mime_type = media.document.mime_type
                    if mime_type:
                        if mime_type.startswith("video/"):
                            media_type = "video"
                        elif mime_type.startswith("audio/"):
                            media_type = "audio"
                        elif mime_type.startswith("image/"):
                            media_type = "photo"
                elif hasattr(media, "photo"):
                    media_type = "photo"

                logger.info(f"成功处理文件: {title}/{filename}")
                return True, {
                    "title": title,
                    "filename": filename,
                    "path": result,
                    "directory": target_dir,
                    "type": media_type,
                    "description": description,
                    "message_id": message_id
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)

    def cleanup_message_counter(self, message_id):
        """清理消息计数器"""
        if message_id in self.message_media_count:
            del self.message_media_count[message_id]
