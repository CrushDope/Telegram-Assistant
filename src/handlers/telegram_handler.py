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
)

logger = logging.getLogger(__name__)


MIME_TO_EXT = {
    "video/mp4": ".mp4",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-flac": ".flac",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


class TelegramHandler:
    def __init__(self, config):
        self.config = config

    def _sanitize_filename(self, filename):
        illegal_chars = r'[<>:"/\\|?*\x00-\x1f]'
        filename = re.sub(illegal_chars, '', filename)
        filename = filename.strip('. ')
        if len(filename) > 200:
            filename = filename[:200]
        return filename

    def _extract_title(self, message_text):
        if not message_text:
            return "无标题媒体组"

        pattern = r"【(.*?)】"
        match = re.search(pattern, message_text)

        if match:
            title_part = match.group(1)
            rest_of_text = message_text[match.end():].strip()
            if rest_of_text:
                end_match = re.search(r"[\n#]", rest_of_text)
                rest_part = rest_of_text[:end_match.start()].strip() if end_match else rest_of_text
                title = f"【{title_part}】{rest_part}"
            else:
                title = f"【{title_part}】"
            return self._sanitize_filename(title)

        first_line = message_text.split('\n')[0].strip()
        first_line = re.sub(r'#.*$', '', first_line).strip()
        first_line = self._sanitize_filename(first_line)
        return first_line if first_line else "无标题媒体组"

    def _get_media_type_and_dir(self, media):
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

    def _get_ext_from_mime(self, mime_type):
        if not mime_type:
            return ""
        return MIME_TO_EXT.get(mime_type, f".{mime_type.split('/')[-1]}")

    async def process_media(self, event):
        """处理单条 Telegram 媒体消息"""
        msg_id = event.message.id
        chat_id = event.chat_id
        logger.info(f"[process_media] 开始处理 chat={chat_id} msg={msg_id}")

        try:
            media = event.message.media
            if not media:
                logger.warning(f"[process_media] msg={msg_id} 没有媒体内容")
                return False, "没有检测到媒体文件"

            media_type, target_dir = self._get_media_type_and_dir(media)
            logger.debug(f"[process_media] msg={msg_id} 媒体类型={media_type} 目标目录={target_dir}")

            logger.info(f"[process_media] msg={msg_id} 开始下载到临时目录 {TELEGRAM_TEMP_DIR}")
            downloaded_file = await event.message.download_media(file=TELEGRAM_TEMP_DIR)
            if not downloaded_file:
                logger.error(f"[process_media] msg={msg_id} 下载失败，download_media 返回 None")
                return False, "文件下载失败"
            logger.info(f"[process_media] msg={msg_id} 下载完成: {downloaded_file}")

            temp_stem, temp_ext = os.path.splitext(os.path.basename(downloaded_file))
            logger.debug(f"[process_media] msg={msg_id} 临时文件名={temp_stem} 临时扩展名={temp_ext}")

            if hasattr(media, "document"):
                mime_type = media.document.mime_type
                ext = self._get_ext_from_mime(mime_type) or temp_ext
                logger.debug(f"[process_media] msg={msg_id} mime_type={mime_type} 最终扩展名={ext}")
            else:
                ext = ".jpg"

            title = self._extract_title(event.message.message) if event.message.message else None
            if title and title != "无标题媒体组":
                filename = title
                logger.debug(f"[process_media] msg={msg_id} 使用消息标题作为文件名: {filename}")
            else:
                filename = temp_stem
                logger.debug(f"[process_media] msg={msg_id} 使用原始文件名: {filename}")

            target_path = os.path.join(target_dir, f"{filename}{ext}")
            if os.path.exists(target_path):
                new_target = os.path.join(
                    target_dir, f"{filename}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
                )
                logger.warning(f"[process_media] msg={msg_id} 目标文件已存在，重命名为: {os.path.basename(new_target)}")
                target_path = new_target

            logger.info(f"[process_media] msg={msg_id} 移动文件: {downloaded_file} -> {target_path}")
            success, result = move_file(downloaded_file, target_path)

            if success:
                logger.info(f"[process_media] msg={msg_id} 处理完成: {result}")
                return True, {
                    "type": media_type,
                    "path": result,
                    "filename": os.path.basename(result),
                }
            else:
                logger.error(f"[process_media] msg={msg_id} 移动文件失败: {result}")
                return False, f"移动文件失败: {result}"

        except Exception as e:
            logger.exception(f"[process_media] msg={msg_id} 处理异常: {e}")
            return False, str(e)

    async def process_media_group(self, group_id, media_files, caption):
        """处理媒体组文件"""
        total_files = len(media_files)
        photo_count = sum(1 for f in media_files if f['type'] == 'photo')
        video_count = sum(1 for f in media_files if f['type'] == 'video')
        other_count = total_files - photo_count - video_count

        logger.info(
            f"[process_media_group] group={group_id} 开始处理: "
            f"共{total_files}个文件 ({photo_count}图/{video_count}视频/{other_count}其他)"
        )

        try:
            directory_name = self._extract_title(caption)
            group_dir = os.path.join(TELEGRAM_VIDEOS_DIR, directory_name)
            os.makedirs(group_dir, exist_ok=True)
            logger.info(f"[process_media_group] group={group_id} 目标目录: {group_dir}")

            photos = [f for f in media_files if f['type'] == 'photo']
            videos = [f for f in media_files if f['type'] == 'video']
            others = [f for f in media_files if f['type'] == 'other']

            photo_renames = {}
            for i, photo in enumerate(photos):
                new_filename = "fanart.jpg" if i == 0 else f"snapshot{i}.jpg"
                target_path = os.path.join(group_dir, new_filename)
                shutil.move(photo['temp_path'], target_path)
                photo_renames[photo['original_filename']] = new_filename
                logger.info(f"[process_media_group] group={group_id} 图片: {photo['original_filename']} -> {new_filename}")

            video_names = {}
            for i, video in enumerate(videos):
                original_ext = os.path.splitext(video['original_filename'])[1]
                new_filename = (
                    f"{directory_name}{original_ext}"
                    if video_count == 1
                    else f"{directory_name}_{i+1}{original_ext}"
                )
                new_filename = self._sanitize_filename(new_filename)
                target_path = os.path.join(group_dir, new_filename)
                shutil.move(video['temp_path'], target_path)
                video_names[video['original_filename']] = new_filename
                logger.info(f"[process_media_group] group={group_id} 视频: {video['original_filename']} -> {new_filename}")

            other_names = {}
            for other in others:
                original_filename = other['original_filename']
                target_path = os.path.join(group_dir, original_filename)
                shutil.move(other['temp_path'], target_path)
                other_names[original_filename] = original_filename
                logger.info(f"[process_media_group] group={group_id} 其他文件: {original_filename}")

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
            }

            logger.info(f"[process_media_group] group={group_id} 处理完成，目录: {group_dir}")
            return True, group_info

        except Exception as e:
            logger.exception(f"[process_media_group] group={group_id} 处理异常: {e}")
            for media_file in media_files:
                if os.path.exists(media_file['temp_path']):
                    try:
                        os.remove(media_file['temp_path'])
                        logger.debug(f"[process_media_group] 清理临时文件: {media_file['temp_path']}")
                    except Exception:
                        pass
            return False, str(e)
