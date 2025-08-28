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
        # 用于跟踪每个标题目录中的图片数量
        self.image_counters = {}
        # 用于存储最近提取的标题，以便后续消息使用
        self.last_title = None

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
        if not message_text:
            return self.last_title or ""  # 如果没有文本，返回最近使用的标题
            
        # 尝试匹配【】中的内容
        pattern = r"【(.*?)】"
        match = re.search(pattern, message_text)
        
        if match:
            # 找到【】中的内容
            title_part = match.group(1)
            
            # 获取【】后面的内容直到换行或结束
            rest_of_text = message_text[match.end():].strip()
            
            # 如果后面有内容，取直到换行符或#标签之前的部分
            if rest_of_text:
                # 找到第一个换行符或#标签的位置
                end_match = re.search(r"[\n#]", rest_of_text)
                if end_match:
                    rest_part = rest_of_text[:end_match.start()].strip()
                else:
                    rest_part = rest_of_text
                
                # 组合标题
                title = f"【{title_part}】{rest_part}"
            else:
                title = f"【{title_part}】"
            
            # 清理标题中的非法文件名字符
            title = re.sub(r'[<>:"/\\|?*]', '', title)
            self.last_title = title  # 保存最近使用的标题
            return title
        
        # 如果没有找到【】格式的标题，返回原始文本的第一行（直到换行符）
        first_line = message_text.split('\n')[0].strip()
        # 移除可能的标签部分（以#开头的内容）
        first_line = re.sub(r'#.*$', '', first_line).strip()
        # 清理非法字符
        first_line = re.sub(r'[<>:"/\\|?*]', '', first_line)
        
        if first_line:
            self.last_title = first_line  # 保存最近使用的标题
            return first_line
        
        return self.last_title or ""  # 如果没有提取到标题，返回最近使用的标题

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
            return "photo", TELEGRAM_VIDEOS_DIR  # 图片也存到视频目录的子目录中
        return "other", TELEGRAM_OTHERS_DIR

    def _get_filename(self, media, message_text=""):
        """获取文件名"""
        title = self._extract_title(message_text)
        
        if hasattr(media, "document"):
            mime_type = getattr(media.document, "mime_type", "")
            if mime_type and mime_type.startswith("video/"):
                # 视频文件使用标题作为文件名
                return title if title else f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            for attr in media.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    return attr.file_name
                elif hasattr(attr, "title") and attr.title:
                    return f"{attr.title}.{media.document.mime_type.split('/')[-1]}"

            # 如果没有找到文件名，使用MIME类型生成
            if hasattr(media.document, "mime_type"):
                ext = media.document.mime_type.split("/")[-1]
                return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"

        elif hasattr(media, "photo"):
            # 图片文件使用fanart.jpg, fanart1.jpg等命名
            # 使用当前标题作为键
            if title not in self.image_counters:
                self.image_counters[title] = 0
            else:
                self.image_counters[title] += 1
                
            if self.image_counters[title] == 0:
                return "fanart"
            else:
                return f"fanart{self.image_counters[title]}"

        return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            # 获取媒体类型和目标目录
            media_type, base_target_dir = self._get_media_type_and_dir(media)

            # 提取标题作为子目录名
            title = self._extract_title(event.message.text if event.message.text else "")
            if not title:
                title = f"untitled_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # 创建子目录
            target_dir = os.path.join(base_target_dir, title)
            os.makedirs(target_dir, exist_ok=True)

            # 获取文件名
            filename = self._get_filename(media, event.message.text if event.message.text else "")
            
            # 如果文件名是时间戳格式或者不包含中文，但消息文本中有中文，使用提取的标题
            message_text = event.message.text if event.message.text else ""
            if (not re.search("[\u4e00-\u9fff]+", filename) or 
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}" in filename) and re.search(r"[\u4e00-\u9fff]+", message_text):
                extracted_title = self._extract_title(message_text)
                if extracted_title:
                    filename = extracted_title

            # 下载文件
            downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)

            if not downloaded_file:
                return False, "文件下载失败"

            # 获取文件扩展名
            ext = os.path.splitext(downloaded_file)[1]
            
            # 对于图片文件，确保使用正确的扩展名
            if hasattr(media, "photo"):
                ext = ".jpg"
            
            # 构建目标路径
            target_path = os.path.join(target_dir, f"{filename}{ext}")
            
            # 处理可能的重复文件
            counter = 1
            original_target_path = target_path
            while os.path.exists(target_path):
                target_path = os.path.join(
                    target_dir, 
                    f"{filename}_{counter}{ext}"
                )
                counter += 1

            # 移动文件到目标目录
            success, result = move_file(downloaded_file, target_path)

            if success:
                return True, {
                    "type": media_type,
                    "path": result,
                    "filename": os.path.basename(result),
                    "subdir": title
                }
            else:
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.error(f"处理Telegram媒体文件时出错: {str(e)}")
            return False, str(e)
