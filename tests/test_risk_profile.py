"""风控保护开关与档位（v22 可视化）：开关和档位必须真实控制底层参数。

同一开关开/关时节奏参数确实不同，档位切换时参数表确实换挡；
auto_pause_on_verify 关了就不再自动建 hold 等验证；
daily_cap 关了就是不限，改了数值就按新的走。
"""
import unittest
from unittest.mock import Mock, patch

from bosshunter import antibot
from bosshunter.antibot import (
    resolve_risk_profile,
    sample_batch_size,
    sample_rest_delay,
    should_stage_rest,
)


class ResolveRiskProfileTests(unittest.TestCase):
    def test_default_tier_is_standard(self):
        profile = resolve_risk_profile({})
        self.assertEqual(profile["risk_tier"], "standard")
        self.assertEqual((profile["micro_delay_min"], profile["micro_delay_max"]), (3.0, 8.0))

    def test_unknown_tier_falls_back_to_standard(self):
        profile = resolve_risk_profile({"risk_tier": "turbo"})
        self.assertEqual(profile["risk_tier"], "standard")
        self.assertEqual((profile["micro_delay_min"], profile["micro_delay_max"]), (3.0, 8.0))

    def test_tier_switch_changes_the_table(self):
        # 档位切换时参数表确实换挡：同一组键，三档三个值。
        steady = resolve_risk_profile({"risk_tier": "steady"})
        standard = resolve_risk_profile({"risk_tier": "standard"})
        fast = resolve_risk_profile({"risk_tier": "fast"})
        self.assertEqual((standard["micro_delay_min"], standard["micro_delay_max"]), (3.0, 8.0))
        self.assertEqual((fast["micro_delay_min"], fast["micro_delay_max"]), (1.0, 3.0))
        # 标准模式 = v9 上线以来的现状。
        self.assertEqual((standard["browse_duration_min"], standard["browse_duration_max"]), (15.0, 30.0))
        self.assertEqual((standard["platform_stagger_min"], standard["platform_stagger_max"]), (30.0, 120.0))
        self.assertEqual((standard["interval_min"], standard["interval_max"]), (60.0, 180.0))
        # 稳妥拉长、极速缩短：微停顿、浏览、错峰、间隔、休息都按档位走。
        self.assertGreater(steady["micro_delay_min"], standard["micro_delay_min"])
        self.assertGreater(standard["micro_delay_max"], fast["micro_delay_max"])
        # v47：批次间的休息按 lucky 定的三档——稳妥 3–5 分钟、
        # 标准 1–3 分钟、极速半分钟–1 分钟。
        self.assertEqual((steady["rest_min"], steady["rest_max"]), (180.0, 300.0))
        self.assertEqual((standard["rest_min"], standard["rest_max"]), (60.0, 180.0))
        self.assertEqual((fast["rest_min"], fast["rest_max"]), (30.0, 60.0))
        self.assertGreater(steady["rest_min"], standard["rest_min"])
        self.assertGreater(standard["rest_min"], fast["rest_min"])
        # 每批投几个三档一样，都是 3–5 个。
        for profile in (steady, standard, fast):
            self.assertEqual((profile["batch_size_min"], profile["batch_size_max"]), (3, 5))

    def test_batch_size_is_redrawn_between_three_and_five(self):
        # 每批个数真随机：抽 50 次都在 3–5 之间，且不全是一个值。
        seen = {sample_batch_size() for _ in range(50)}
        self.assertTrue(seen <= {3, 4, 5})
        self.assertGreater(len(seen), 1)

    def test_random_interval_off_really_stops_random_pauses(self):
        # 同一开关开/关，微停顿确实不同：关了就是 0，不再随机停顿。
        on = resolve_risk_profile({"risk_tier": "standard", "random_interval": True})
        off = resolve_risk_profile({"risk_tier": "standard", "random_interval": False})
        self.assertEqual((on["micro_delay_min"], on["micro_delay_max"]), (3.0, 8.0))
        self.assertEqual((off["micro_delay_min"], off["micro_delay_max"]), (0.0, 0.0))
        self.assertTrue(on["random_interval"])
        self.assertFalse(off["random_interval"])

    def test_switches_pass_through(self):
        profile = resolve_risk_profile({
            "risk_tier": "fast",
            "stage_rest": False,
            "auto_pause_on_verify": False,
            "daily_cap_enabled": False,
            "daily_cap": 50,
        })
        self.assertFalse(profile["stage_rest"])
        self.assertFalse(profile["auto_pause_on_verify"])
        self.assertFalse(profile["daily_cap_enabled"])
        self.assertEqual(profile["daily_cap"], 50)

    def test_daily_cap_defaults_to_120_and_prefers_new_field(self):
        self.assertEqual(resolve_risk_profile({})["daily_cap"], 120)
        # 老字段 daily_limit 只当兜底：新字段在就用新的。
        self.assertEqual(resolve_risk_profile({"daily_limit": 50})["daily_cap"], 50)
        self.assertEqual(resolve_risk_profile({"daily_limit": 50, "daily_cap": 80})["daily_cap"], 80)
        # 非法值回默认 120。
        self.assertEqual(resolve_risk_profile({"daily_cap": "abc"})["daily_cap"], 120)
        self.assertEqual(resolve_risk_profile({"daily_cap": 0})["daily_cap"], 1)

    def test_stage_rest_decision(self):
        self.assertTrue(should_stage_rest(5, 5, True))
        self.assertTrue(should_stage_rest(10, 5, True))
        self.assertFalse(should_stage_rest(4, 5, True))
        self.assertFalse(should_stage_rest(5, 5, False))
        self.assertFalse(should_stage_rest(0, 5, True))
        self.assertFalse(should_stage_rest(5, 0, True))

    def test_rest_delay_stays_in_range(self):
        for _ in range(20):
            delay = sample_rest_delay(60.0, 120.0)
            self.assertGreaterEqual(delay, 60.0)
            self.assertLessEqual(delay, 120.0)


