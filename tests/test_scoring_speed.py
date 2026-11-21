"""Scoring speed (v32 task 3): concurrency, prompt reuse and live progress.

Covers the v32 changes only: default concurrency 3, one prompt build per
job across retries/review, in-flight progress reporting (including retry
state), and failed/interrupted jobs leaving the in-flight list.
"""
import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from bosshunter.ai import scorer
from bosshunter.ai.scorer import ScoreOutcome, score_jobs
from bosshunter.db import get_db, insert_job

RESUME = "候选人简历：5 年 AI 产品经验，主导过推荐系统与搜索排序，熟悉 Python/SQL。"

# 让 _call_claude 走到 call_ai_text（mock 的传输层）：不配 service 时
# 无 Key 会直接返回 None，改走本地规则评分。
RELAY_AI = {
    "service": "relay",
    "relay_url": "http://127.0.0.1:9",
    "relay_token": "bench-token",
}


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": f"AI Product Manager {job_id}",
        "company": "Example",
        "salary": "20-30K",
        "city": "Shanghai",
        "experience": "1-3 years",
        "jd": "Build AI product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "active",
        "company_size": "100-499",
        "company_industry": "Software",
        "url": f"https://example.com/jobs/{job_id}",
    }


def _score_response(score: int) -> str:
    components = {
        72: (32, 20, 11, 7, 8),
        75: (34, 21, 12, 7, 8),
        85: (36, 22, 13, 8, 9),
    }[score]
    return json.dumps({
        "role_summary": "AI 产品经理",
        "core_duties": {"score": components[0], "evidence": "匹配"},
        "transferable_evidence": {"score": components[1], "evidence": "匹配"},
        "hard_requirements": {"score": components[2], "evidence": "匹配"},
        "tools_industry": {"score": components[3], "evidence": "匹配"},
        "practical_fit": {"score": components[4], "evidence": "匹配"},
        "caps": [],
        "hard_gaps": [],
        "reason": "匹配",
        "missing": "",
    }, ensure_ascii=False)


def _fake_call_ai_text(latency: float):
    def _call(prompt, config, max_tokens, **kwargs):
        time.sleep(latency)
        return _score_response(85)
    return _call


def _seed_db(tmp_path: Path, name: str, count: int) -> Path:
    db_path = tmp_path / name
    db = get_db(db_path)
    try:
        for i in range(count):
            insert_job(db, _job(f"job-{i:03d}"))
    finally:
        db.close()
    return db_path


def _run_batch(db_path: Path, count: int, concurrency: int, latency: float) -> float:
    events: list[dict] = []
    config = {
        "ai": {
            "service": "relay",
            "relay_url": "http://127.0.0.1:9",
            "relay_token": "bench-token",
            "scoring_concurrency": concurrency,
            "scoring_max_attempts": 2,
        },
        "scoring": {"threshold": 60},
        "profile": {"resume_path": "unused"},
        "_workbench_score_progress": events.append,
    }
    start = time.monotonic()
    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value=RESUME),
        patch("bosshunter.ai.scorer.call_ai_text", side_effect=_fake_call_ai_text(latency)),
    ):
        scored, filtered = score_jobs(config)
    assert (scored, filtered) == (count, 0)
    assert events[0] == {
        "completed": 0, "total": count, "scored": 0, "filtered": 0, "failed": 0,
    }
    assert events[-1]["completed"] == count
    assert events[-1]["current"] == []
    return time.monotonic() - start


def test_prompt_built_once_per_job_across_retry_and_review():
    """首次、重试、二次复核共用同一份 Prompt，不重复拼接简历与 JD。"""
    job = _job("one")
    config = {"ai": {"scoring_second_review": True}, "scoring": {"threshold": 60}}
    build_count = 0
    original_build = scorer._build_scoring_prompt

    def counting_build(*args, **kwargs):
        nonlocal build_count
        build_count += 1
        return original_build(*args, **kwargs)

    with (
        patch("bosshunter.ai.scorer._build_scoring_prompt", side_effect=counting_build),
        patch(
            "bosshunter.ai.scorer._call_claude",
            side_effect=["这不是完整 JSON", _score_response(72), _score_response(75)],
        ) as call_ai,
    ):
        outcome = scorer._score_job_with_ai(job, RESUME, config, 2)

    assert outcome.result is not None
    assert outcome.result.reviewed is True
    # 3 次 AI 调用（首次失败重试 + 临界分二次复核），Prompt 只构建 1 次。
    assert call_ai.call_count == 3
    assert build_count == 1
    # 复核 Prompt 就是首次 Prompt + 复核后缀，内容一致。
    assert call_ai.call_args_list[2].args[0].startswith(call_ai.call_args_list[1].args[0])


def test_batch_scoring_is_faster_with_concurrency_three(tmp_path):
    """同样 10 份、每调用 0.2 秒：并发 3 明显快于并发 1，且结果一致。"""
    db_path = _seed_db(tmp_path, "batch.db", 10)
    elapsed_one = _run_batch(db_path, 10, 1, 0.2)
    db_path = _seed_db(tmp_path, "batch3.db", 10)
    elapsed_three = _run_batch(db_path, 10, 3, 0.2)
    assert elapsed_one >= 1.9  # 10 次串行调用，每次 >= 0.2 秒
    assert elapsed_three < elapsed_one * 0.75


