import os
import re
import logging
import shutil
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

    def _sanitize_filename(self, filename):
        """清理文件名中的非法字符"""
        # 移除Windows和Linux中的非法文件名字符
        illegal_chars = r'[<>:"/\\|?*\x00-\x1f]'
        filename = re.sub(illegal_chars, '', filename)
        # 移除开头和结尾的点号和空格
        filename = filename.strip('. ')
        # 限制文件名长度
        if len(filename) > 200:
            filename = filename[:200]
        return filename

    def _extract_title(self, message_text):
        """从消息文本中提取标题"""
        if not message_text:
            return "无标题媒体组"
            
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
            title = self._sanitize_filename(title)
            return title
        
        # 如果没有找到【】格式的标题，返回原始文本的第一行（直到换行符）
        first_line = message_text.split('\n')[0].strip()
        # 移除可能的标签部分（以#开头的内容）
        first_line = re.sub(r'#.*$', '', first_line).strip()
        # 清理非法字符
        first_line = self._sanitize_filename(first_line)
        
        return first_line if first_line else "无标题媒体组"

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

            # 如果没有找到文件名，使用MIME类型生成
            if hasattr(media.document, "mime_type"):
                ext = media.document.mime_type.split("/")[-1]
                return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"

        elif hasattr(media, "photo"):
            return f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

        # 从消息文本中提取标题作为文件名
        title = self._extract_title(message_text)
        if title:
            return title
            
        return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    async def process_media(self, event):
        """处理Telegram媒体消息"""
        try:
            media = event.message.media
            if not media:
                return False, "没有检测到媒体文件"

            # 获取媒体类型和目标目录
            media_type, target_dir = self._get_media_type_and_dir(media)

            # 获取文件名
            filename = self._get_filename(media, event.message.message)
            
            # 如果文件名是时间戳格式或者不包含中文，但消息文本中有中文，使用提取的标题
            if (not re.search("[\u4e00-\u9fff]+", filename) or 
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}" in filename) and re.search(r"[\u4e00-\u9fff]+", event.message.message):
                extracted_title = self._extract_title(event.message.message)
                if extracted_title:
                    filename = extracted_title

            # 下载文件
            downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)

            if not downloaded_file:
                return False, "文件下载失败"

            # 移动文件到目标目录
            ext = os.path.splitext(downloaded_file)[1]
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

    async def process_media_group(self, group_id, media_files, caption):
        """处理媒体组文件"""
        try:
            logger.info(f"开始处理媒体组 {group_id}, 包含 {len(media_files)} 个文件")
            
            # 统计文件信息
            total_files = len(media_files)
            photo_count = sum(1 for f in media_files if f['type'] == 'photo')
            video_count = sum(1 for f in media_files if f['type'] == 'video')
            other_count = total_files - photo_count - video_count
            
            logger.info(f"媒体组 {group_id} 统计: {total_files}个文件, {photo_count}张图片, {video_count}个视频, {other_count}个其他文件")
            
            # 从标题中提取目录名
            directory_name = self._extract_title(caption)
            
            # 创建媒体组专属目录（在TELEGRAM_VIDEOS_DIR下）
            group_dir = os.path.join(TELEGRAM_VIDEOS_DIR, directory_name)
            os.makedirs(group_dir, exist_ok=True)
            
            # 分离图片、视频和其他文件
            photos = [f for f in media_files if f['type'] == 'photo']
            videos = [f for f in media_files if f['type'] == 'video']
            others = [f for f in media_files if f['type'] == 'other']
            
            # 处理图片命名
            photo_renames = {}
            for i, photo in enumerate(photos):
                temp_path = photo['temp_path']
                
                if i == 0:  # 第一张图片命名为fanart.jpg
                    new_filename = "fanart.jpg"
                else:  # 其他图片命名为snapshot.jpg
                    new_filename = f"snapshot{i}.jpg"
                
                target_path = os.path.join(group_dir, new_filename)
                
                # 移动文件
                shutil.move(temp_path, target_path)
                photo_renames[photo['original_filename']] = new_filename
                logger.info(f"图片重命名: {photo['original_filename']} -> {new_filename}")
            
            # 处理视频命名（使用提取的标题）
            video_names = {}
            for i, video in enumerate(videos):
                temp_path = video['temp_path']
                original_ext = os.path.splitext(video['original_filename'])[1]
                
                if video_count == 1:
                    # 如果只有一个视频，直接使用目录名
                    new_filename = f"{directory_name}{original_ext}"
                else:
                    # 如果有多个视频，添加序号
                    new_filename = f"{directory_name}_{i+1}{original_ext}"
                
                # 清理文件名中的非法字符
                new_filename = self._sanitize_filename(new_filename)
                
                target_path = os.path.join(group_dir, new_filename)
                
                # 移动文件
                shutil.move(temp_path, target_path)
                video_names[video['original_filename']] = new_filename
                logger.info(f"视频重命名: {video['original_filename']} -> {new_filename}")
            
            # 处理其他文件（保持原有名称）
            other_names = {}
            for other in others:
                temp_path = other['temp_path']
                original_filename = other['original_filename']
                
                target_path = os.path.join(group_dir, original_filename)
                
                # 移动文件
                shutil.move(temp_path, target_path)
                other_names[original_filename] = original_filename
                logger.info(f"其他文件保持原名: {original_filename}")
            
            # 构建详细的统计信息
            group_info = {
                'group_id': group_id,
                'caption': caption,
                'directory': group_dir,
                'directory_name': directory_name,
                'total_files': total_files,
                'photo_count': photo_count,
                'video_count': video_count,
                'other_count': other_count,
                'photo_renames': photo_renames,
                'video_names': list(video_names.values()),
                'other_names': list(other_names.keys()),
                'processed_at': datetime.now().isoformat(),
                'file_list': [
                    {
                        'original_name': f['original_filename'],
                        'final_name': photo_renames.get(f['original_filename'], 
                                      video_names.get(f['original_filename'], 
                                      other_names.get(f['original_filename'], f['original_filename']))),
                        'type': f['type']
                    }
                    for f in media_files
                ]
            }
            
            logger.info(f"媒体组 {group_id} 处理完成: {group_info}")
            return True, group_info
            
        except Exception as e:
            logger.error(f"处理媒体组时出错: {str(e)}")
            # 清理临时文件
            for media_file in media_files:
                if os.path.exists(media_file['temp_path']):
                    try:
                        os.remove(media_file['temp_path'])
                    except:
                        pass
            return False, str(e)
