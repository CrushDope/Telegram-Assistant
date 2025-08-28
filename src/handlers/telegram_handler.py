import os
import re
import time
import logging
from datetime import datetime
from ..utils.file_utils import move_file
from ..constants import (
    TELEGRAM_TEMP_DIR,
    TELEGRAM_VIDEOS_DIR,
    TELEGRAM_AUDIOS_DIR,   # 保留，不直接使用
    TELEGRAM_PHOTOS_DIR,   # 保留，不直接使用
    TELEGRAM_OTHERS_DIR,   # 保留，不直接使用
    DOUYIN_DEST_DIR,       # 保留，不直接使用
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

    def _extract_title_and_intro(self, message_text: str):
        """
        从消息文本中提取 标题 和 简介：
        - 忽略【 前面的内容，只取 】后面的影视名字作为标题
        - 简介为标题后的剩余文本（可多行）
        """
        if not message_text or not message_text.strip():
            return None, None

        pattern = r'【[^】]+】(.+?)(?=\n|$)'
        match = re.search(pattern, message_text)
        title, intro = None, None

        if match:
            # 标题只取 】 后面的内容
            title = match.group(1).strip()
            title = re.sub(r'\s+', ' ', title)
            title = self._sanitize_filename(title)

            # 简介：匹配段之后的剩余内容
            split_text = message_text.split(match.group(0), 1)
            if len(split_text) > 1:
                intro = split_text[1].strip()
        else:
            # 兜底：第一行作为标题，其余为简介
            lines = message_text.strip().split('\n', 1)
            title = self._sanitize_filename(lines[0].strip())
            if len(lines) > 1:
                intro = lines[1].strip()

        return title, intro

    def _sanitize_filename(self, filename):
        """清理文件名，移除非法字符"""
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = filename.strip('. ')
        if len(filename) > 100:
            filename = filename[:100]
        return filename

    def _should_download_file(self, media):
        """判断是否应该下载该文件：跳过以 photo_ 开头的文档文件"""
        if hasattr(media, "document"):
            for attr in getattr(media.document, "attributes", []):
                file_name = getattr(attr, "file_name", None)
                if file_name and file_name.startswith('photo_'):
                    logger.info(f"跳过以photo_开头的文件: {file_name}")
                    return False
        # 其余情况默认下载（photo 类型没有文件名，不作跳过）
        return True

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

    def _is_video(self, media):
        return hasattr(media, "document") and getattr(media.document, "mime_type", "") and media.document.mime_type.startswith("video/")

    def _is_audio(self, media):
        return hasattr(media, "document") and getattr(media.document, "mime_type", "") and media.document.mime_type.startswith("audio/")

    def _is_image(self, media):
        if hasattr(media, "photo"):
            return True
        if hasattr(media, "document") and getattr(media.document, "mime_type", ""):
            return media.document.mime_type.startswith("image/")
        return False

    def _get_file_type_str(self, media):
        if self._is_video(media):
            return "video"
        if self._is_audio(media):
            return "audio"
        if self._is_image(media):
            return "photo"
        return "other"

    def _get_target_directory(self, title):
        """
        获取目标目录（基于标题创建子文件夹）
        要求：视频和图片都放在以标题命名的子目录下
        """
        target_dir = os.path.join(TELEGRAM_VIDEOS_DIR, title)
        os.makedirs(target_dir, exist_ok=True)
        return target_dir

    def _get_original_filename_from_document(self, media):
        """从 Document 的 attributes 中尽力获取原始文件名"""
        if hasattr(media, "document"):
            for attr in getattr(media.document, "attributes", []):
                file_name = getattr(attr, "file_name", None)
                if file_name:
                    return file_name
        return None

    def _get_filename(self, media, title, is_first_photo=False):
        """根据规则获取文件名"""
        # 视频：使用标题命名
        if self._is_video(media):
            ext = self._get_file_extension(media)
            return f"{title}{ext}"

        # 图片（包括 photo 和 document 的 image/*）
        if self._is_image(media):
            if is_first_photo:
                return "fanart.jpg"
            # 非第一张：尽量保留原名（仅 document 有原名）
            original = self._get_original_filename_from_document(media)
            if original:
                return original
            # photo 没有原名，用时间戳兜底
            return f"image_{datetime.now().strftime('%H%M%S')}.jpg"

        # 其他：时间戳 + 扩展名
        ext = self._get_file_extension(media)
        return f"file_{datetime.now().strftime('%H%M%S')}{ext}"

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            # 跳过不需要的文件
            if not self._should_download_file(media):
                return False, "跳过以photo_开头的文件"

            # 提取 标题 & 简介
            message_text = event.message.text or ""
            title, intro = self._extract_title_and_intro(message_text)
            if not title:
                # 兜底：尝试从 document 获取
                if hasattr(media, "document"):
                    original = self._get_original_filename_from_document(media)
                    if original:
                        title = os.path.splitext(original)[0]
                if not title:
                    title = f"media_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                title = self._sanitize_filename(title)

            # 创建目标目录
            target_dir = self._get_target_directory(title)

            # 判断是否是第一张图片 → fanart.jpg
            is_first_photo = False
            if self._is_image(media):
                fanart_path = os.path.join(target_dir, "fanart.jpg")
                is_first_photo = not os.path.exists(fanart_path)

            # 获取文件名
            filename = self._get_filename(media, title, is_first_photo)

            # 下载并计时
            start_time = time.time()
            downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)
            elapsed = time.time() - start_time
            if not downloaded_file:
                return False, "文件下载失败"

            logger.info(f"文件下载完成: {filename}，耗时 {elapsed:.2f} 秒")

            # 目标路径 & 重名处理（fanart.jpg 允许覆盖）
            target_path = os.path.join(target_dir, filename)
            if filename != "fanart.jpg":
                counter = 1
                name, ext = os.path.splitext(target_path)
                while os.path.exists(target_path):
                    target_path = f"{name}_{counter}{ext}"
                    counter += 1

            # 移动文件到目标目录
            success, result = move_file(downloaded_file, target_path)

            if success:
                file_type = self._get_file_type_str(media)
                logger.info(f"成功处理文件: {title}/{os.path.basename(target_path)}")
                return True, {
                    "type": file_type,            # 'video' | 'photo' | 'audio' | 'other'
                    "title": title,
                    "intro": intro,
                    "filename": os.path.basename(target_path),
                    "path": result,
                    "directory": target_dir,
                    "elapsed": elapsed,           # 下载耗时（秒）
                    "is_first_photo": is_first_photo
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)