def test_progress_reports_in_flight_job_then_clears(tmp_path):
    """进度上报在途岗位（第 X/N 份、公司｜职位），打完后在途清空。"""
    db_path = _seed_db(tmp_path, "progress.db", 3)
    events: list[dict] = []
    config = {
        "ai": {**RELAY_AI, "scoring_concurrency": 1, "scoring_max_attempts": 2},
        "scoring": {"threshold": 60},
        "_workbench_score_progress": events.append,
    }
    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value=RESUME),
        patch("bosshunter.ai.scorer.call_ai_text", side_effect=_fake_call_ai_text(0.05)),
    ):
        assert score_jobs(config) == (3, 0)

    in_flight = [e for e in events if e.get("current")]
    assert in_flight, "提交后应有带在途岗位的进度事件"
    first = in_flight[0]["current"][0]
    assert first["index"] == 1
    assert first["company"] == "Example"
    assert first["title"] == "AI Product Manager job-000"
    assert first["attempt"] == 1
    assert events[-1]["completed"] == 3
    assert events[-1]["current"] == []


def test_progress_shows_retry_attempt_state(tmp_path):
    """首次返回无效 JSON 时，在途岗位应显示第 2 次尝试，重试成功后完成。"""
    db_path = _seed_db(tmp_path, "retry.db", 1)
    events: list[dict] = []
    config = {
        "ai": {**RELAY_AI, "scoring_concurrency": 1, "scoring_max_attempts": 2},
        "scoring": {"threshold": 60},
        "_workbench_score_progress": events.append,
    }
    calls = {"count": 0}

    def flaky_call(prompt, config, max_tokens, **kwargs):
        calls["count"] += 1
        time.sleep(0.2)
        return "这不是完整 JSON" if calls["count"] == 1 else _score_response(85)

    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value=RESUME),
        patch("bosshunter.ai.scorer.call_ai_text", side_effect=flaky_call),
    ):
        assert score_jobs(config) == (1, 0)

    assert calls["count"] == 2
    retry_states = [
        entry
        for event in events
        for entry in event.get("current", [])
        if entry.get("attempt") == 2
    ]
    assert retry_states, "重试期间进度应在途岗位 attempt 应为 2"
    assert retry_states[0]["max_attempts"] == 2
    assert events[-1]["completed"] == 1
    assert events[-1]["current"] == []


def test_failed_job_leaves_in_flight_and_counts_failed(tmp_path):
    """评分失败的岗位记 failed=1，不再出现在在途列表里，也不阻塞其余岗位。"""
    db_path = _seed_db(tmp_path, "failed.db", 2)
    events: list[dict] = []
    config = {
        "ai": {"scoring_concurrency": 1, "scoring_max_attempts": 2},
        "scoring": {"threshold": 60},
        "_workbench_score_progress": events.append,
    }
    outcomes = [
        ScoreOutcome(failure_detail="invalid JSON"),
        ScoreOutcome(result=scorer._validated_score_result(_score_response(85))),
    ]
    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value=RESUME),
        patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
        patch("bosshunter.ai.scorer._score_job_with_ai", side_effect=outcomes),
    ):
        assert score_jobs(config) == (1, 0)

    assert events[-1]["failed"] == 1
    assert events[-1]["scored"] == 1
    assert events[-1]["completed"] == 2
    assert events[-1]["current"] == []
    failed_ids = {
        entry["title"]
        for event in events
        for entry in event.get("current", [])
    }
    assert "AI Product Manager job-000" in failed_ids


def test_interrupted_batch_clears_in_flight(tmp_path):
    """用户停止后，最后一次进度上报在途为空，不残留正在打的岗位。"""
    db_path = _seed_db(tmp_path, "interrupt.db", 3)
    events: list[dict] = []
    stop_event = threading.Event()
    release = threading.Event()
    config = {
        "ai": {"scoring_concurrency": 3, "scoring_max_attempts": 2},
        "scoring": {"threshold": 60},
        "_workbench_stop_event": stop_event,
        "_workbench_score_progress": events.append,
    }

    def blocking_score(job, resume, config, max_attempts):
        release.wait(timeout=10)
        return ScoreOutcome(result=scorer._validated_score_result(_score_response(85)))

    result: dict = {}
    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value=RESUME),
        patch("bosshunter.ai.scorer.quick_score", return_value=(80, "通过")),
        patch("bosshunter.ai.scorer._score_job_with_ai", side_effect=blocking_score),
    ):
        worker = threading.Thread(target=lambda: result.update(
            {"value": score_jobs(config)}), daemon=True)
        worker.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(e.get("current") for e in events):
            time.sleep(0.05)
        assert any(e.get("current") for e in events), "停止前应先看到在途岗位"
        stop_event.set()
        worker.join(timeout=10)
        release.set()
        worker.join(timeout=10)

    assert worker.is_alive() is False
    assert events[-1]["current"] == []
    assert result["value"] == (0, 0)


def test_second_review_reports_review_state(tmp_path):
    """临界分二次复核期间，在途岗位显示二次复核状态。"""
    db_path = _seed_db(tmp_path, "review.db", 1)
    events: list[dict] = []
    config = {
        "ai": {
            **RELAY_AI,
            "scoring_concurrency": 1,
            "scoring_max_attempts": 2,
            "scoring_second_review": True,
        },
        "scoring": {"threshold": 60},
        "_workbench_score_progress": events.append,
    }

    def slow_review(prompt, config, max_tokens, **kwargs):
        time.sleep(0.2)
        return _score_response(72)

    with (
        patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)),
        patch("bosshunter.ai.scorer._load_resume", return_value=RESUME),
        patch("bosshunter.ai.scorer.call_ai_text", side_effect=slow_review),
    ):
        assert score_jobs(config) == (1, 0)

    review_states = [
        entry
        for event in events
        for entry in event.get("current", [])
        if entry.get("note") == "二次复核"
    ]
    assert review_states, "复核期间进度应在途岗位 note 应为 二次复核"
    assert events[-1]["current"] == []
