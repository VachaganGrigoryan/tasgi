"""Services for the modular tasgi example app."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from models import QueueStatsOut, TaskCreateIn, TaskOut


@dataclass
class _TaskRecord:
    task_id: str
    title: str
    owner: str
    status: str


class TaskQueueService:
    """Simple in-memory task queue used by the modular example."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._sequence = 0
        self._tasks: dict[str, _TaskRecord] = {}
        self._seed()

    def _seed(self) -> None:
        self.create_task(TaskCreateIn(title="Sync product catalog", owner="ops"))
        initial = self.create_task(TaskCreateIn(title="Prepare weekly finance export", owner="finance"))
        self.mark_completed(initial.task_id)

    def list_tasks(self) -> list[TaskOut]:
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda task: task.task_id)
        return [
            TaskOut(task_id=task.task_id, title=task.title, owner=task.owner, status=task.status)
            for task in tasks
        ]

    def create_task(self, payload: TaskCreateIn) -> TaskOut:
        with self._lock:
            self._sequence += 1
            task = _TaskRecord(
                task_id="task-%03d" % self._sequence,
                title=payload.title,
                owner=payload.owner,
                status="queued",
            )
            self._tasks[task.task_id] = task
        return TaskOut(task_id=task.task_id, title=task.title, owner=task.owner, status=task.status)

    def mark_completed(self, task_id: str) -> TaskOut:
        with self._lock:
            task = self._tasks[task_id]
            task.status = "completed"
            return TaskOut(task_id=task.task_id, title=task.title, owner=task.owner, status=task.status)

    def stats(self) -> QueueStatsOut:
        with self._lock:
            total = len(self._tasks)
            completed = sum(1 for task in self._tasks.values() if task.status == "completed")
        return QueueStatsOut(
            total_tasks=total,
            completed_tasks=completed,
            pending_tasks=total - completed,
        )

