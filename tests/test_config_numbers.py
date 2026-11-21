"""H-6: 后端保存配置时拦截数字框空值/非法值/范围外值。

前端逐框校验与保存前集中校验是第一道防线；直接调接口、旧脏数据、
其他保存入口绕过前端时，后端 POST /api/config 是最后一道防线。
"""

import pytest

from bosshunter.config import validate_config_numbers


def test_missing_fields_use_defaults_and_pass():
    validate_config_numbers({})
    validate_config_numbers({"profile": {"salary_min": 0, "salary_max": 0}})


def test_valid_config_passes():
    validate_config_numbers(
        {
            "platforms": {
                "boss": {"search": {"max_pages": 3}},
                "zhilian": {"search": {"max_pages": 1}},
            },
            "search": {"max_pages": 3},
            "scoring": {"max_candidates": 20, "threshold": 60},
            "collection": {
                "daily_search_page_limit": 60,
                "daily_detail_page_limit": 900,
                "max_consecutive_page_failures": 3,
                "collection_delay_multiplier": 1.5,
                "risk_pause_min_minutes": 5,
                "risk_pause_max_minutes": 10,
                "delivery_cooldown_min_minutes": 5,
                "delivery_cooldown_max_minutes": 15,
            },
            "safety": {"daily_platform_page_limit": 1100},
            "throttle": {"daily_cap": 120},
            "monitor": {
                "interval": 30,
                "initial_cooldown_minutes": 10,
                "max_conversations_per_cycle": 5,
                "max_consecutive_page_failures": 3,
                "max_resume_sends_per_cycle": 5,
            },
            "follow_up": {"interval_hours": 48},
            "profile": {"salary_min": 4, "salary_max": 7, "salary_ceil_ratio": 1.5, "posted_days": 7},
        }
    )


def test_zero_from_cleared_input_is_rejected():
    # 清空输入框 Number('')=0 曾静默存入；0 不在 1-10 内。
    with pytest.raises(ValueError, match="最多翻几页应在 1–10 之间"):
        validate_config_numbers({"platforms": {"boss": {"search": {"max_pages": 0}}}})


def test_null_from_nan_is_rejected():
    # 输入 e/- 得 NaN，JSON.stringify 后变成 null 存入。
    with pytest.raises(ValueError, match="一次最多看多少个请填写数字"):
        validate_config_numbers({"scoring": {"max_candidates": None}})


def test_non_integer_is_rejected():
    with pytest.raises(ValueError, match="请填写整数"):
        validate_config_numbers({"scoring": {"max_candidates": 2.5}})


def test_bool_is_not_a_number():
    with pytest.raises(ValueError, match="请填写数字"):
        validate_config_numbers({"monitor": {"interval": True}})


def test_min_greater_than_max_is_rejected():
    with pytest.raises(ValueError, match="我想要的工资：最少不能比最多大"):
        validate_config_numbers({"profile": {"salary_min": 10, "salary_max": 5}})
    with pytest.raises(ValueError, match="出问题先歇几分钟：最少不能比最多大"):
        validate_config_numbers(
            {"collection": {"risk_pause_min_minutes": 20, "risk_pause_max_minutes": 5}}
        )
    with pytest.raises(ValueError, match="找完先歇几分钟再投：最少不能比最多大"):
        validate_config_numbers(
            {"collection": {"delivery_cooldown_min_minutes": 30, "delivery_cooldown_max_minutes": 5}}
        )
