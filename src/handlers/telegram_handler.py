import os
import re
import logging
import time
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

    def _extract_title_and_intro(self, message_text: str):
        """从消息文本中提取标题（忽略【前面的内容）与简介"""
        if not message_text or not message_text.strip():
            return None, None

        # 标题匹配：忽略【前面的部分，只取影视名字（中文+英文等）
        # 例如: 🎬🔥【Dorcel啄木鸟】激情陷阱 Passion Trap (2023)
        pattern = r'【[^】]+】([^\n]+)'
        match = re.search(pattern, message_text)
        if match:
            # 标题
            title = match.group(1).strip()
            title = re.sub(r'\s+', ' ', title)
            title = self._sanitize_filename(title)

            # 简介：匹配段之后的剩余文本
            intro = message_text[match.end():].strip()
            intro = intro if intro else None
            return title, intro

        # 若未匹配到【】结构，则使用首行作为标题，后续为简介
        lines = message_text.strip().split('\n', 1)
        title = self._sanitize_filename(lines[0].strip()) if lines else None
        intro = lines[1].strip() if len(lines) > 1 else None
        return title, intro

    def _sanitize_filename(self, filename):
        """清理文件名，移除非法字符"""
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = filename.strip('. ')
        if len(filename) > 100:
            filename = filename[:100]
        return filename

    def _should_download_file(self, media):
        """判断是否应该下载该文件（跳过文件名以 photo_ 开头的）"""
        if hasattr(media, "document"):
            # 检查文件名是否以 photo_ 开头
            for attr in media.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    if attr.file_name.startswith('photo_'):
                        logger.info(f"跳过以photo_开头的文件: {attr.file_name}")
                        return False
            return True
        elif hasattr(media, "photo"):
            # Telegram photo 默认下载
            return True
        return True

    def _get_target_directory(self, title):
        """获取目标目录（基于标题创建子文件夹，视频与图片都放在此处）"""
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

    def _get_media_type(self, media):
        """获取媒体类型：video/image/audio/other"""
        if hasattr(media, "document") and hasattr(media.document, "mime_type") and media.document.mime_type:
            mt = media.document.mime_type
            if mt.startswith("video/"):
                return "video"
            if mt.startswith("image/"):
                return "image"
            if mt.startswith("audio/"):
                return "audio"
            return "other"
        if hasattr(media, "photo"):
            return "image"
        return "other"

    def _is_image_media(self, media):
        """判断是否图片（包括 photo 与 image/* 的 document）"""
        if hasattr(media, "photo"):
            return True
        if hasattr(media, "document") and hasattr(media.document, "mime_type") and media.document.mime_type:
            return media.document.mime_type.startswith("image/")
        return False

    def _get_filename(self, media, title, is_first_photo=False):
        """获取文件名"""
        # 视频：使用标题命名
        if hasattr(media, "document") and hasattr(media.document, "mime_type"):
            mime_type = media.document.mime_type or ""
            if mime_type.startswith("video/"):
                ext = self._get_file_extension(media)
                return f"{title}{ext}"

            # 图片（以 document 形式发送）
            if mime_type.startswith("image/"):
                if is_first_photo:
                    return "fanart.jpg"
                # 尝试原始文件名
                for attr in media.document.attributes:
                    if hasattr(attr, "file_name") and attr.file_name:
                        return attr.file_name
                # 若无原名，则生成
                ext = self._get_file_extension(media)
                return f"image_{datetime.now().strftime('%H%M%S')}{ext}"

        # Telegram photo
        if hasattr(media, "photo"):
            if is_first_photo:
                return "fanart.jpg"
            # Telegram photo 一般没有原始文件名，生成一个
            return f"image_{datetime.now().strftime('%H%M%S')}.jpg"

        # 其他类型
        ext = self._get_file_extension(media)
        return f"file_{datetime.now().strftime('%H%M%S')}{ext}"

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            # 过滤不应下载的文件
            if not self._should_download_file(media):
                return False, "跳过以photo_开头的文件"

            # 提取标题与简介
            message_text = event.message.text or ""
            title, intro = self._extract_title_and_intro(message_text)
            if not title:
                # 未提取到标题则使用默认
                if hasattr(media, "document"):
                    for attr in media.document.attributes:
                        if hasattr(attr, "file_name") and attr.file_name:
                            title = os.path.splitext(attr.file_name)[0]
                            break
                        elif hasattr(attr, "title") and attr.title:
                            title = attr.title
                            break
                if not title:
                    title = f"media_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                title = self._sanitize_filename(title)

            # 目录（视频与图片统一放到标题目录）
            target_dir = self._get_target_directory(title)

            # 是否第一张图片（含 photo 和 image document）
            is_first_photo = False
            if self._is_image_media(media):
                fanart_path = os.path.join(target_dir, "fanart.jpg")
                is_first_photo = not os.path.exists(fanart_path)

            # 文件名
            filename = self._get_filename(media, title, is_first_photo)

            # 下载计时
            start_time = time.time()
            downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)
            elapsed = time.time() - start_time

            if not downloaded_file:
                return False, "文件下载失败"

            logger.info(f"文件下载完成: {filename}，耗时 {elapsed:.2f} 秒")

            # 目标路径
            target_path = os.path.join(target_dir, filename)

            # 处理重名（fanart.jpg 允许覆盖）
            if filename != "fanart.jpg":
                counter = 1
                name, ext = os.path.splitext(target_path)
                while os.path.exists(target_path):
                    target_path = f"{name}_{counter}{ext}"
                    counter += 1

            # 移动文件到目标目录
            success, result = move_file(downloaded_file, target_path)
            media_type = self._get_media_type(media)

            if success:
                logger.info(f"成功处理文件: {title}/{filename}")
                return True, {
                    "type": media_type,
                    "title": title,
                    "intro": intro,
                    "filename": filename,
                    "path": result,
                    "directory": target_dir,
                    "elapsed": elapsed,
                    "is_video": (media_type == "video"),
                    "is_first_photo": is_first_photo,
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)
