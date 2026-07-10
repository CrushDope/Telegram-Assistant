import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")

TEMP_DIR = os.path.join(BASE_DIR, "temp")
TELEGRAM_TEMP_DIR = os.path.join(TEMP_DIR, "telegram")

TELEGRAM_DEST_DIR = os.path.join(BASE_DIR, "downloads/telegram")
TELEGRAM_VIDEOS_DIR = os.path.join(TELEGRAM_DEST_DIR, "videos")
TELEGRAM_AUDIOS_DIR = os.path.join(TELEGRAM_DEST_DIR, "audios")
TELEGRAM_PHOTOS_DIR = os.path.join(TELEGRAM_DEST_DIR, "photos")
TELEGRAM_OTHERS_DIR = os.path.join(TELEGRAM_DEST_DIR, "others")
