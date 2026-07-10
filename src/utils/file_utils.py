import os
import re
import shutil
import logging

logger = logging.getLogger(__name__)


def sanitize_filename(s):
    """清理文件名中的非法字符"""
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip()
    return s


def ensure_dirs(*dirs):
    """确保目录存在"""
    for dir_path in dirs:
        os.makedirs(dir_path, exist_ok=True)


def move_file(source_path, target_path, create_dirs=True):
    """移动文件到目标位置"""
    try:
        if create_dirs:
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.move(source_path, target_path)
        return True, target_path
    except Exception as e:
        return False, str(e)
