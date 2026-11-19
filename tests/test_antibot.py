"""Tests for the anti-detection layer (bosshunter/antibot.py).

Covers: Gaussian micro-delays, human-like browsing, the captcha detector JS
(run through node when available), verification holds, the per-platform
circuit breaker, per-platform daily quotas, platform grouping/stagger, and
the local-calendar-day counting fix.
"""

import json
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bosshunter import antibot
from bosshunter.config import DEFAULTS
from bosshunter.db import (
    add_history,
    breaker_is_suspended,
    breaker_record_failure,
    breaker_record_success,
    breaker_suspended_platforms,
    count_sent_today,
    count_sent_today_by_platform,
    create_verification_hold,
    get_db,
    get_open_verification_hold,
    resolve_verification_hold,
)


def _seed_job(conn, job_id, platform, status="ready", greeting="你好"):
    conn.execute(
        "INSERT INTO jobs (id, title, company, source_platform, status, greeting)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, "测试岗位", "测试公司", platform, status, greeting),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. Gaussian micro-delays
# ---------------------------------------------------------------------------

def test_micro_delay_stays_within_3_to_8_seconds():
    rng = random.Random(7)
    samples = [antibot.sample_micro_delay(rng=rng) for _ in range(300)]
    assert all(3.0 <= value <= 8.0 for value in samples)
    mean = sum(samples) / len(samples)
    assert 5.0 <= mean <= 6.0, f"mean {mean} drifted from 5.5"


def test_micro_pause_uses_gaussian_delay_and_reports_stop():
    delays = []
    rng = random.Random(11)
    stopped = antibot.micro_pause(
        rng=rng, sleep_fn=lambda seconds: delays.append(seconds) or False
    )
    assert stopped is False
    assert len(delays) == 1 and 3.0 <= delays[0] <= 8.0

    stop_event = Event()
    stop_event.set()
    assert antibot.micro_pause(stop_event=stop_event) is True


def test_stagger_delay_stays_within_30_to_120_seconds():
    rng = random.Random(3)
    samples = [antibot.sample_stagger_delay(rng=rng) for _ in range(200)]
    assert all(30.0 <= value <= 120.0 for value in samples)
    mean = sum(samples) / len(samples)
    assert 65.0 <= mean <= 85.0, f"mean {mean} drifted from 75"


# ---------------------------------------------------------------------------
# 2. Human-like browsing
# ---------------------------------------------------------------------------

def test_human_browse_scrolls_in_random_steps_within_budget():
    clock = {"now": 1000.0}
    steps = []

    def fake_sleep(seconds):
        clock["now"] += seconds
        return False

    stopped = antibot.human_browse(
        "target-1",
        None,
        min_s=15.0,
        max_s=30.0,
        scroll_fn=lambda target_id, y: steps.append(y),
        rng=random.Random(42),
        sleep_fn=fake_sleep,
        clock_fn=lambda: clock["now"],
    )
    assert stopped is False
    assert len(steps) >= 2, "expected several scroll steps"
    assert all(isinstance(step, int) and -400 <= step <= 700 for step in steps)
    assert len(set(steps)) > 1, "steps must vary, not repeat one value"
    assert 1015.0 <= clock["now"] <= 1030.0, "must spend the sampled budget"


def test_human_browse_stops_on_request():
    stop_event = Event()
    stop_event.set()
    calls = []
    stopped = antibot.human_browse(
        "target-1",
        stop_event,
        min_s=15.0,
        max_s=30.0,
        scroll_fn=lambda target_id, y: calls.append(y),
        rng=random.Random(1),
    )
    assert stopped is True
    assert calls == []


# ---------------------------------------------------------------------------
# 3. Captcha detector JS (through node when available)
# ---------------------------------------------------------------------------

_NODE_HARNESS = """
const fs = require('fs');
const fixture = JSON.parse(process.argv[2]);
const js = fs.readFileSync(process.argv[3], 'utf8');
global.document = {
  body: { innerText: fixture.text || '' },
  title: fixture.title || '',
  querySelector: (sel) => fixture.hasCaptchaEl ? { tagName: 'DIV' } : null,
};
global.location = { href: fixture.url || 'https://www.zhipin.com/job_detail/abc.html' };
console.log(eval(js));
"""


def _run_detector(fixture):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to evaluate the detector JS")
    with tempfile.TemporaryDirectory() as tmp:
        js_path = Path(tmp) / "detector.js"
        harness_path = Path(tmp) / "harness.js"
        js_path.write_text(antibot.CAPTCHA_DETECT_JS, encoding="utf-8")
        harness_path.write_text(_NODE_HARNESS, encoding="utf-8")
        out = subprocess.run(
            [node, str(harness_path), json.dumps(fixture), str(js_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout.strip())


def test_detector_flags_captcha_element_and_url():
    assert _run_detector({"hasCaptchaEl": True})["risk"] == "captcha"
    result = _run_detector({"url": "https://www.zhipin.com/captcha?x=1"})
    assert result["risk"] == "captcha"
    assert result["evidence"] == "captcha_url"


def test_detector_ignores_clean_job_page():
    result = _run_detector({
        "text": "高级 Java 工程师\n立即沟通\n投递简历\n公司介绍",
    })
    assert result["risk"] is None


def test_detector_flags_captcha_page_text_only_without_action_buttons():
    result = _run_detector({"text": "请完成安全验证\n拖动滑块完成验证"})
    assert result["risk"] == "captcha"
    assert result["evidence"] == "captcha_page"


def test_detector_flags_rate_limit_and_login_and_blocked():
    assert _run_detector({"text": "操作频繁，请稍后再试"})["risk"] == "rate_limit"
    assert _run_detector({"url": "https://www.zhipin.com/web/user/login"})["risk"] == "login_required"
    assert _run_detector({"title": "403 Forbidden"})["risk"] == "blocked"


def test_detector_does_not_flag_job_description_mentioning_verification():
    # A job description may mention 验证码; with action buttons present it is not a captcha page.
    result = _run_detector({
        "text": "岗位要求：熟悉短信验证码接入\n立即沟通\n投递简历",
    })
    assert result["risk"] is None


# ---------------------------------------------------------------------------
# 4. Verification holds
# ---------------------------------------------------------------------------

def _hold_db(tmp_path):
    return get_db(tmp_path / "holds.db")


def test_hold_create_resolve_roundtrip(tmp_path):
    db = _hold_db(tmp_path)
    hold_id = create_verification_hold(db, "boss", url="https://x/verify", evidence="captcha_url")
    opened = get_open_verification_hold(db)
    assert opened is not None and opened["id"] == hold_id and opened["platform"] == "boss"
    assert resolve_verification_hold(db, hold_id) is True
    assert resolve_verification_hold(db, hold_id) is False  # second resolve is a no-op
    assert get_open_verification_hold(db) is None
    db.close()


def test_wait_for_manual_verification_resolves_from_another_thread(tmp_path):
    db = _hold_db(tmp_path)
    hold_id = create_verification_hold(db, "zhilian")
    threading.Timer(0.2, lambda: resolve_verification_hold(get_db(tmp_path / "holds.db"), hold_id)).start()
    assert antibot.wait_for_manual_verification(db, hold_id, poll_s=0.05, timeout_s=5.0) is True
    db.close()


def test_wait_for_manual_verification_times_out_and_stops(tmp_path):
    db = _hold_db(tmp_path)
    hold_id = create_verification_hold(db, "boss")
    assert antibot.wait_for_manual_verification(db, hold_id, poll_s=0.05, timeout_s=0.2) is False
    stop_event = Event()
    stop_event.set()
    assert antibot.wait_for_manual_verification(db, hold_id, stop_event, poll_s=0.05, timeout_s=5.0) is False
    db.close()


def test_handle_page_risk_hands_over_and_resumes(tmp_path):
    db = _hold_db(tmp_path)
    calls = {"n": 0}

    def fake_evaluate(target_id, js):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"risk": "captcha", "evidence": "captcha_url", "url": "https://x/verify"})
        return json.dumps({"risk": None, "url": "https://www.zhipin.com/job_detail/abc"})

    threading.Timer(
        0.2,
        lambda: resolve_verification_hold(get_db(tmp_path / "holds.db"), 1),
    ).start()
    result = antibot.handle_page_risk(
        "target-1", "boss",
        {"_db": db, "_captcha_wait_timeout_s": 5.0},
        evaluate_fn=fake_evaluate,
    )
    assert result is None  # clear after the user resolved it
    assert calls["n"] >= 2
    assert get_open_verification_hold(db) is None
    db.close()


def test_handle_page_risk_gives_up_after_three_holds(tmp_path):
    db = _hold_db(tmp_path)
    stop_resolver = Event()

    def resolver():
        # Pretend the user keeps pressing Continue while the page stays blocked.
        # Each thread needs its own SQLite connection.
        own_db = get_db(tmp_path / "holds.db")
        try:
            while not stop_resolver.is_set():
                hold = get_open_verification_hold(own_db)
                if hold:
                    resolve_verification_hold(own_db, hold["id"])
                time.sleep(0.05)
        finally:
            own_db.close()

    thread = threading.Thread(target=resolver, daemon=True)
    thread.start()
    try:
        def always_captcha(target_id, js):
            return json.dumps({"risk": "captcha", "evidence": "captcha_element", "url": "https://x/verify"})

        result = antibot.handle_page_risk(
            "target-1", "boss",
            {"_db": db, "_captcha_wait_timeout_s": 5.0},
            evaluate_fn=always_captcha,
        )
    finally:
        stop_resolver.set()
    assert result == "captcha"
    rows = db.execute("SELECT COUNT(*) AS cnt FROM verification_holds").fetchone()
    assert rows["cnt"] == 3
    db.close()


def test_handle_page_risk_without_db_returns_captcha(tmp_path):
    def always_captcha(target_id, js):
        return json.dumps({"risk": "captcha", "evidence": "captcha_element", "url": ""})

    assert antibot.handle_page_risk("t", "boss", {}, evaluate_fn=always_captcha) == "captcha"


# ---------------------------------------------------------------------------
# 5. Circuit breaker
# ---------------------------------------------------------------------------

def test_breaker_suspends_after_three_consecutive_failures(tmp_path):
    db = _hold_db(tmp_path)
    breaker = antibot.CircuitBreaker(db, max_failures=3)
    assert breaker.record_failure("boss") is False
    assert breaker.record_failure("boss") is False
    assert breaker.record_failure("boss") is True  # third failure suspends
    assert breaker.is_suspended("boss") is True
    assert breaker.suspended_platforms() == ["boss"]
    assert breaker.is_suspended("zhilian") is False
    db.close()


def test_breaker_success_resets_the_counter(tmp_path):
    db = _hold_db(tmp_path)
    breaker = antibot.CircuitBreaker(db, max_failures=3)
    breaker.record_failure("boss")
    breaker.record_failure("boss")
    breaker.record_success("boss")
    assert breaker.record_failure("boss") is False
    assert breaker.record_failure("boss") is False
    assert breaker.is_suspended("boss") is False
    db.close()


def test_breaker_suspension_expires_at_end_of_day(tmp_path):
    db = _hold_db(tmp_path)
    breaker_record_failure(db, "boss", 3)
    breaker_record_failure(db, "boss", 3)
    breaker_record_failure(db, "boss", 3)
    assert breaker_is_suspended(db, "boss") is True
    past = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("UPDATE platform_breakers SET suspended_until = ? WHERE platform = 'boss'", (past,))
    db.commit()
    assert breaker_is_suspended(db, "boss") is False
    assert breaker_suspended_platforms(db) == []
    db.close()


# ---------------------------------------------------------------------------
# 6. Per-platform daily quotas + local-day counting
# ---------------------------------------------------------------------------

def test_per_platform_quota_counts(tmp_path):
    db = _hold_db(tmp_path)
    _seed_job(db, "j1", "boss")
    _seed_job(db, "j2", "boss")
    _seed_job(db, "j3", "zhilian")
    add_history(db, "j1", "sent")
    add_history(db, "j2", "sent")
    add_history(db, "j3", "sent")
    assert count_sent_today_by_platform(db, "boss") == 2
    assert count_sent_today_by_platform(db, "zhilian") == 1
    assert count_sent_today_by_platform(db, "liepin") == 0
    assert count_sent_today(db) == 3
    db.close()


def test_local_day_counting_ignores_utc_boundary(tmp_path):
    """A send at 00:30 local counts as today even when it is yesterday in UTC
    (and vice versa when the local offset is negative). Rows are stored the
    way SQLite CURRENT_TIMESTAMP stores them: as a UTC instant string."""
    db = _hold_db(tmp_path)
    _seed_job(db, "j1", "boss")
    _seed_job(db, "j2", "boss")
    now_local = datetime.now().astimezone()
    offset = now_local.utcoffset() or timedelta(0)
    if offset == timedelta(0):
        pytest.skip("no UTC/local boundary exists in a UTC timezone")
    if offset > timedelta(0):
        # 00:30 local today = yesterday in UTC.
        local_ts = now_local.replace(hour=0, minute=30, second=0, microsecond=0)
        utc_ts = local_ts - offset
        db.execute(
            "INSERT INTO history (job_id, action, created_at) VALUES (?, 'sent', ?)",
            ("j1", utc_ts.strftime("%Y-%m-%d %H:%M:%S")),
        )
        db.commit()
        assert count_sent_today(db) == 1
        old_utc = db.execute(
            "SELECT COUNT(*) AS cnt FROM history WHERE action='sent' AND date(created_at)=date('now')"
        ).fetchone()["cnt"]
        assert old_utc == 0, "old UTC query must miss the local-early-morning send"
    else:
        # 23:30 local yesterday = today in UTC.
        local_ts = (now_local - timedelta(days=1)).replace(hour=23, minute=30, second=0, microsecond=0)
        utc_ts = local_ts - offset
        db.execute(
            "INSERT INTO history (job_id, action, created_at) VALUES (?, 'sent', ?)",
            ("j1", utc_ts.strftime("%Y-%m-%d %H:%M:%S")),
        )
        db.commit()
        assert count_sent_today(db) == 0
        old_utc = db.execute(
            "SELECT COUNT(*) AS cnt FROM history WHERE action='sent' AND date(created_at)=date('now')"
        ).fetchone()["cnt"]
        assert old_utc == 1, "old UTC query must wrongly count the local-yesterday send"
    db.close()


def test_platform_daily_limits_defaults():
    limits = antibot.platform_daily_limits({})
    assert limits == {"boss": 80, "zhilian": 80, "51job": 80, "liepin": 80}
    limits = antibot.platform_daily_limits({"daily_limit_per_platform": {"boss": 10}})
    assert limits["boss"] == 10 and limits["zhilian"] == 80


def test_config_defaults_carry_the_new_throttle_keys():
    throttle = DEFAULTS["throttle"]
    assert throttle["daily_limit_per_platform"]["boss"] == 80
    assert (throttle["micro_delay_min"], throttle["micro_delay_max"]) == (3, 8)
    assert (throttle["platform_stagger_min"], throttle["platform_stagger_max"]) == (30, 120)
    assert throttle["breaker_max_consecutive_failures"] == 3
    assert throttle["captcha_wait_timeout_minutes"] == 120


# ---------------------------------------------------------------------------
# 7. Grouping + stagger
# ---------------------------------------------------------------------------

def test_group_jobs_by_platform_keeps_collection_order():
    jobs = [
        {"id": "a", "source_platform": "zhilian"},
        {"id": "b", "source_platform": "boss"},
        {"id": "c", "source_platform": "boss"},
        {"id": "d", "source_platform": "liepin"},
        {"id": "e", "source_platform": "zhilian"},
    ]
    groups = antibot.group_jobs_by_platform(jobs)
    assert [platform for platform, _ in groups] == ["boss", "zhilian", "liepin"]
    assert [job["id"] for _, items in groups for job in items] == ["b", "c", "a", "e", "d"]


def test_webdriver_flag_helper_reports_undefined_as_false():
    def fake_evaluate(target_id, js):
        return json.dumps({"webdriver": None})

    assert antibot.check_webdriver_flag(fake_evaluate, "t") is None

    def fake_true(target_id, js):
        return json.dumps({"webdriver": True})

    assert antibot.check_webdriver_flag(fake_true, "t") is True


def test_stealth_launch_arg_present():
    assert "--disable-blink-features=AutomationControlled" in antibot.CHROME_STEALTH_ARGS


def test_stealth_arg_reaches_every_chrome_launch_path(tmp_path):
    """反检测第一层要落到用户真正用的启动路径上：exe 本身不拉 Chrome，
    用户按启动脚本、QUICKSTART 命令或面板诊断提示手动起 Chrome——
    三处都要带上抹除参数，缺一处就等于这层没上线。"""
    repo = Path(__file__).resolve().parents[1]
    flag = "--disable-blink-features=AutomationControlled"
    assert flag in (repo / "scripts" / "windows" / "start_bosshunter.ps1").read_text(encoding="utf-8")
    assert flag in (repo / "scripts" / "macos" / "start_bosshunter.sh").read_text(encoding="utf-8")
    assert flag in (repo / "src" / "bosshunter" / "browser" / "diagnostics.py").read_text(encoding="utf-8")
    quickstart = (repo / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")
    assert quickstart.count(flag) >= 3, "QUICKSTART 三条手动启动命令都要带抹除参数"
