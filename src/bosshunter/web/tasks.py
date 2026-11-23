"""Workbench background task runner."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event, Lock, Thread, Timer
from typing import Any, Callable
from uuid import uuid4

from bosshunter.browser.ownership import browser_execution
from contextlib import nullcontext


logger = logging.getLogger(__name__)


MODE_LABELS = {
    "full": "运行全流程",
    "collect": "单独采集",
    "score": "单独 AI 评分",
    "rescore": "重新评分",
    "greet": "生成招呼语",
    "monitor": "单独监测",
    "deliver": "确认投递",
}

TERMINAL_STATUSES = {"completed", "failed", "stopped"}
ACTIVE_STATUSES = {"running", "stopping", "paused"}
DEADLINE_MODES = {"full", "monitor", "deliver"}


class TaskAlreadyRunningError(RuntimeError):
    """Raised when a mutually exclusive workbench task is already active."""


@dataclass
class WorkbenchTask:
    id: str
    mode: str
    label: str
    status: str = "running"
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    deadline_at: str | None = None
    stop_reason: str | None = None
    stop_requested: Event = field(default_factory=Event, repr=False)
    pause_requested: Event = field(default_factory=Event, repr=False)
    metrics: dict[str, int] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict, repr=False)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "label": self.label,
            "status": self.status,
            "logs": list(self.logs),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deadline_at": self.deadline_at,
            "stop_reason": self.stop_reason,
            "stop_requested": self.stop_requested.is_set(),
            "pause_requested": self.pause_requested.is_set(),
            "metrics": dict(self.metrics),
            "progress": dict(self.progress),
        }


Executor = Callable[[WorkbenchTask, dict], None]


def wait_for_initial_monitor_cooldown(
    task: WorkbenchTask,
    config: dict,
    log: Callable[[WorkbenchTask, str], None],
) -> bool:
    """Wait for the full-flow monitor cooldown; return true when cancellation wins."""
    raw_cooldown = config.get("monitor", {}).get("initial_cooldown_minutes", 10)
    try:
        cooldown_sec = max(float(raw_cooldown), 0) * 60
    except (TypeError, ValueError):
        cooldown_sec = 10 * 60
    if cooldown_sec <= 0:
        return False
    log(task, f"发送结束，首次监测将在 {cooldown_sec / 60:g} 分钟冷却后开始")
    if task.stop_requested.wait(cooldown_sec):
        log(task, "首次监测冷却已取消")
        return True
    return False


class WorkbenchTaskRunner:
    def __init__(self, executors: dict[str, Executor] | None = None):
        self._executors = executors or {}
        self._tasks: dict[str, WorkbenchTask] = {}
        self._threads: dict[str, Thread] = {}
        self._deadline_timers: dict[str, Timer] = {}
        # 每次 start() 给任务记一个 run 世代；force_stop() 推进世代，旧线程的
        # 完成回调核对不上当前世代时直接丢弃，不得复活任务状态。
        self._generations: dict[str, int] = {}
        self._lock = Lock()

    def start(
        self,
        mode: str,
        config: dict,
        *,
        before_start: Callable[[], None] | None = None,
    ) -> dict:
        if mode not in MODE_LABELS:
            raise ValueError(f"Unsupported workbench mode: {mode}")

        with self._lock:
            active = self._active_task_locked()
            if active:
                raise TaskAlreadyRunningError(
                    f"当前已有后台任务「{active.label}」正在运行或停止中，请等待其完全结束"
                )

            task = WorkbenchTask(id=str(uuid4()), mode=mode, label=MODE_LABELS[mode])
            deadline = _deadline_from_config(mode, config)
            if deadline:
                task.deadline_at = deadline.isoformat(timespec="seconds")
            if deadline and deadline <= datetime.now():
                task.stop_requested.set()
                task.status = "stopped"
                task.stop_reason = "今日发送时间窗口已截止，后台未启动"
                task.logs.append(task.stop_reason)
                task.updated_at = datetime.now().isoformat(timespec="seconds")
                self._tasks[task.id] = task
                return task.snapshot()

            # Persist start settings under the same lock as admission. A failed
            # callback must leave no registered task or worker behind.
            if before_start is not None:
                before_start()
            self._tasks[task.id] = task
            generation = 1
            self._generations[task.id] = generation
            thread = Thread(target=self._run, args=(task, config, generation), daemon=True)
            self._threads[task.id] = thread
            if deadline:
                delay_seconds = max((deadline - datetime.now()).total_seconds(), 0)
                timer = Timer(delay_seconds, self._stop_at_deadline, args=(task.id,))
                timer.daemon = True
                self._deadline_timers[task.id] = timer
                timer.start()
            thread.start()
            return task.snapshot()

    def status(self) -> dict:
        with self._lock:
            active = self._active_task_locked()
            tasks = [task.snapshot() for task in self._tasks.values()]
            return {
                "active": active.snapshot() if active else None,
                "last_task": tasks[-1] if tasks else None,
                "tasks": tasks,
            }

    def pause(self, task_id: str) -> dict:
        """Pause a running task: the worker waits between sends until resumed.

        The task keeps its browser lock and its place in the queue; only
        ``running`` tasks can be paused. A paused task still counts as the
        active task, so no other task can start while it is paused.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.status in TERMINAL_STATUSES or task.status != "running":
                return task.snapshot()
            task.pause_requested.set()
            task.status = "paused"
            if not task.logs or task.logs[-1] != "已暂停，等你点继续再投":
                task.logs.append("已暂停，等你点继续再投")
            task.updated_at = datetime.now().isoformat(timespec="seconds")
            return task.snapshot()

    def resume(self, task_id: str) -> dict:
        """Resume a paused task; only ``paused`` tasks can be resumed."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.status != "paused":
                return task.snapshot()
            task.pause_requested.clear()
            task.status = "running"
            if not task.logs or task.logs[-1] != "已继续投递":
                task.logs.append("已继续投递")
            task.updated_at = datetime.now().isoformat(timespec="seconds")
            return task.snapshot()

    def stop(self, task_id: str, reason: str = "用户已请求停止") -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.status in TERMINAL_STATUSES:
                return task.snapshot()
            persist_stop = task.context.get("persist_stop")
            if callable(persist_stop):
                try:
                    persist_stop()
                finally:
                    task.stop_requested.set()
            task.stop_requested.set()
            task.pause_requested.clear()
            task.status = "stopping"
            task.stop_reason = reason
            if reason and (not task.logs or task.logs[-1] != reason):
                task.logs.append(reason)
            task.updated_at = datetime.now().isoformat(timespec="seconds")
            confirmation_event = task.context.get("confirmation_event")
            if isinstance(confirmation_event, Event):
                confirmation_event.set()
            monitor_wakeup_event = task.context.get("monitor_wakeup_event")
            if isinstance(monitor_wakeup_event, Event):
                monitor_wakeup_event.set()
            return task.snapshot()

    def force_stop(self, task_id: str, reason: str = "用户已强制停止", *, wait_seconds: float = 10.0) -> dict:
        """Force a task to ``stopped`` when its worker thread will not end.

        Intended flow: ``stop()`` first, then ``force_stop()`` once the worker
        has not ended within ``wait_seconds`` (default 10s). The wait happens
        outside the lock; if the thread ends naturally inside the wait, its
        own terminal snapshot is returned and nothing is forced.

        Only task state is touched: the browser lock is NOT released, the
        worker thread is NOT killed, and the browser is NOT touched. While
        the old thread is still alive, a new task fails fast with
        ``BrowserBusyError`` -- the frontend should tell the user the previous
        task is still exiting and to retry starting in a few seconds.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            thread = self._threads.get(task_id)
            if not task:
                raise KeyError(task_id)
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(wait_seconds, 0.0))
        with self._lock:
            if task.status in TERMINAL_STATUSES:
                return task.snapshot()
            # 推进 run 世代：旧线程最终返回时，完成回调核对不上当前世代会被
            # 直接丢弃，不得复活任务状态。
            self._generations[task.id] = self._generations.get(task.id, 1) + 1
            task.stop_requested.set()
            task.pause_requested.clear()
            task.status = "stopped"
            task.stop_reason = reason
            task.error = None
            if isinstance(task.progress, dict) and "stage" in task.progress:
                task.progress["stage"] = "stopped"
            if reason and (not task.logs or task.logs[-1] != reason):
                task.logs.append(reason)
            task.updated_at = datetime.now().isoformat(timespec="seconds")
            confirmation_event = task.context.get("confirmation_event")
            if isinstance(confirmation_event, Event):
                confirmation_event.set()
            monitor_wakeup_event = task.context.get("monitor_wakeup_event")
            if isinstance(monitor_wakeup_event, Event):
                monitor_wakeup_event.set()
            logger.warning(
                "任务 %s（%s）执行线程在 %.1f 秒内未结束，已强制标记为 stopped；"
                "旧线程返回时将被丢弃，浏览器锁不释放",
                task.id,
                task.label,
                wait_seconds,
            )
            return task.snapshot()

    def _is_current_run_locked(self, task_id: str, generation: int) -> bool:
        """核对完成回调的 run 世代；调用方须已持有 ``self._lock``。"""
        return self._generations.get(task_id) == generation

    def wait(self, timeout: float | None = None) -> None:
        threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=timeout)

    def _run(self, task: WorkbenchTask, config: dict, generation: int) -> None:
        try:
            executor = self._executors.get(task.mode)
            if executor:
                with browser_execution() if task.mode in {"full", "collect", "monitor", "deliver"} else nullcontext():
                    executor(task, config)
            with self._lock:
                if not self._is_current_run_locked(task.id, generation):
                    # 已被 force_stop 接管：旧线程的完成回调直接丢弃，不得复活任务状态。
                    return
                if task.stop_requested.is_set():
                    task.status = "stopped"
                else:
                    task.status = "completed"
                task.updated_at = datetime.now().isoformat(timespec="seconds")
        except Exception as exc:
            with self._lock:
                if not self._is_current_run_locked(task.id, generation):
                    # 已被 force_stop 接管：旧线程的完成回调直接丢弃，不得复活任务状态。
                    return
                if task.stop_requested.is_set():
                    task.status = "stopped"
                    task.error = None
                else:
                    task.status = "failed"
                    task.error = str(exc)
                task.updated_at = datetime.now().isoformat(timespec="seconds")
        finally:
            with self._lock:
                timer = self._deadline_timers.pop(task.id, None)
                if self._generations.get(task.id) == generation:
                    self._generations.pop(task.id, None)
            if timer:
                timer.cancel()

    def _stop_at_deadline(self, task_id: str) -> None:
        try:
            self.stop(task_id, "已到发送时间窗口截止时间，后台自动停止")
        except KeyError:
            return

    def _active_task_locked(self) -> WorkbenchTask | None:
        for task in self._tasks.values():
            if task.status in ACTIVE_STATUSES:
                return task
        return None


def _deadline_from_config(mode: str, config: dict) -> datetime | None:
    """Resolve the automatic stop deadline for long-running/send tasks.

    投递时间窗口已取消：任务不再按窗口自动截止，始终返回 None。
    """
    return None
