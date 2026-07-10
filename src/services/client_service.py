import os
import logging
from telethon import TelegramClient
from ..constants import CONFIG_DIR

logger = logging.getLogger(__name__)


class ClientService:
    def __init__(self, config):
        self.config = config
        self.clients = []
        self._setup_proxy()

    def _setup_proxy(self):
        """配置代理"""
        proxy_config = self.config.get("proxy", {})
        self.proxy = None
        if (
            proxy_config.get("enabled")
            and proxy_config.get("host")
            and proxy_config.get("port")
        ):
            self.proxy = {
                "proxy_type": "socks5",
                "addr": proxy_config["host"],
                "port": proxy_config["port"],
            }

    async def start_user_client(self):
        """启动用户客户端"""
        user_config = self.config.get("user_account", {})
        if not user_config.get("enabled", False):
            return None

        logger.info("正在启动用户账号客户端...")
        session_name = user_config.get("session_name", "user_session")
        session_path = os.path.join(CONFIG_DIR, session_name)

        client = TelegramClient(
            session_path,
            self.config["api_id"],
            self.config["api_hash"],
            proxy=self.proxy,
        )

        try:
            phone = user_config.get("phone", "")
            await client.start(phone=phone)
            logger.info(f"用户账号 {phone} 登录成功！")
            self.clients.append(client)
            return client
        except Exception as e:
            logger.error(f"用户客户端启动失败: {str(e)}")
            raise

    async def start_bot_client(self):
        """启动机器人客户端"""
        bot_config = self.config.get("bot_account", {})
        if not bot_config.get("token"):
            return None

        logger.info("正在启动机器人客户端...")
        session_name = bot_config.get("session_name", "bot_session")
        session_path = os.path.join(CONFIG_DIR, session_name)

        client = TelegramClient(
            session_path,
            self.config["api_id"],
            self.config["api_hash"],
            proxy=self.proxy,
        )

        try:
            await client.start(bot_token=bot_config["token"])
            logger.info("机器人启动成功！")
            self.clients.append(client)
            return client
        except Exception as e:
            logger.error(f"机器人客户端启动失败: {str(e)}")
            return None

    async def disconnect_all(self):
        """断开所有客户端连接"""
        for client in self.clients:
            try:
                await client.disconnect()
            except Exception as e:
                logger.error(f"断开客户端连接时出错: {str(e)}")
