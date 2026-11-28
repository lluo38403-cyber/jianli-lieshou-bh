"""v54 图2-6：岗位方向多选——标题门 + 规则打分按方向过滤。

回归：选了“国内电商运营”后，“电商财务/美工”进不了库、打分也
过不了线（只含宽泛词“电商”不算命中）；“淘宝运营”正常进库、
正常加分；没选方向时老行为不变。
"""

import unittest

from bosshunter.ai.prefilter import (
    normalize_role_picks,
    role_pick_keywords,
    rule_score,
    title_matches_role_picks,
)

PICKS = [{"label": "国内电商运营", "keywords": ["淘宝运营", "天猫运营", "京东运营", "拼多多运营", "电商运营"]}]
JOB_BASE = {"salary": "15-25K", "city": "上海", "education": "本科", "jd": "负责店铺日常运营。"}
CONFIG_BASE = {
    "profile": {"target_roles": ["电商运营"], "target_cities": ["上海"], "education": "本科", "salary_min": 10, "salary_max": 30},
    "search": {"keywords": ["电商"], "role_picks": PICKS},
}


def _job(title, **overrides):
    job = dict(JOB_BASE, title=title)
    job.update(overrides)
    return job


class NormalizeRolePicksTests(unittest.TestCase):
    def test_clean_picks(self):
        self.assertEqual(normalize_role_picks({"search": {"role_picks": PICKS}}), PICKS)
        self.assertEqual(role_pick_keywords({"search": {"role_picks": PICKS}}), PICKS[0]["keywords"])

    def test_dirty_entries_are_skipped_not_fatal(self):
        config = {"search": {"role_picks": [
            {"label": "国内电商运营", "keywords": ["淘宝运营", "", "  "]},
            {"label": "", "keywords": ["客服"]},
            {"label": "空方向", "keywords": []},
            "不是字典",
            None,
        ]}}
        self.assertEqual(normalize_role_picks(config), [{"label": "国内电商运营", "keywords": ["淘宝运营"]}])

    def test_missing_picks_means_no_limit(self):
        self.assertEqual(normalize_role_picks({}), [])
        self.assertEqual(normalize_role_picks({"search": {}}), [])
        self.assertTrue(title_matches_role_picks("电商财务", []))


class TitleMatchesRolePicksTests(unittest.TestCase):
    def test_matching_title_passes(self):
        self.assertTrue(title_matches_role_picks("淘宝运营", PICKS))
        self.assertTrue(title_matches_role_picks("高级天猫运营专员", PICKS))

    def test_duty_mismatch_is_blocked(self):
        # 只含宽泛词“电商”不算：电商财务/美工不是运营岗。
        self.assertFalse(title_matches_role_picks("电商财务", PICKS))
        self.assertFalse(title_matches_role_picks("电商美工", PICKS))

    def test_jd_match_does_not_count(self):
        # JD 里写“淘宝运营”不代表岗位就是运营岗——只看标题。
        self.assertFalse(title_matches_role_picks("电商财务", PICKS))


class RuleScoreRolePickTests(unittest.TestCase):
    def test_duty_mismatch_scores_zero_keywords_and_caps_at_55(self):
        score, reason = rule_score(_job("电商财务"), CONFIG_BASE)
        self.assertEqual(score, 55)
        self.assertIn("关键词0分", reason)

    def test_matching_pick_title_scores_full(self):
        score, _reason = rule_score(_job("淘宝运营"), CONFIG_BASE)
        self.assertEqual(score, 70)

    def test_no_picks_keeps_old_behavior(self):
        config = {"profile": CONFIG_BASE["profile"], "search": {"keywords": ["电商"]}}
        score, _reason = rule_score(_job("电商财务"), config)
        self.assertEqual(score, 70)


if __name__ == "__main__":
    unittest.main()
