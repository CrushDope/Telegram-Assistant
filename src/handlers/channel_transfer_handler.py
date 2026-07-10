import logging
import asyncio
import os
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import Channel, MessageEntityTextUrl
from telethon.errors import FloodWaitError

logger = logging.getLogger(__name__)

# 定义上海时区（UTC+8）
SHANGHAI_TIMEZONE = timezone(timedelta(hours=8))


class ChannelTransferHandler:
    """处理频道消息转发的类"""

    def __init__(self, client: TelegramClient):
        """
        初始化频道转发处理器

        Args:
            client: Telegram客户端实例
        """
        self.client = client
        # 创建临时目录
        self.temp_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "temp",
        )
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)

    async def get_entity(self, channel_id_or_username):
        """获取频道实体"""
        try:
            return await self.client.get_entity(channel_id_or_username)
        except Exception as e:
            logger.error(f"获取频道实体失败: {str(e)}")
            return None

    async def transfer_messages(self, source_channel, target_channel, since_date):
        """
        转发指定日期后的消息

        Args:
            source_channel: 源频道ID/用户名/实体对象
            target_channel: 目标频道ID/用户名/实体对象
            since_date: 日期时间对象，只转发该时间之后的消息

        Returns:
            成功转发的消息数量
        """
        try:
            # 判断传入的是否已经是实体对象
            if isinstance(source_channel, Channel):
                source_entity = source_channel
            else:
                source_entity = await self.get_entity(source_channel)

            if isinstance(target_channel, Channel):
                target_entity = target_channel
            else:
                target_entity = await self.get_entity(target_channel)

            if not source_entity or not target_entity:
                logger.error("未能获取源频道或目标频道实体")
                return 0

            # 将日期转换为时间戳（秒）
            since_timestamp = since_date.timestamp()
            logger.info(
                f"使用时间戳作为起始时间: {since_timestamp} ({since_date.strftime('%Y-%m-%d %H:%M:%S')})"
            )

            # 获取频道消息历史
            messages = []
            offset_id = 0
            limit = 100
            total_messages = 0

            while True:
                history = await self.client(
                    GetHistoryRequest(
                        peer=source_entity,
                        offset_id=offset_id,
                        offset_date=None,
                        add_offset=0,
                        limit=limit,
                        max_id=0,
                        min_id=0,
                        hash=0,
                    )
                )

                if not history.messages:
                    break

                # 更新offset以获取下一批消息
                offset_id = history.messages[-1].id
                total_count = len(history.messages)
                total_messages += total_count
                now_message_count = 0

                # 过滤消息
                for message in history.messages:
                    message_time_shanghai = message.date.astimezone(SHANGHAI_TIMEZONE)
                    # 将消息日期转换为时间戳
                    message_timestamp = message_time_shanghai.timestamp()

                    # 使用时间戳直接比较
                    if message_timestamp + 8 * 3600 >= since_timestamp:
                        messages.append(message)
                        now_message_count += 1
                        logger.info(
                            f"消息时间戳 {message_timestamp} ({message_time_shanghai.strftime('%Y-%m-%d %H:%M:%S')}) >= 起始时间戳 {since_timestamp} ({since_date.strftime('%Y-%m-%d %H:%M:%S')})，添加"
                        )
                    else:
                        logger.info(
                            f"消息时间戳 {message_timestamp} ({message_time_shanghai.strftime('%Y-%m-%d %H:%M:%S')}) < 起始时间戳 {since_timestamp} ({since_date.strftime('%Y-%m-%d %H:%M:%S')})，跳过"
                        )
                        break

                if now_message_count < len(history.messages):
                    logger.info(
                        f"当前已收集符合条件的消息数：{now_message_count}条，跳过剩余消息"
                    )
                    break

                # 避免触发速率限制
                await asyncio.sleep(1)

            message_count = len(messages)
            logger.info(f"当前已收集符合条件的消息数：{message_count}条")

            # 开始转发消息
            forwarded_count = 0
            messages.reverse()
            for message in messages:
                try:
                    # 提取消息文本和实体（保留格式化和链接）
                    text = message.message
                    entities = message.entities

                    # 检查是否有链接实体，输出调试信息
                    url = ""
                    for entity in entities:
                        if isinstance(entity, MessageEntityTextUrl):
                            url = entity.url
                            text += f"\n{url}"
                            if "115" in url:
                                logger.info(f"发现链接:  {url}")
                                break

                    # 如果发现了链接，检查消息文本中是否有"点击转存"字样，将其替换为Markdown链接形式
                    if url and "点击转存" in text:
                        # 替换"点击转存"为Markdown格式的链接
                        text = text.replace("点击转存", f"[点击转存]({url})")

                        # 移除之前在文本末尾添加的链接
                        if text.endswith(url) or text.endswith(f"\n{url}"):
                            text = text[
                                : -(len(url) + (1 if text.endswith(f"\n{url}") else 0))
                            ]

                        logger.info(f"已将'点击转存'转换为Markdown链接形式: {url}")

                        # 设置parse_mode为Markdown，清除entities避免冲突
                        entities = None

                    # 检查消息是否包含photo
                    if hasattr(message, "photo") and message.photo:
                        # 下载照片到临时文件
                        temp_file_path = os.path.join(
                            self.temp_dir, f"photo_{message.id}.jpg"
                        )
                        await self.client.download_media(message.photo, temp_file_path)

                        # 发送带格式的文本和照片
                        await self.client.send_message(
                            target_entity,
                            text,
                            file=temp_file_path,
                            formatting_entities=None if entities is None else entities,
                            parse_mode="md" if entities is None else None,
                        )

                        # 删除临时文件
                        if os.path.exists(temp_file_path):
                            os.remove(temp_file_path)

                        forwarded_count += 1
                        logger.info("已转发图文消息（保留格式）")
                    else:
                        # 发送带格式的纯文本
                        await self.client.send_message(
                            target_entity,
                            text,
                            formatting_entities=None if entities is None else entities,
                            parse_mode="md" if entities is None else None,
                        )
                        forwarded_count += 1
                        logger.info(f"已转发文本消息（保留格式）: {text[:30]}...")

                    # 避免触发速率限制
                    await asyncio.sleep(2)

                except FloodWaitError as e:
                    logger.warning(f"遇到速率限制，等待 {e.seconds} 秒")
                    await asyncio.sleep(e.seconds)
                    # 继续下一条消息
                except Exception as e:
                    logger.error(f"转发消息时出错: {str(e)}")

            return forwarded_count

        except Exception as e:
            logger.error(f"转发消息失败: {str(e)}")
            return 0

    async def schedule_transfer(
        self, source_channel, target_channel, since_date_str, interval_hours=24
    ):
        """
        定时转发指定日期后的消息

        Args:
            source_channel: 源频道ID/用户名/实体对象
            target_channel: 目标频道ID/用户名/实体对象
            since_date_str: 字符串格式的日期 'YYYY-MM-DD HH:MM:SS' 或 datetime 对象
            interval_hours: 定时执行的时间间隔（小时）
        """
        # 判断是否已是日期对象
        if isinstance(since_date_str, datetime):
            since_date = since_date_str
        else:
            since_date = datetime.strptime(since_date_str, "%Y-%m-%d %H:%M:%S")

        # 添加上海时区信息（方便日志显示）
        if since_date.tzinfo is None:
            since_date = since_date.replace(tzinfo=SHANGHAI_TIMEZONE)

        # 获取频道名称用于日志
        source_name = (
            source_channel.title
            if hasattr(source_channel, "title")
            else str(source_channel)
        )
        target_name = (
            target_channel.title
            if hasattr(target_channel, "title")
            else str(target_channel)
        )

        while True:
            logger.info(f"开始从 {source_name} 向 {target_name} 转发消息")
            count = await self.transfer_messages(
                source_channel, target_channel, since_date
            )
            logger.info(f"成功转发 {count} 条消息")

            # 更新since_date为当前时间，这样下次只会转发新消息
            since_date = datetime.now()
            logger.info(
                f"更新起始时间为当前时间: {since_date.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            # 等待指定的小时数
            logger.info(f"等待 {interval_hours} 小时后继续执行")
            await asyncio.sleep(interval_hours * 3600)
