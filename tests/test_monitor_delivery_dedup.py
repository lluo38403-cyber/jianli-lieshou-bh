"""H-5: 监测中重复确认同一岗位不得重复入队、重复发送招呼语。

复现：岗位入队后被 _take_monitor_deliveries 取走开始发送，此时岗位
状态仍是 approved；再确认一次，旧实现只看 pending 队列，已取走的批
不在其中，于是再次入队。修复后在途批次保留在 monitor_in_flight_ids
里，重复确认返回 already_queued_count；该批发送结束释放后才可再确认。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bosshunter.web.server import (
    _queue_monitor_delivery,
    _release_monitor_deliveries,
    _take_monitor_deliveries,
)
from bosshunter.web.tasks import WorkbenchTask


def _task() -> WorkbenchTask:
    return WorkbenchTask(id="monitor", mode="monitor", label="监测")


def test_monitor_duplicate_while_pending_is_deduped():
    task = _task()
    payload, new_ids = _queue_monitor_delivery(task, ["job-1", "job-2"])
    assert new_ids == ["job-1", "job-2"]
    assert payload["queued_count"] == 2
    assert payload["already_queued_count"] == 0

    payload, new_ids = _queue_monitor_delivery(task, ["job-2", "job-3"])
    assert new_ids == ["job-3"]
    assert payload["queued_count"] == 1
    assert payload["already_queued_count"] == 1
    pending = task.context["pending_deliveries"]
    assert [job for batch in pending for job in batch["job_ids"]] == ["job-1", "job-2", "job-3"]


def test_monitor_duplicate_while_in_flight_is_deduped():
    task = _task()
    _queue_monitor_delivery(task, ["job-1", "job-2"])
    batches = _take_monitor_deliveries(task)
    assert len(batches) == 1
    assert task.context["pending_deliveries"] == []

    # 在途批次已不在 pending 里，但重复确认仍不得入队。
    payload, new_ids = _queue_monitor_delivery(task, ["job-1"])
    assert new_ids == []
    assert payload["queued_count"] == 0
    assert payload["already_queued_count"] == 1
    assert task.context["pending_deliveries"] == []


def test_monitor_batch_release_allows_requeue_after_send():
    task = _task()
    _queue_monitor_delivery(task, ["job-1"])
    batches = _take_monitor_deliveries(task)
    assert task.context["monitor_in_flight_ids"] == {"job-1"}

    # 该批发送结束释放后（例如发送失败、岗位仍是 approved），可再次确认。
    _release_monitor_deliveries(task, batches[0]["job_ids"])
    assert task.context["monitor_in_flight_ids"] == set()
    payload, new_ids = _queue_monitor_delivery(task, ["job-1"])
    assert new_ids == ["job-1"]
    assert payload["queued_count"] == 1
