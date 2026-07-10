import os
import logging
import asyncio
import signal
from src.config.config_loader import load_config
from src.services.client_service import ClientService
from src.handlers.event_handler import EventHandler
from src.utils.file_utils import ensure_dirs
from src.constants import (
    BASE_DIR,
    TELEGRAM_TEMP_DIR,
    TELEGRAM_VIDEOS_DIR,
    TELEGRAM_AUDIOS_DIR,
    TELEGRAM_PHOTOS_DIR,
    TELEGRAM_OTHERS_DIR,
)

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "app.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def main():
    """主程序入口"""
    logger.info("=" * 60)
    logger.info("Telegram Assistant 启动中...")
    logger.info("=" * 60)

    try:
        logger.info("加载配置文件...")
        config = load_config()
        logger.info("配置文件加载成功")

        logger.info("创建目录结构...")
        ensure_dirs(
            TELEGRAM_TEMP_DIR,
            TELEGRAM_VIDEOS_DIR,
            TELEGRAM_AUDIOS_DIR,
            TELEGRAM_PHOTOS_DIR,
            TELEGRAM_OTHERS_DIR,
        )

        log_level = config.get("log_level", "INFO")
        logging.getLogger().setLevel(log_level)
        logger.info(f"日志级别设置为: {log_level}")

        client_service = ClientService(config)
        event_handler = EventHandler(config)

        logger.info("启动客户端...")
        user_client = await client_service.start_user_client()
        if user_client:
            logger.info("User client 启动成功")
        else:
            logger.info("User client 未启用")

        bot_client = await client_service.start_bot_client()
        if not bot_client:
            raise ValueError("Bot 客户端启动失败，请检查配置文件中的 bot_account.token")
        logger.info("Bot client 启动成功")

        event_handler.register_handlers(bot_client, user_client)
        logger.info("事件处理器注册完成")

        loop = asyncio.get_event_loop()

        async def shutdown(signal_=None):
            if signal_:
                logger.info(f"收到信号 {signal_.name}，开始优雅关闭...")
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            logger.info(f"取消 {len(tasks)} 个待处理任务")
            [task.cancel() for task in tasks]
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("断开所有客户端连接...")
            await client_service.disconnect_all()
            logger.info("程序已关闭")
            loop.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

        logger.info("=" * 60)
        logger.info("Telegram Assistant 运行中，等待消息...")
        logger.info("=" * 60)

        await asyncio.gather(
            *(client.run_until_disconnected() for client in client_service.clients)
        )

    except Exception as e:
        logger.exception(f"程序运行出错: {e}")
        raise
    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {e}")
