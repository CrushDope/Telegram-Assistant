import os
import re
import logging
from datetime import datetime
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

    def _extract_title_and_intro(self, message_text):
        """提取标题和简介"""
        if not message_text or not message_text.strip():
            return None, None

        # 匹配格式：忽略【前面的内容，只取影视名字
        pattern = r'【[^】]+】(.+?)(?=\n|$)'
        match = re.search(pattern, message_text)
        title, intro = None, None

        if match:
            title = match.group(1).strip()
            title = self._sanitize_filename(title)

            # 剩余部分作为简介
            split_text = message_text.split(match.group(0), 1)
            if len(split_text) > 1:
                intro = split_text[1].strip()
        else:
            # 如果没匹配到，用第一行作为标题
            lines = message_text.strip().split("\n", 1)
            title = self._sanitize_filename(lines[0])
            if len(lines) > 1:
                intro = lines[1].strip()

        return title, intro

    def _should_download_file(self, media):
        """跳过以photo_开头的文件"""
        if hasattr(media, "document"):
            for attr in media.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    if attr.file_name.startswith("photo_"):
                        logger.info(f"跳过以photo_开头的文件: {attr.file_name}")
                        return False
        return True

    def _get_media_type_and_dir(self, media, title):
        """确定媒体类型和目标目录"""
        if hasattr(media, "document"):
            mime_type = media.document.mime_type
            if mime_type:
                if mime_type.startswith("video/"):
                    title_dir = os.path.join(TELEGRAM_VIDEOS_DIR, title)
                    os.makedirs(title_dir, exist_ok=True)
                    return "video", title_dir
                elif mime_type.startswith("audio/"):
                    return "audio", TELEGRAM_AUDIOS_DIR
            return "other", TELEGRAM_OTHERS_DIR
        elif hasattr(media, "photo"):
            title_dir = os.path.join(TELEGRAM_VIDEOS_DIR, title)
            os.makedirs(title_dir, exist_ok=True)
            return "photo", title_dir
        return "other", TELEGRAM_OTHERS_DIR

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

    def _sanitize_filename(self, filename):
        """清理文件名"""
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = filename.strip('. ')
        if len(filename) > 100:
            filename = filename[:100]
        return filename

    def _get_filename(self, media, title, is_first_photo=False):
        """获取文件名"""
        if hasattr(media, "document") and media.document.mime_type.startswith("video/"):
            ext = self._get_file_extension(media)
            return f"{title}{ext}"

        if hasattr(media, "photo"):
            if is_first_photo:
                return "fanart.jpg"
            else:
                # 尝试使用原始文件名
                if hasattr(media, "document"):
                    for attr in media.document.attributes:
                        if hasattr(attr, "file_name") and attr.file_name:
                            return attr.file_name
                return f"image_{datetime.now().strftime('%H%M%S')}.jpg"

        ext = self._get_file_extension(media)
        return f"file_{datetime.now().strftime('%H%M%S')}{ext}"

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            # 跳过 photo_ 开头的文件
            if not self._should_download_file(media):
                return False, "跳过以photo_开头的文件"

            # 提取标题和简介
            message_text = event.message.text or ""
            title, intro = self._extract_title_and_intro(message_text)
            if not title:
                title = f"media_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # 判断是否是第一张图片
            is_first_photo = False
            if hasattr(media, "photo"):
                target_dir = os.path.join(TELEGRAM_VIDEOS_DIR, title)
                fanart_path = os.path.join(target_dir, "fanart.jpg")
                is_first_photo = not os.path.exists(fanart_path)

            # 获取文件名
            filename = self._get_filename(media, title, is_first_photo)

            # 获取媒体类型和目标目录
            media_type, target_dir = self._get_media_type_and_dir(media, title)

            # 下载文件
            downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)
            if not downloaded_file:
                return False, "文件下载失败"

            target_path = os.path.join(target_dir, filename)

            # 处理重名（fanart.jpg 除外）
            if filename != "fanart.jpg":
                counter = 1
                base, ext = os.path.splitext(target_path)
                while os.path.exists(target_path):
                    target_path = f"{base}_{counter}{ext}"
                    counter += 1

            success, result = move_file(downloaded_file, target_path)

            if success:
                logger.info(f"成功处理媒体文件: {title}/{filename}")
                return True, {
                    "type": media_type,
                    "title": title,
                    "intro": intro,
                    "path": result,
                    "filename": filename,
                    "directory": target_dir
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)
