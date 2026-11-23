"""Regression coverage for evidence lost or misrepresented before AI scoring."""

import json
from unittest.mock import patch

import pytest

from bosshunter.ai import scorer
from bosshunter.ai.prefilter import quick_score


def _job(salary="12-15K"):
    return {
        "id": "evidence-job",
        "title": "品牌运营",
        "company": "示例公司",
        "salary": salary,
        "city": "北京",
        "experience": "3-5年",
        "jd": "负责品牌策划、传播与项目运营。",
    }


def test_primary_and_review_receive_middle_evidence_and_city_context():
    resume = "既往工作记录。" * 400 + "独立负责客户方案册与产品社区运营。" + "其他经历。" * 200
    job = _job()
    job["jd"] = "日常岗位职责。" * 250 + "须具备产品社区运营实践。" + "协作事项。" * 200
    config = {
        "profile": {"target_cities": ["北京", "上海"], "salary_min": 13, "salary_max": 25},
        "ai": {"scoring_second_review": True},
    }
    response = json.dumps({
        **{key: {"score": score, "evidence": "有相关实践"} for key, score in zip(
            scorer.COMPONENT_LIMITS, (30, 19, 10, 7, 8)
        )},
        "reason": "职责相关，部分要求待确认",
        "caps": [],
        "hard_gaps": [],
    }, ensure_ascii=False)
    with patch.object(scorer, "_call_claude", return_value=response) as call_ai:
        outcome = scorer._score_job_with_ai(job, resume, config, 2)

    assert outcome.result.reviewed is True
    assert call_ai.call_count == 2
    for call in call_ai.call_args_list:
        prompt = call.args[0]
        assert resume in prompt
        assert job["jd"] in prompt
        assert "目标城市：北京、上海" in prompt
        assert "工作城市：北京" in prompt
        assert "月薪交集为13K-15K" in prompt


@pytest.mark.parametrize("salary,minimum,maximum,ratio,expected,passes", [
    ("12-15K", 13, 25, 1, "月薪交集为13K-15K", True),
    ("8-13K", 13, 25, 1, "月薪交集为13K-13K", True),
    ("25-50K·15薪", 13, 25, 1, "月薪交集为25K-25K", True),
    ("20-30K", 13, 25, 1, "月薪交集为20K-25K", True),
    ("8-12K", 13, 25, 1, "上限低于期望下限，无交集", False),
    ("26-50K", 13, 25, 1, "下限超过候选人设置的放宽上限，无交集", False),
    ("26-50K", 13, 25, 1.5, "在候选人设置的放宽范围内", True),
    ("面议", 13, 25, 1, "薪资条件待确认", True),
    ("15K", 0, 0, 1, "未设置薪资限制", True),
    ("12-15K", 13, 0, 1, "月薪交集为13K-15K", True),
    ("12-15K", 0, 13, 1, "月薪交集为12K-13K", True),
])
def test_salary_evidence_agrees_with_prefilter(salary, minimum, maximum, ratio, expected, passes):
    job = _job(salary)
    config = {"profile": {
        "salary_min": minimum, "salary_max": maximum, "salary_ceil_ratio": ratio,
        "filter_unparsed_salary": False,
    }}

    prompt = scorer._build_scoring_prompt(job, "候选人简历", config)

    assert expected in prompt
    assert (quick_score(job, config)[0] > 0) is passes


def _structured_components():
    return {"core_duties": 35, "transferable_evidence": 21, "hard_requirements": 7, "tools_industry": 8, "practical_fit": 10}


def test_structured_reason_is_three_lines_with_total_and_no_repeated_gap():
    # v46：截图里 81 分岗位的理由是一整段密文，还把“会 p 图”说两遍。
    # 新格式三行：第 1 行分项 + 总分，第 2 行理由，第 3 行缺失（理由里
    # 提过的不重复写）。
    score, raw, reason = scorer._format_structured_reason(
        _structured_components(),
        (),
        "运营职责与经历高度直接匹配，薪资城市均契合；唯一缺口是JD明确要求的会p图无证据。",
        "会p图（PS等图像处理能力）无简历证据",
    )
    assert score == 81
    assert raw == 81
    assert "|" not in reason
    lines = reason.split("\n")
    assert len(lines) == 2
    assert lines[0] == "AI评分：职责35/40 · 证据21/25 · 硬要求7/15 · 工具行业8/10 · 实际条件10/10 = 81分"
    assert lines[1].startswith("运营职责与经历高度直接匹配")


def test_structured_reason_keeps_missing_line_when_reason_does_not_mention_it():
    score, _, reason = scorer._format_structured_reason(
        _structured_components(),
        (),
        "运营职责与经历高度直接匹配，薪资城市均契合。",
        "会p图（PS等图像处理能力）无简历证据",
    )
    assert score == 81
    lines = reason.split("\n")
    assert len(lines) == 3
    assert lines[2] == "缺失：会p图（PS等图像处理能力）无简历证据"


def test_structured_reason_reviewed_prefix_and_cap_note_on_first_line():
    score, raw, reason = scorer._format_structured_reason(
        {"core_duties": 40, "transferable_evidence": 25, "hard_requirements": 15, "tools_industry": 10, "practical_fit": 10},
        ("technical_required",),
        "技术栈高度匹配。",
        "",
        reviewed=True,
    )
    assert score == 55
    assert raw == 100
    lines = reason.split("\n")
    assert lines[0] == "AI评分（二次复核后）：职责40/40 · 证据25/25 · 硬要求15/15 · 工具行业10/10 · 实际条件10/10 = 55分；硬技术缺口封顶55"
    assert lines[1] == "技术栈高度匹配。"