class AutoPauseOnVerifyTests(unittest.TestCase):
    def _captcha_page(self, target_id, js):
        return {"risk": "captcha", "evidence": "captcha_element", "url": "https://example.com/verify"}

    def test_auto_pause_off_returns_captcha_without_hold_or_wait(self):
        # 关了就不自动暂停：不建 hold、不等用户验证，直接返回 captcha，
        # 但仍要提示用户（log 里有验证提示）。
        logs = []
        throttle = {
            "auto_pause_on_verify": False,
            "_db": Mock(),
            "_workbench_log": logs.append,
            "captcha_wait_timeout_minutes": 120,
        }
        with (
            patch("bosshunter.antibot.create_verification_hold") as create_hold,
            patch("bosshunter.antibot.wait_for_manual_verification") as wait,
        ):
            result = antibot.handle_page_risk("t1", "boss", throttle, evaluate_fn=self._captcha_page)
        self.assertEqual(result, "captcha")
        create_hold.assert_not_called()
        wait.assert_not_called()
        self.assertTrue(any("验证" in message for message in logs))

    def test_auto_pause_on_creates_hold_and_waits(self):
        # 开着（默认）还是老行为：建 hold、等用户验证完再继续。
        throttle = {
            "_db": Mock(),
            "_workbench_log": lambda message: None,
            "captcha_wait_timeout_minutes": 120,
        }
        calls = {"n": 0}

        def evaluate(target_id, js):
            calls["n"] += 1
            # 第一次是验证码页，等用户验证完，第二次复查已干净。
            if calls["n"] == 1:
                return {"risk": "captcha", "evidence": "captcha_element", "url": "https://example.com/verify"}
            return {"risk": None, "url": "https://example.com/job"}

        with (
            patch("bosshunter.antibot.create_verification_hold", return_value=7) as create_hold,
            patch("bosshunter.antibot.wait_for_manual_verification", return_value=True) as wait,
        ):
            result = antibot.handle_page_risk("t1", "boss", throttle, evaluate_fn=evaluate)
        self.assertIsNone(result)
        create_hold.assert_called_once()
        wait.assert_called_once()


class DailyCapMigrationTests(unittest.TestCase):
    def test_old_daily_limit_is_copied_to_daily_cap_on_load(self):
        import tempfile
        from pathlib import Path

        from bosshunter.config import load_config

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("throttle:\n  daily_limit: 50\n", encoding="utf-8")
            cfg = load_config(config_path)
        self.assertEqual(cfg["throttle"]["daily_cap"], 50)
        self.assertEqual(cfg["throttle"]["daily_limit"], 50)

    def test_risk_tier_is_normalized_to_standard_when_invalid(self):
        import tempfile
        from pathlib import Path

        from bosshunter.config import load_config, save_config

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.yaml"
            config_path.write_text("throttle:\n  risk_tier: turbo\n", encoding="utf-8")
            cfg = load_config(config_path)
            self.assertEqual(cfg["throttle"]["risk_tier"], "standard")
            cfg["throttle"]["risk_tier"] = "nope"
            save_config(cfg, config_path)
            self.assertEqual(load_config(config_path)["throttle"]["risk_tier"], "standard")


if __name__ == "__main__":
    unittest.main()
