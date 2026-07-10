import os
import yaml
import logging
from ..constants import CONFIG_DIR

logger = logging.getLogger(__name__)


def load_config():
    """加载配置文件"""
    config_file = os.path.join(CONFIG_DIR, "config.yaml")
    default_config = {
        "api_id": "",
        "api_hash": "",
        "user_account": {
            "enabled": False,
            "phone": "",
            "session_name": "user_session",
        },
        "bot_account": {
            "token": "",
            "session_name": "bot_session",
        },
        "log_level": "INFO",
        "proxy": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 7890,
        },
        "send_file": False,
        "media_group_delay": 3.0,
        "max_concurrent_downloads": 2,
    }

    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        config_updated = False

        if os.path.exists(config_file):
            with open(config_file, "r", encoding="utf-8") as file:
                config = yaml.safe_load(file) or {}
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                        config_updated = True
                    elif isinstance(value, dict) and isinstance(config[key], dict):
                        for sub_key, sub_value in value.items():
                            if sub_key not in config[key]:
                                config[key][sub_key] = sub_value
                                config_updated = True

            if config_updated:
                with open(config_file, "w", encoding="utf-8") as file:
                    yaml.dump(config, file, allow_unicode=True, default_flow_style=False)
                logger.info("配置文件已更新，新增配置项已添加")
        else:
            config = default_config
            with open(config_file, "w", encoding="utf-8") as file:
                yaml.dump(config, file, allow_unicode=True, default_flow_style=False)
            raise ValueError("请先配置 config/config.yaml 文件")

        if not config.get("api_id") or not config.get("api_hash"):
            raise ValueError("请在 config.yaml 中配置 api_id 和 api_hash")

        if not config.get("bot_account", {}).get("token"):
            raise ValueError("请在 config.yaml 中配置 bot_account.token")

        return config

    except Exception as e:
        logger.error(f"加载配置文件时出错: {str(e)}")
        raise
