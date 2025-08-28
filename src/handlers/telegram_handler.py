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
        # 用于跟踪每个消息的图片计数
        self.photo_counters = {}

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

    def _extract_title_from_message(self, message_text):
        """从消息文本中提取标题和简介"""
        if not message_text or not message_text.strip():
            return None, None
        
        # 匹配标题格式：两个表情 + 【品牌】+ 影视名字（中文+英文）
        # 例如: 🎬🔥【Dorcel啄木鸟】激情陷阱 Passion Trap (2023)
        title_pattern = r'[�-🯿]{2}【([^】]+)】(.+?)(?=\n|$)'
        match = re.search(title_pattern, message_text)
        
        if match:
            brand = match.group(1)  # 品牌部分，如：Dorcel啄木鸟
            title_content = match.group(2).strip()  # 影视标题部分
            
            # 提取简介（标题后的所有内容）
            description_start = match.end()
            description = message_text[description_start:].strip()
            
            # 清理标题内容（移除可能的多余空格和换行）
            title_content = re.sub(r'\s+', ' ', title_content)
            
            return title_content, description
        
        # 如果没有匹配到标准格式，尝试其他可能的格式
        lines = message_text.strip().split('\n')
        if lines:
            # 取第一行作为标题，剩余作为简介
            title_content = lines[0].strip()
            description = '\n'.join(lines[1:]).strip() if len(lines) > 1 else ""
            return title_content, description
        
        return None, None

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

    def _get_filename_for_media(self, media, title, message_id, is_video=False):
        """根据媒体类型获取文件名"""
        if is_video:
            # 视频文件使用提取的标题命名
            file_extension = self._get_file_extension(media)
            return f"{title}{file_extension}"
        
        elif hasattr(media, "photo"):
            # 图片文件处理
            if message_id not in self.photo_counters:
                self.photo_counters[message_id] = 0
            
            self.photo_counters[message_id] += 1
            photo_count = self.photo_counters[message_id]
            
            if photo_count == 1:
                # 第一张图片命名为fanart.jpg
                return "fanart.jpg"
            else:
                # 其他图片保持原始文件名或使用默认命名
                file_extension = self._get_file_extension(media)
                # 尝试获取原始文件名
                original_name = None
                if hasattr(media, "document"):
                    for attr in media.document.attributes:
                        if hasattr(attr, "file_name") and attr.file_name:
                            original_name = attr.file_name
                            break
                
                if original_name:
                    return original_name
                else:
                    return f"image_{photo_count}{file_extension}"
        
        # 其他类型文件
        file_extension = self._get_file_extension(media)
        return f"file_{datetime.now().strftime('%H%M%S')}{file_extension}"

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            message_id = event.message.id
            message_text = event.message.text or ""

            # 提取标题和简介
            title, description = self._extract_title_from_message(message_text)
            
            if not title:
                # 如果没有提取到标题，使用备用方案
                logger.warning(f"消息 {message_id} 中未提取到标题，使用备用命名")
                if hasattr(media, "document"):
                    for attr in media.document.attributes:
                        if hasattr(attr, "file_name") and attr.file_name:
                            title = os.path.splitext(attr.file_name)[0]
                            break
                        elif hasattr(attr, "title") and attr.title:
                            title = attr.title
                            break
                
                if not title:
                    media_type = "video" if hasattr(media, "document") and media.document.mime_type.startswith("video/") else "photo"
                    title = f"{media_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # 清理标题
            title = self._sanitize_filename(title)

            # 判断是否是视频
            is_video = hasattr(media, "document") and hasattr(media.document, "mime_type") and media.document.mime_type.startswith("video/")

            # 获取文件名
            filename = self._get_filename_for_media(media, title, message_id, is_video)

            # 获取媒体类型和目标目录
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
                    "description": description,
                    "directory": target_dir,
                    "message_id": message_id
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)

    def cleanup_message_counter(self, message_id):
        """清理消息计数器"""
        if message_id in self.photo_counters:
            del self.photo_counters[message_id]
