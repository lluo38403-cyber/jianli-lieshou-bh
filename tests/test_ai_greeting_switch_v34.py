"""v34 任务 2：智能定制打招呼开关（默认关闭）与降级链路。

覆盖点：
- 默认关闭在全链路一致（配置默认值、示例配置、手写缺键 dict 的兜底）。
- 字数目标 60-100 字（提示词、评审提示、风格检查阈值一致；300 字符硬上限不动）。
- 降级链路：开关关闭 / 本地规则 / 回落友好服务商调用失败或空返回时，
  走固定招呼语且队列继续；非回落服务商失败仍按原有暂停逻辑（显性上报，
  不静默回落）；无简历文件时（任务 3 附加项，用户要求）同样走固定
  招呼语回落、队列继续，不再阻断——见
  test_missing_resume_falls_back_to_fixed_and_queue_continues。
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from bosshunter.ai import greeter
from bosshunter.ai.credentials import AIRequestError
from bosshunter.config import DEFAULTS, load_config
from bosshunter.db import get_db, insert_job, update_job_status

FIXED = "您好，我想了解这个岗位。方便聊聊吗？"


def make_job(db, job_id):
    insert_job(db, {"id": job_id, "title": "内容运营", "company": "示例公司", "salary": "16-25K", "jd": "内容运营"})
    update_job_status(db, job_id, "approved")


def greeting_of(db, job_id):
    return db.execute("SELECT greeting FROM jobs WHERE id=?", (job_id,)).fetchone()[0]


def base_config(**overrides):
    config = {
        "profile": {"ai_greeting_enabled": True, "fixed_greeting": FIXED},
        "ai": {"service": "zhipu", "greeting_style_suggestions": False},
    }
    for section, values in overrides.items():
        config.setdefault(section, {}).update(values)
    return config


def test_config_default_is_off_and_example_config_is_off(tmp_path):
    assert DEFAULTS["profile"]["ai_greeting_enabled"] is False
    assert load_config(tmp_path / "missing.yaml")["profile"]["ai_greeting_enabled"] is False
    example = load_config(Path(__file__).resolve().parent.parent / "config.example.yaml")
    assert example["profile"]["ai_greeting_enabled"] is False
    # 示例配置开箱即用：开关关闭时固定文案不能为空，否则保存/启动即报错。
    assert example["profile"]["fixed_greeting"].strip()


def test_greeting_config_error_defaults_to_off():
    assert greeter.greeting_config_error({"profile": {}}) == "关闭 AI 招呼语后，请先填写固定招呼语"
    assert greeter.greeting_config_error({"profile": {"fixed_greeting": FIXED}}) == ""
    assert greeter.greeting_config_error({"profile": {"ai_greeting_enabled": True}}) == ""


def test_missing_switch_key_defaults_to_off_without_ai_call_or_resume_read(tmp_path):
    """缺键 dict（旧配置/手写 dict）以前会因 .get(..., True) 默认走 AI，现默认关闭。"""
    db_path = tmp_path / "jobs.db"
    db = get_db(db_path)
    make_job(db, "new")
    # 注意：profile 里故意不写 ai_greeting_enabled。
    config = {"profile": {"fixed_greeting": FIXED}, "ai": {"service": "zhipu"}}
    with patch.object(greeter, "_call_claude") as ai, patch.object(greeter, "_get_resume_summary") as resume:
        assert greeter.generate_greetings(config, ["new"], db_path) == 1
    ai.assert_not_called()
    resume.assert_not_called()
    assert greeting_of(db, "new") == FIXED
    db.close()


def test_switch_off_sends_fixed_greeting_with_no_ai_call_and_no_wait(tmp_path):
    """关闭状态：发送逻辑与原来一字不差——固定文案原样写库，不调 AI、不读简历。"""
    db_path = tmp_path / "jobs.db"
    db = get_db(db_path)
    make_job(db, "new")
    config = base_config(profile={"ai_greeting_enabled": False})
    with patch.object(greeter, "_call_claude") as ai, patch.object(greeter, "_get_resume_summary") as resume:
        assert greeter.generate_greetings(config, ["new"], db_path) == 1
    ai.assert_not_called()
    resume.assert_not_called()
    assert greeting_of(db, "new") == FIXED
    db.close()


def test_local_service_with_switch_on_still_uses_fixed_greeting(tmp_path):
    """AI 不可用（本地规则服务）：开关开着也不调 AI，走固定文案。"""
    db_path = tmp_path / "jobs.db"
    db = get_db(db_path)
    make_job(db, "new")
    config = base_config(ai={"service": "local"})
    with patch.object(greeter, "_call_claude") as ai, patch.object(greeter, "_get_resume_summary") as resume:
        assert greeter.generate_greetings(config, ["new"], db_path) == 1
    ai.assert_not_called()
    resume.assert_not_called()
    assert greeting_of(db, "new") == FIXED
    db.close()


@pytest.mark.parametrize("service", ["zhipu", "relay"])
def test_ai_timeout_falls_back_to_fixed_and_queue_continues(tmp_path, service):
    """生成超时（network  kind，含超时）时回落固定文案，同一批后续岗位继续生成。"""
    db_path = tmp_path / "jobs.db"
    db = get_db(db_path)
    make_job(db, "one")
    make_job(db, "two")
    config = base_config(ai={"service": service})
    with (
        patch.object(greeter, "_get_resume_summary", return_value="真实简历"),
        patch.object(greeter, "_call_claude", side_effect=AIRequestError("network", "AI 服务连接失败或超时")) as ai,
    ):
        assert greeter.generate_greetings(config, ["one", "two"], db_path) == 2
    assert ai.call_count == 2
    assert greeting_of(db, "one") == FIXED
    assert greeting_of(db, "two") == FIXED
    assert not config["_workbench_greeting_report"].get("pause_reason")
    db.close()


def test_ai_empty_response_falls_back_to_fixed_after_retry(tmp_path):
    """AI 无结果（空返回）：按配置重试一次后仍无结果，回落固定文案。"""
    db_path = tmp_path / "jobs.db"
    db = get_db(db_path)
    make_job(db, "new")
    config = base_config()
    with (
        patch.object(greeter, "_get_resume_summary", return_value="真实简历"),
        patch.object(greeter, "_call_claude", return_value=None) as ai,
    ):
        assert greeter.generate_greetings(config, ["new"], db_path) == 1
    assert ai.call_count == 2  # 首次 + 一次重试（greeting_max_attempts 默认 2）。
    assert greeting_of(db, "new") == FIXED
    assert not config["_workbench_greeting_report"].get("pause_reason")
    db.close()


def test_ai_failure_pauses_instead_of_falling_back_for_non_fallback_service(tmp_path):
    """非回落服务商（anthropic）调用失败：保持原有暂停逻辑，不静默回落。"""
    db_path = tmp_path / "jobs.db"
    db = get_db(db_path)
    make_job(db, "new")
    config = base_config(ai={"service": "anthropic"})
    with (
        patch.object(greeter, "_get_resume_summary", return_value="真实简历"),
        patch.object(greeter, "_call_claude", side_effect=AIRequestError("rate_limit", "AI 服务触发请求或 Token 频率限制")),
    ):
        assert greeter.generate_greetings(config, ["new"], db_path) == 0
    assert config["_workbench_greeting_report"].get("pause_reason")
    assert not greeting_of(db, "new")
    db.close()


def test_ai_empty_response_does_not_fall_back_for_non_fallback_service(tmp_path):
    db_path = tmp_path / "jobs.db"
    db = get_db(db_path)
    make_job(db, "new")
    config = base_config(ai={"service": "anthropic"})
    with (
        patch.object(greeter, "_get_resume_summary", return_value="真实简历"),
        patch.object(greeter, "_call_claude", return_value=None),
    ):
        assert greeter.generate_greetings(config, ["new"], db_path) == 0
    assert not greeting_of(db, "new")
    assert db.execute("SELECT COUNT(*) FROM history WHERE action='greeting_failed'").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize("service", ["zhipu", "anthropic"])
def test_missing_resume_falls_back_to_fixed_and_queue_continues(tmp_path, service):
    """无简历文件（任务 3 附加项，用户要求）：不再阻断——开关开着也
    自动无感降级走固定招呼语，整批照常生成、队列继续，不写
    pause_reason；只记一条通知说明智能定制没生效。不分服务商：
    这是“没简历可定制”的回落，不是“AI 调用失败”的回落，所以
    非回落服务商（anthropic）同样回落。"""
    db_path = tmp_path / "jobs.db"
    db = get_db(db_path)
    make_job(db, "one")
    make_job(db, "two")
    config = base_config(ai={"service": service})
    with (
        patch.object(greeter, "_get_resume_summary", return_value=""),
        patch.object(greeter, "_call_claude") as ai,
    ):
        assert greeter.generate_greetings(config, ["one", "two"], db_path) == 2
    ai.assert_not_called()
    assert greeting_of(db, "one") == FIXED
    assert greeting_of(db, "two") == FIXED
    assert not config["_workbench_greeting_report"].get("pause_reason")
    db.close()


def test_word_count_target_is_60_to_100_and_300_char_cap_unchanged():
    assert "60-100字" in greeter.GREETING_PROMPT
    assert "40-90字" not in greeter.GREETING_PROMPT
    assert "60-100字" in greeter.REVIEW_PROMPT
    assert "40-90字" not in greeter.REVIEW_PROMPT
    assert "全文不得超过300字符" in greeter.GREETING_PROMPT


def test_style_length_hint_uses_60_to_100_threshold():
    short = "您好，我做过内容运营，负责选题和社群互动，熟悉数据复盘。" + "熟悉用户增长，" * 10
    assert len(short) == 98
    assert not any("精简" in issue for issue in greeter._greeting_style_issues(short))
    long = "您好，我做过内容运营，负责选题和社群互动，熟悉数据复盘。" + "熟悉用户增长，" * 11
    assert len(long) == 105
    assert any("60-100字" in issue for issue in greeter._greeting_style_issues(long))
    # 300 字符硬上限的提示由 generate_greetings 另行附加（风格检查本身只给字数建议）。
    assert any("60-100字" in issue for issue in greeter._greeting_style_issues("内" * 301))
