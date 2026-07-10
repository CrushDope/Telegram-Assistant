#!/usr/bin/env python3
import os
import sys
import asyncio
import logging
from telethon import TelegramClient

# 添加项目根目录到系统路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config.config_loader import load_config

# 配置日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# 获取程序所在目录的绝对路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")


async def list_all_channels():
    """列出所有可访问的频道"""
    try:
        # 加载配置
        config = load_config()

        # 创建Telegram客户端
        api_id = config["api_id"]
        api_hash = config["api_hash"]
        session_name = config["user_account"]["session_name"]
        session_file = os.path.join(CONFIG_DIR, session_name)

        client = TelegramClient(session_file, api_id, api_hash)
        await client.start()

        if not client.is_connected():
            logger.error("客户端连接失败")
            return 1

        logger.info("客户端连接成功，开始获取对话列表...")

        # 遍历所有对话，筛选出频道类型
        channels = []
        async for dialog in client.iter_dialogs():
            if dialog.is_channel:
                channels.append(
                    {
                        "name": dialog.name,
                        "id": dialog.id,
                        "username": (
                            dialog.entity.username
                            if hasattr(dialog.entity, "username")
                            else None
                        ),
                    }
                )
                print(
                    f"频道: {dialog.name} - ID: {dialog.id} - 用户名: {dialog.entity.username if hasattr(dialog.entity, 'username') else '无'}"
                )

        logger.info(f"总共找到 {len(channels)} 个频道")
        return channels

    except Exception as e:
        logger.error(f"程序运行出错: {str(e)}")
        return []

    finally:
        # 断开客户端连接
        await client.disconnect()
        logger.info("客户端已断开连接")


if __name__ == "__main__":
    try:
        asyncio.run(list_all_channels())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        sys.exit(0)
    except Exception as e:
        logger.error(f"程序异常退出: {str(e)}")
        sys.exit(1)
