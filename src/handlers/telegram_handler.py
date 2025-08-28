import os
import re
import logging
from datetime import datetime
from telethon.tl.types import Document, MessageMediaPhoto
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

    def _get_media_type_and_dir(self, media):
        """确定媒体类型和目标目录"""
        if hasattr(media, "document"):
            mime_type = media.document.mime_type
            if mime_type:
                if mime_type.startswith("video/"):
                    return "video", TELEGRAM_VIDEOS_DIR
                elif mime_type.startswith("audio/"):
                    return "audio", TELEGRAM_AUDIOS_DIR
            return "other", TELEGRAM_OTHERS_DIR
        elif hasattr(media, "photo"):
            return "photo", TELEGRAM_PHOTOS_DIR
        return "other", TELEGRAM_OTHERS_DIR

    def _get_filename(self, media, message_text=""):
        """获取文件名"""
        if hasattr(media, "document"):
            for attr in media.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    return attr.file_name
                elif hasattr(attr, "title") and attr.title:
                    return f"{attr.title}.{media.document.mime_type.split('/')[-1]}"

            if hasattr(media.document, "mime_type"):
                ext = media.document.mime_type.split("/")[-1]
                return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"

        elif hasattr(media, "photo"):
            return f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

        return message_text or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _extract_title(self, message_text: str) -> str:
        """提取转发消息中的标题文本"""
        if not message_text:
            return None
        first_line = message_text.strip().split("\n")[0]
        return re.sub(r'[\\/:*?"<>|]', "_", first_line).strip()

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            # 获取媒体类型和基本目标目录
            media_type, target_dir = self._get_media_type_and_dir(media)

            # 提取消息标题并作为子目录
            title = self._extract_title(event.message.text)
            if title:
                target_dir = os.path.join(target_dir, title)
                os.makedirs(target_dir, exist_ok=True)

            # 获取文件名
            filename = self._get_filename(media, event.message.message)

            if (
                not re.search("[\u4e00-\u9fff]+", filename)
                or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}" in filename
            ) and re.search(r"[\u4e00-\u9fff]+", event.message.message):
                filename = event.message.message

            # -----------------------------
            # 下载文件
            # -----------------------------
            ext = None
            downloaded_file = None

            if hasattr(media, "document") and isinstance(media.document, Document):
                document: Document = media.document

                # 获取扩展名
                for attr in document.attributes:
                    if hasattr(attr, "file_name") and "." in attr.file_name:
                        ext = os.path.splitext(attr.file_name)[1]
                        break
                if not ext and media.document.mime_type:
                    ext = "." + media.document.mime_type.split("/")[-1]

                temp_file = os.path.join(
                    TELEGRAM_TEMP_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.tmp"
                )

                # ✅ 使用并发分块下载视频/大文件
                await event.client.download_file(
                    document,
                    file=temp_file,
                    request_size=1024 * 1024 * 2,  # 2MB per chunk
                    parallel=4,  # 4 个并发
                )
                downloaded_file = temp_file

            elif isinstance(media, MessageMediaPhoto):
                ext = ".jpg"
                downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)

            else:
                downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)
                ext = os.path.splitext(downloaded_file)[1]

            if not downloaded_file:
                return False, "文件下载失败"

            # -----------------------------
            # 移动文件到目标目录
            # -----------------------------
            target_path = os.path.join(target_dir, f"{filename}{ext}")
            target_path = target_path.replace(".x-flac", "").replace(".mp4.m4a", ".m4a")

            if os.path.exists(target_path):
                filename = f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                target_path = os.path.join(target_dir, filename)

            target_path = target_path.replace(ext + ext, ext)

            success, result = move_file(downloaded_file, target_path)

            if success:
                return True, {
                    "type": media_type,
                    "path": result,
                    "filename": os.path.basename(result),
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)
