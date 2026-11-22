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


# ---------------- v38：默认关可验证 + 固定文案用户可配 + 当前生效文案 ----------------
#
# 根因（2026-11-22 实测复核）：代码三处默认全是关——config.py DEFAULTS、
# config.example.yaml、ConfigPage/AdminPage 的 `?? false`；也没有任何
# 写 true 的路径（缓存、localStorage、启动写回都没有）。开关显示开，
# 只能是配置文件里已经存了 true（旧版上游默认曾是 True，见 a9e72a5；
# 或某次在 /admin 手动开过），之后每次加载都以文件为准。v38 不静默
# 重置已存值（分不清“特意开的”和“旧默认带入的”），而是把三件事钉死：
# 全新默认一定关、手动开关保存重读不变、开关关时展示与发送严格走
# 当前固定文案。

from bosshunter.ai.greeter import DEFAULT_FIXED_GREETING, effective_greeting
from bosshunter.web import server as web_server


def test_v38_fresh_default_off_and_manual_toggle_persists_through_save_reload(tmp_path):
    config_path = tmp_path / "config.yaml"
    # 全新默认：文件不存在 -> 关，固定文案是内置默认句。
    fresh = load_config(config_path)
    assert fresh["profile"]["ai_greeting_enabled"] is False
    assert fresh["profile"]["fixed_greeting"] == DEFAULT_FIXED_GREETING

    with patch.object(web_server, "CONFIG_PATH", config_path):
        # 手动开 -> 保存 -> 重读仍为开，固定文案一起落盘。
        opened = web_server._sanitize_config_for_write(
            {"profile": {"ai_greeting_enabled": True, "fixed_greeting": "新固定文案"}}
        )
        web_server._write_config(opened)
        reloaded = load_config(config_path)
        assert reloaded["profile"]["ai_greeting_enabled"] is True
        assert reloaded["profile"]["fixed_greeting"] == "新固定文案"
        # GET 回填同样返回开关与固定文案（偏好仍脱敏）。
        redacted = web_server._redact_config_for_response(reloaded)
        assert redacted["profile"]["ai_greeting_enabled"] is True
        assert redacted["profile"]["fixed_greeting"] == "新固定文案"
        assert "greeting_preference" not in redacted["profile"]

        # 手动关 -> 保存 -> 重读仍为关；没提交固定文案时沿用已存值，不丢。
        closed = web_server._sanitize_config_for_write({"profile": {"ai_greeting_enabled": False}})
        web_server._write_config(closed)
        reloaded = load_config(config_path)
        assert reloaded["profile"]["ai_greeting_enabled"] is False
        assert reloaded["profile"]["fixed_greeting"] == "新固定文案"


def test_v38_user_can_edit_fixed_greeting_and_bad_type_falls_back_to_saved(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "profile:\n  ai_greeting_enabled: false\n  fixed_greeting: 已存固定文案\n",
        encoding="utf-8",
    )
    with patch.object(web_server, "CONFIG_PATH", config_path):
        # 用户提交的字符串固定文案生效。
        cleaned = web_server._sanitize_config_for_write(
            {"profile": {"ai_greeting_enabled": False, "fixed_greeting": "用户新写的固定文案"}}
        )
        assert cleaned["profile"]["fixed_greeting"] == "用户新写的固定文案"
        # 类型不对（数字）时沿用已存值，不静默写坏。
        cleaned = web_server._sanitize_config_for_write(
            {"profile": {"ai_greeting_enabled": False, "fixed_greeting": 123}}
        )
        assert cleaned["profile"]["fixed_greeting"] == "已存固定文案"


@pytest.mark.parametrize(
    "enabled,stored,fixed,expected",
    [
        # 开关关：哪怕库里存着定制文案，也展示/发送当前固定文案。
        (False, "旧定制文案", "当前固定文案", "当前固定文案"),
        # 开关关、无存量：固定文案。
        (False, "", "当前固定文案", "当前固定文案"),
        # 开关开、有定制文案：用定制文案。
        (True, "旧定制文案", "当前固定文案", "旧定制文案"),
        # 开关开、还没生成：回退固定文案，不阻塞。
        (True, "", "当前固定文案", "当前固定文案"),
        # 固定文案被清空：退回内置默认句，永不返回空串。
        (False, "", "", DEFAULT_FIXED_GREETING),
    ],
)
def test_v38_effective_greeting_follows_switch_and_current_fixed_text(enabled, stored, fixed, expected):
    config = {"profile": {"ai_greeting_enabled": enabled, "fixed_greeting": fixed}}
    assert effective_greeting(config, {"greeting": stored}) == expected


def test_v38_confirmed_greeting_survives_switch_off():
    # 用户逐条确认过的文案（评审核对选 optimized + reviewed_at）是明确选择，
    # 开关关着也不改写它；展示与发送都以它为准。
    config = {"profile": {"ai_greeting_enabled": False, "fixed_greeting": "当前固定文案"}}
    job = {
        "greeting": "优化稿：我做过三年内容运营。",
        "greeting_selection": "optimized",
        "greeting_reviewed_at": "2026-11-22 10:00:00",
    }
    assert effective_greeting(config, job) == "优化稿：我做过三年内容运营。"


def test_v38_generated_but_unconfirmed_greeting_follows_switch_off():
    # 只生成、没确认过的文案（selection='generated'、reviewed_at 为空）不是
    # 明确选择：开关一关就改走当前固定文案，不读生成时的陈旧快照。
    config = {"profile": {"ai_greeting_enabled": False, "fixed_greeting": "当前固定文案"}}
    job = {
        "greeting": "旧定制文案",
        "greeting_selection": "generated",
        "greeting_reviewed_at": None,
    }
    assert effective_greeting(config, job) == "当前固定文案"
