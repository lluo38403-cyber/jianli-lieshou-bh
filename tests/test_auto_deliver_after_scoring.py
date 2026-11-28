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


def _run_full(task_config: dict, db_path: Path, deliver_calls: list, monitor_calls: list) -> WorkbenchTask:
    """跑一次 _execute_full；采集是假的（什么都不采），只看投递门。"""
    task = WorkbenchTask(id="full", mode="full", label="运行全流程")
    with patch.object(server, "_execute_collect", side_effect=lambda t, cfg: None), \
         patch.object(server, "_execute_deliver", side_effect=lambda t, cfg: deliver_calls.append(cfg)), \
         patch.object(server, "_execute_monitor", side_effect=lambda t, cfg, **kw: monitor_calls.append(cfg)):
        server._execute_full(task, task_config)
    return task


def test_full_flow_switch_off_scores_only():
    """v54 图9：全流程开关关着时只打分、不自动投递、不进监测。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _seed(Path(tmp))
        deliver_calls: list = []
        monitor_calls: list = []
        task = _run_full(
            {"scoring": {"threshold": 60, "auto_deliver_ready": False}},
            db_path,
            deliver_calls,
            monitor_calls,
        )
        assert deliver_calls == []
        assert monitor_calls == []
        assert any("只打分、不自动投递" in line for line in task.logs)


def test_full_flow_switch_on_delivers():
    """开关开着时全流程照常投递（门只拦关着的情况）。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = _seed(Path(tmp))
        db = get_db(db_path)
        try:
            update_job_score(db, "job-a", 85, "匹配")
            update_job_status(db, "job-a", "ready")
        finally:
            db.close()
        deliver_calls: list = []
        monitor_calls: list = []
        task = _run_full(
            {"scoring": {"threshold": 60, "auto_deliver_ready": True}},
            db_path,
            deliver_calls,
            monitor_calls,
        )
        assert len(deliver_calls) == 1
        assert "job-a" in deliver_calls[0]["_workbench_job_ids"]
        assert len(monitor_calls) == 1


def test_switch_flipped_off_mid_run_stops_auto_deliver():
    """任务启动时开关开着、打分期间用户关了开关：以磁盘最新值为准，不投。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        db_path = _seed(base)
        (base / "config.yaml").write_text(
            "scoring:\n  threshold: 60\n  auto_deliver_ready: false\n",
            encoding="utf-8",
        )
        deliver_calls: list = []
        _run_score(
            {
                "scoring": {"threshold": 60, "auto_deliver_ready": True},
                "_score_options": {"job_ids": ["job-a", "job-b"]},
            },
            db_path,
            {"job-a"},
            deliver_calls,
        )
        assert deliver_calls == []
