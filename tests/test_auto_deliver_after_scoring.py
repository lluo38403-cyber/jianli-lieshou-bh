"""v48 item 4：打分完成后“符合评分标准的直接投递”开关的后端链路。"""
import tempfile
from pathlib import Path
from unittest.mock import patch

from bosshunter.db import get_db, insert_job, update_job_score, update_job_status
from bosshunter.web import server
from bosshunter.web.tasks import WorkbenchTask


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "AI运营",
        "company": "Example",
        "salary": "20-30K",
        "city": "北京",
        "experience": "1-3年",
        "jd": "负责AI内容运营",
        "hr_name": "HR",
        "hr_title": "招聘者",
        "hr_active": "",
        "company_size": "",
        "company_industry": "",
        "url": "https://example.com/job",
    }


def _run_score(task_config: dict, db_path: Path, ready_ids: set, deliver_calls: list) -> WorkbenchTask:
    """跑一次 _execute_score；打分是假的：ready_ids 里的标 ready，其余标 filtered。"""
    task = WorkbenchTask(id="score", mode="score", label="单独评分")

    def fake_score_jobs(score_config, **kwargs):
        job_ids = list(kwargs.get("job_ids") or [])
        db = get_db(db_path)
        try:
            for job_id in job_ids:
                if job_id in ready_ids:
                    update_job_score(db, job_id, 85, "匹配")
                    update_job_status(db, job_id, "ready")
                else:
                    update_job_score(db, job_id, 50, "不匹配")
                    update_job_status(db, job_id, "filtered")
        finally:
            db.close()

    with patch("bosshunter.ai.scorer.score_jobs", side_effect=fake_score_jobs), \
         patch.object(server, "_execute_deliver", side_effect=lambda t, cfg: deliver_calls.append(cfg)):
        server._execute_score(task, task_config)
    return task


def _seed(base_dir: Path) -> Path:
    db_path = base_dir / "data" / "bosshunter.db"
    db = get_db(db_path)
    try:
        insert_job(db, _job("job-a"))
        insert_job(db, _job("job-b"))
    finally:
        db.close()
    server.set_base_dir(base_dir)
    return db_path


def test_switch_on_delivers_only_ready_jobs():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _seed(Path(tmp))
        deliver_calls: list = []
        task = _run_score(
            {
                "scoring": {"threshold": 60, "auto_deliver_ready": True},
                "_score_options": {"job_ids": ["job-a", "job-b"]},
            },
            db_path,
            {"job-a"},
            deliver_calls,
        )
        assert len(deliver_calls) == 1
        # 只投达标的 job-a；50 分的 job-b 不投。
        assert deliver_calls[0]["_workbench_job_ids"] == ["job-a"]
        assert any("符合标准的 1 个直接投递" in line for line in task.logs)


def test_switch_off_delivers_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _seed(Path(tmp))
        deliver_calls: list = []
        _run_score(
            {
                "scoring": {"threshold": 60, "auto_deliver_ready": False},
                "_score_options": {"job_ids": ["job-a", "job-b"]},
            },
            db_path,
            {"job-a"},
            deliver_calls,
        )
        assert deliver_calls == []


def test_switch_on_but_no_ready_jobs_delivers_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _seed(Path(tmp))
        deliver_calls: list = []
        task = _run_score(
            {
                "scoring": {"threshold": 60, "auto_deliver_ready": True},
                "_score_options": {"job_ids": ["job-a", "job-b"]},
            },
            db_path,
            set(),
            deliver_calls,
        )
        assert deliver_calls == []
        assert any("没有符合评分标准的岗位" in line for line in task.logs)
