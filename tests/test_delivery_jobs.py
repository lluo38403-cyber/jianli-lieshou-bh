"""投递跟进“进行中任务”列表的后端数据：最近走过投递流程的岗位。

v33 任务一：GET /api/workbench 新增的 delivery_jobs 字段由
``get_recent_delivery_jobs`` 提供。只读 jobs.status / history，不改
任何投递状态机；失败原因取 jobs.last_error（发送时已持久化）。
"""

import tempfile
from pathlib import Path

from bosshunter.db import (
    add_history,
    get_db,
    get_recent_delivery_jobs,
    insert_job,
    update_job_last_error,
    update_job_status,
)


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "测试岗位",
        "company": "测试公司",
        "salary": "20-30K",
        "city": "深圳",
        "experience": "1-3 年",
        "jd": "测试岗位描述",
        "hr_name": "HR",
        "hr_title": "招聘",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


def _seed(db) -> None:
    # 已投出：确认 → 投出，带最近一次投递动作。
    insert_job(db, {**_job("sent-1"), "company": "已投公司"})
    update_job_status(db, "sent-1", "approved")
    add_history(db, "sent-1", "approved", "Web Dashboard 确认投递")
    update_job_status(db, "sent-1", "sent")
    add_history(db, "sent-1", "sent", "招呼语")
    # 没投出去：确认 → 失败，原因写在 last_error。
    insert_job(db, {**_job("error-1"), "company": "失败公司"})
    update_job_status(db, "error-1", "approved")
    add_history(db, "error-1", "approved", "Web Dashboard 确认投递")
    update_job_status(db, "error-1", "error")
    update_job_last_error(db, "error-1", "对方暂时不接受新消息", "blocked")
    add_history(db, "error-1", "error", "对方暂时不接受新消息")
    # 排队中：只确认，还没投。
    insert_job(db, {**_job("approved-1"), "company": "排队公司"})
    update_job_status(db, "approved-1", "approved")
    add_history(db, "approved-1", "approved", "Web Dashboard 确认投递")
    # 不该出现的：还没走到投递的岗位。
    insert_job(db, {**_job("ready-1"), "company": "没投公司"})
    update_job_status(db, "ready-1", "ready")
    insert_job(db, {**_job("rejected-1"), "company": "不要公司"})
    update_job_status(db, "rejected-1", "rejected")


def test_recent_delivery_jobs_returns_delivery_flow_with_latest_step():
    with tempfile.TemporaryDirectory() as tmp:
        db = get_db(Path(tmp) / "bosshunter.db")
        try:
            _seed(db)
            rows = get_recent_delivery_jobs(db)
        finally:
            db.close()

    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {"sent-1", "error-1", "approved-1"}
    assert by_id["sent-1"]["status"] == "sent"
    assert by_id["sent-1"]["step_action"] == "sent"
    assert by_id["sent-1"]["step_created_at"]
    assert by_id["error-1"]["status"] == "error"
    assert by_id["error-1"]["step_action"] == "error"
    assert by_id["error-1"]["last_error"] == "对方暂时不接受新消息"
    assert by_id["approved-1"]["status"] == "approved"
    assert by_id["approved-1"]["step_action"] == "approved"


def test_recent_delivery_jobs_respects_limit_and_includes_jobs_without_history():
    with tempfile.TemporaryDirectory() as tmp:
        db = get_db(Path(tmp) / "bosshunter.db")
        try:
            # 只有状态、没有投递历史行的岗位也要出现（步骤字段为空，前端按状态展示）。
            insert_job(db, {**_job("approved-no-history"), "company": "无记录公司"})
            update_job_status(db, "approved-no-history", "approved")
            rows = get_recent_delivery_jobs(db, limit=1)
            assert len(rows) == 1
            rows = get_recent_delivery_jobs(db)
        finally:
            db.close()

    by_id = {row["id"]: row for row in rows}
    assert "approved-no-history" in by_id
    assert by_id["approved-no-history"]["step_action"] is None
    assert by_id["approved-no-history"]["step_created_at"] is None
