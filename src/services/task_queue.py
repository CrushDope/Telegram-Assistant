import json
import logging
import os
import uuid
from datetime import datetime
from ..constants import TASK_QUEUE_FILE

logger = logging.getLogger(__name__)

# 任务类型
TASK_SINGLE = "single_media"
TASK_FORWARDED = "forwarded_media"

# 任务状态
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


class TaskQueue:
    """
    基于 JSON 文件的持久化任务队列。
    每次修改后立即写盘，重启后自动恢复未完成任务。
    """

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load(self):
        if not os.path.exists(TASK_QUEUE_FILE):
            return
        try:
            with open(TASK_QUEUE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._tasks = {t["task_id"]: t for t in data.get("tasks", [])}
            logger.info(f"[TaskQueue] 加载队列文件，共 {len(self._tasks)} 条记录")
        except Exception as e:
            logger.error(f"[TaskQueue] 加载队列文件失败: {e}")
            self._tasks = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(TASK_QUEUE_FILE), exist_ok=True)
            with open(TASK_QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump({"tasks": list(self._tasks.values())}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[TaskQueue] 保存队列文件失败: {e}")

    # ------------------------------------------------------------------
    # 写操作
    # ------------------------------------------------------------------

    def add_single(self, chat_id: int, message_id: int) -> str:
        """登记单文件下载任务"""
        task_id = f"{chat_id}_{message_id}"
        if task_id in self._tasks:
            return task_id
        self._tasks[task_id] = {
            "task_id": task_id,
            "task_type": TASK_SINGLE,
            "chat_id": chat_id,
            "message_id": message_id,
            "status": STATUS_PENDING,
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        logger.debug(f"[TaskQueue] 新增单文件任务 {task_id}")
        return task_id

    def add_forwarded(self, chat_id: int, message_id: int,
                      original_peer_id: int, original_peer_type: str,
                      original_message_id: int) -> str:
        """登记转发消息任务（需要 user client 拉取原始媒体组）"""
        task_id = f"fwd_{chat_id}_{message_id}"
        if task_id in self._tasks:
            return task_id
        self._tasks[task_id] = {
            "task_id": task_id,
            "task_type": TASK_FORWARDED,
            "chat_id": chat_id,
            "message_id": message_id,
            "original_peer_id": original_peer_id,
            "original_peer_type": original_peer_type,   # "channel" | "chat" | "user"
            "original_message_id": original_message_id,
            "status": STATUS_PENDING,
            "created_at": datetime.now().isoformat(),
        }
        self._save()
        logger.debug(f"[TaskQueue] 新增转发任务 {task_id}")
        return task_id

    def set_processing(self, task_id: str):
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = STATUS_PROCESSING
            self._save()

    def set_done(self, task_id: str):
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = STATUS_DONE
            self._tasks[task_id]["finished_at"] = datetime.now().isoformat()
            self._save()
            logger.debug(f"[TaskQueue] 任务完成 {task_id}")

    def set_failed(self, task_id: str, error: str):
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = STATUS_FAILED
            self._tasks[task_id]["error"] = error
            self._tasks[task_id]["finished_at"] = datetime.now().isoformat()
            self._save()
            logger.warning(f"[TaskQueue] 任务失败 {task_id}: {error}")

    # ------------------------------------------------------------------
    # 读操作
    # ------------------------------------------------------------------

    def pending_tasks(self) -> list[dict]:
        """返回所有未完成任务（pending 或 processing 视为未完成）"""
        return [
            t for t in self._tasks.values()
            if t["status"] in (STATUS_PENDING, STATUS_PROCESSING)
        ]

    def get(self, task_id: str) -> dict | None:
        return self._tasks.get(task_id)
