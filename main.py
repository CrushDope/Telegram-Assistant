import os
import logging
import asyncio
import signal
from src.config.config_loader import load_config
from src.services.client_service import ClientService
from src.handlers.event_handler import EventHandler
from src.utils.file_utils import ensure_dirs
from src.constants import (
    TELEGRAM_TEMP_DIR,
    TELEGRAM_VIDEOS_DIR,
    TELEGRAM_AUDIOS_DIR,
    TELEGRAM_PHOTOS_DIR,
    TELEGRAM_OTHERS_DIR,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


async def main():
    """主程序入口"""
    try:
        config = load_config()

        ensure_dirs(
            TELEGRAM_TEMP_DIR,
            TELEGRAM_VIDEOS_DIR,
            TELEGRAM_AUDIOS_DIR,
            TELEGRAM_PHOTOS_DIR,
            TELEGRAM_OTHERS_DIR,
        )

        logging.getLogger().setLevel(config.get("log_level", "INFO"))

        client_service = ClientService(config)
        event_handler = EventHandler(config)

        bot_client = await client_service.start_bot_client()
        if not bot_client:
            raise ValueError("Bot 客户端启动失败，请检查配置文件中的 bot_account.token")

        event_handler.register_handlers(bot_client)

        loop = asyncio.get_event_loop()

        async def shutdown(signal_=None):
            if signal_:
                logger.info(f"收到信号 {signal_.name}...")
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            [task.cancel() for task in tasks]
            await asyncio.gather(*tasks, return_exceptions=True)
            await client_service.disconnect_all()
            loop.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

        await asyncio.gather(
            *(client.run_until_disconnected() for client in client_service.clients)
        )

    except Exception as e:
        logger.error(f"程序运行出错: {str(e)}")
        raise
    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {str(e)}")
