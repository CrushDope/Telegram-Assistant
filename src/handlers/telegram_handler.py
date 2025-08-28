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

    def _extract_title(self, message_text):
        """从消息文本中提取标题"""
        if not message_text or not message_text.strip():
            return None
        
        # 匹配格式：任意内容【品牌】标题内容
        # 例如: 🎬🔥【Dorcel啄木鸟】激情陷阱 Passion Trap (2023)
        title_pattern = r'.*?【([^】]+)】(.+?)(?=\n|$)'
        match = re.search(title_pattern, message_text)
        
        if match:
            title_content = match.group(2).strip()  # 提取标题部分
            # 清理标题内容
            title_content = re.sub(r'\s+', ' ', title_content)
            return self._sanitize_filename(title_content)
        
        # 如果没有匹配到标准格式，使用第一行作为标题
        lines = message_text.strip().split('\n')
        if lines:
            title_content = lines[0].strip()
            return self._sanitize_filename(title_content)
        
        return None

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

    def _should_download_file(self, media):
        """判断是否应该下载该文件"""
        if hasattr(media, "document"):
            # 检查文件名是否以photo_开头
            for attr in media.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    if attr.file_name.startswith('photo_'):
                        logger.info(f"跳过以photo_开头的文件: {attr.file_name}")
                        return False
            return True
        elif hasattr(media, "photo"):
            # 图片默认都下载
            return True
        return True

    def _get_target_directory(self, title):
        """获取目标目录（基于标题创建子文件夹）"""
        target_dir = os.path.join(TELEGRAM_VIDEOS_DIR, title)
        os.makedirs(target_dir, exist_ok=True)
        return target_dir

    def _get_filename(self, media, title, is_first_photo=False):
        """获取文件名"""
        if hasattr(media, "document") and hasattr(media.document, "mime_type"):
            mime_type = media.document.mime_type
            if mime_type and mime_type.startswith("video/"):
                # 视频文件使用标题命名
                ext = self._get_file_extension(media)
                return f"{title}{ext}"
        
        elif hasattr(media, "photo"):
            # 图片文件：第一张命名为fanart.jpg，其他保持原名
            if is_first_photo:
                return "fanart.jpg"
            else:
                # 尝试获取原始文件名
                if hasattr(media, "document"):
                    for attr in media.document.attributes:
                        if hasattr(attr, "file_name") and attr.file_name:
                            return attr.file_name
        
        # 默认命名
        ext = self._get_file_extension(media)
        return f"file_{datetime.now().strftime('%H%M%S')}{ext}"

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

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            # 检查是否应该下载该文件
            if not self._should_download_file(media):
                return False, "跳过以photo_开头的文件"

            # 提取标题
            message_text = event.message.text or ""
            title = self._extract_title(message_text)
            
            if not title:
                # 备用标题生成
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

            # 创建目标目录
            target_dir = self._get_target_directory(title)

            # 判断是否是第一张图片
            is_first_photo = False
            if hasattr(media, "photo"):
                # 检查目录中是否已经有fanart.jpg
                fanart_path = os.path.join(target_dir, "fanart.jpg")
                is_first_photo = not os.path.exists(fanart_path)

            # 获取文件名
            filename = self._get_filename(media, title, is_first_photo)

            # 下载文件
            downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)
            if not downloaded_file:
                return False, "文件下载失败"

            # 构建目标路径
            target_path = os.path.join(target_dir, filename)
            
            # 处理文件重名
            if filename != "fanart.jpg":  # fanart.jpg允许覆盖
                counter = 1
                name, ext = os.path.splitext(target_path)
                while os.path.exists(target_path):
                    target_path = f"{name}_{counter}{ext}"
                    counter += 1

            # 移动文件到目标目录
            success, result = move_file(downloaded_file, target_path)

            if success:
                logger.info(f"成功处理文件: {title}/{filename}")
                return True, {
                    "title": title,
                    "filename": filename,
                    "path": result,
                    "directory": target_dir,
                    "is_video": hasattr(media, "document") and 
                               hasattr(media.document, "mime_type") and 
                               media.document.mime_type.startswith("video/"),
                    "is_first_photo": is_first_photo
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)
