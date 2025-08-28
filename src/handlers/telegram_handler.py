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

    def _get_media_type_and_dir(self, media, title):
        """确定媒体类型和目标目录"""
        if hasattr(media, "document"):
            mime_type = media.document.mime_type
            if mime_type:
                if mime_type.startswith("video/"):
                    # 视频文件放在以标题命名的子目录中
                    title_dir = os.path.join(TELEGRAM_VIDEOS_DIR, title)
                    os.makedirs(title_dir, exist_ok=True)
                    return "video", title_dir
                elif mime_type.startswith("audio/"):
                    return "audio", TELEGRAM_AUDIOS_DIR
            return "other", TELEGRAM_OTHERS_DIR
        elif hasattr(media, "photo"):
            # 图片文件也放在以标题命名的子目录中
            title_dir = os.path.join(TELEGRAM_VIDEOS_DIR, title)
            os.makedirs(title_dir, exist_ok=True)
            return "photo", title_dir
        return "other", TELEGRAM_OTHERS_DIR

    def _get_file_extension(self, media):
        """获取文件扩展名"""
        if hasattr(media, "document") and hasattr(media.document, "mime_type"):
            mime_type = media.document.mime_type
            if mime_type:
                # 常见MIME类型映射
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
        """清理文件名，移除非法字符"""
        # 移除文件系统非法字符
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # 移除开头和结尾的点号和空格
        filename = filename.strip('. ')
        # 限制文件名长度
        if len(filename) > 100:
            filename = filename[:100]
        return filename

    def _get_title_from_message(self, message):
        """从消息中提取标题"""
        title = None

        if message and message.strip():
            # 尝试提取标题：通常是第一行或前100个字符
            lines = message.strip().split('\n')
            if lines:
                title = lines[0].strip()
                # 如果标题太长，截取合理长度
                if len(title) > 50:
                    title = title[:50] + "..."

        return title

    def _get_filename(self, media, message, is_first_photo=False):
        """获取文件名，优先使用消息标题"""
        # 优先从消息文本中提取标题
        title = self._get_title_from_message(message)

        # 如果没有标题，尝试从文档属性中获取
        if not title and hasattr(media, "document"):
            for attr in media.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    # 使用文件名（不含扩展名）作为标题
                    title = os.path.splitext(attr.file_name)[0]
                    break
                elif hasattr(attr, "title") and attr.title:
                    title = attr.title
                    break

        # 如果还是没有标题，使用默认命名
        if not title:
            media_type = "video" if hasattr(media, "document") and media.document.mime_type.startswith(
                "video/") else "photo"
            title = f"{media_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 清理标题
        title = self._sanitize_filename(title)

        # 如果是第一张图片，命名为fanart.jpg
        if is_first_photo and hasattr(media, "photo"):
            return title, "fanart.jpg"

        # 获取文件扩展名
        file_extension = self._get_file_extension(media)

        return title, f"{title}{file_extension}"

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            # 检查是否是第一张图片（用于fanart命名）
            is_first_photo = False
            if hasattr(media, "photo"):
                # 这里可以添加逻辑来判断是否是第一张图片
                # 例如通过消息ID或其他标识，暂时简单处理
                is_first_photo = True

            # 获取标题和文件名
            title, filename = self._get_filename(media, event.message.text, is_first_photo)

            # 获取媒体类型和目标目录（传入标题用于创建子目录）
            media_type, target_dir = self._get_media_type_and_dir(media, title)

            # 下载文件
            downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)

            if not downloaded_file:
                return False, "文件下载失败"

            # 构建目标路径
            target_path = os.path.join(target_dir, filename)

            # 处理文件重名（除了fanart.jpg）
            if filename != "fanart.jpg":
                counter = 1
                original_target_path = target_path
                name, ext = os.path.splitext(original_target_path)
                while os.path.exists(target_path):
                    target_path = f"{name}_{counter}{ext}"
                    counter += 1

            # 移动文件到目标目录
            success, result = move_file(downloaded_file, target_path)

            if success:
                logger.info(f"成功处理媒体文件: {title}/{filename}")
                return True, {
                    "type": media_type,
                    "path": result,
                    "filename": filename,
                    "title": title,
                    "directory": target_dir
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)
