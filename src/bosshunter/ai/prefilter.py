"""Pre-filter module - hard filtering before LLM evaluation."""

import re
from datetime import datetime, timezone

from bosshunter.employment import classify_employment, internship_rejection

from bosshunter.job_filters import matching_blocked_company, matching_deal_breaker


_ANONYMOUS_COMPANY_PATTERN = re.compile(
    r"^(?:[\u4e00-\u9fff]{2,4})?某.+(?:公司|企业|集团)$"
)


def quick_score(job: dict, config: dict) -> tuple[int, str]:
    """Apply hard filters before LLM scoring."""
    reason = internship_rejection(job, config)
    if reason:
        return 0, reason
    profile = config.get("profile", {})
    deal_breakers = profile.get("deal_breakers", [])
    jd_deal_breakers = profile.get("jd_deal_breakers", [])
    blocked_companies = profile.get("blocked_companies", [])
    title = job.get("title") or ""
    jd = job.get("jd") or ""
    company = str(job.get("company") or "").strip()

    blocked_company = matching_blocked_company(company, blocked_companies)
    if blocked_company:
        return 0, f"触发公司屏蔽: {blocked_company}"

    if _ANONYMOUS_COMPANY_PATTERN.search(company):
        return 0, "匿名公司岗位"

    breaker = matching_deal_breaker(title, deal_breakers)
    if breaker:
        return 0, f"触发排除词: {breaker}"

    jd_breaker = matching_deal_breaker(jd, jd_deal_breakers)
    if jd_breaker:
        return 0, f"触发JD排除词: {jd_breaker}"

    employment_block = _employment_type_block_reason(job, profile)
    if employment_block:
        return 0, employment_block

    posted_block = _posted_days_block_reason(job, profile)
    if posted_block:
        return 0, posted_block

    return salary_filter_result(str(job.get("salary") or ""), profile)


_EMPLOYMENT_TYPE_LABELS = {"internship": "实习", "full_time": "全职", "part_time": "兼职"}


def _employment_type_block_reason(job: dict, profile: dict) -> str | None:
    """求职类型筛选：亮着的类型 = 包含的类型；三个都亮 = 不限；一个都不亮 = 都不通过。

    类型未知的岗位不拦（旧规则保留）；配置里没有 job_types（老配置）时不限。
    """
    selected = profile.get("job_types")
    if not isinstance(selected, list):
        return None
    if not selected:
        return "求职类型未选：没有选中的求职类型"
    kind = classify_employment(job)
    label = _EMPLOYMENT_TYPE_LABELS.get(kind)
    if label and label not in {str(item).strip() for item in selected}:
        return f"求职类型不符合：{label}不在已选类型中"
    return None


def _posted_days_block_reason(job: dict, profile: dict) -> str | None:
    """发布时长筛选：按首次发现时间（created_at）算，超过 N 天的拦截；0=不限。

    收集器不采平台标注的发布时间，所以这里用首次发现时间代替；
    采集时刻（created_at 缺失）不拦截。
    """
    try:
        days = int(profile.get("posted_days") or 0)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    created = str(job.get("created_at") or "").strip()
    if not created:
        return None
    try:
        created_dt = datetime.strptime(created[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age_days = (datetime.now(timezone.utc) - created_dt).days
    if age_days > days:
        return f"发布时间超过 {days} 天（首次发现于 {created[:10]}）"
    return None


def rule_score(job: dict, config: dict) -> tuple[int, str]:
    """本地规则评分（无 AI Key 时的兜底）：0-100 分。

    基准 40 分 + 关键词命中（标题命中 +10、仅 JD 命中 +5，上限 40）
    + 薪资符合 +10（不符合 +3）+ 城市符合 +5 + 学历符合 +5，上限 100。
    未命中任何关键词时总分封顶 55：只凭薪资/城市/学历符合不能过线，
    关键词是必须的相关性信号。校准目标（门槛 60）：标题命中+薪资符合
    正好 60 分过线；标题命中+城市/学历符合 65-70 分；仅 JD 命中或
    无命中时不过线。
    """
    profile = config.get("profile", {}) if isinstance(config.get("profile"), dict) else {}
    search = config.get("search", {}) if isinstance(config.get("search"), dict) else {}
    title = str(job.get("title") or "")
    jd = str(job.get("jd") or "")
    title_lower = title.lower()
    jd_lower = jd.lower()

    terms: list[str] = []
    for source in (profile.get("target_roles") or [], search.get("keywords") or []):
        for term in source if isinstance(source, list) else []:
            cleaned = str(term or "").strip()
            if cleaned and cleaned not in terms:
                terms.append(cleaned)

    keyword_points = 0
    matched: list[str] = []
    for term in terms:
        term_lower = term.lower()
        if term_lower in title_lower:
            keyword_points += 10
            matched.append(term)
        elif term_lower in jd_lower:
            keyword_points += 5
            matched.append(term)
    keyword_points = min(keyword_points, 40)

    salary_points = 0
    parsed = _parse_salary_range_k(str(job.get("salary") or ""))
    salary_min = _as_number(profile.get("salary_min", 0))
    salary_max = _as_number(profile.get("salary_max", 0))
    if parsed is not None:
        low, high = parsed
        if (salary_min <= 0 or high >= salary_min) and (salary_max <= 0 or low <= salary_max):
            salary_points = 10
        else:
            salary_points = 3

    city_points = 0
    target_cities = [str(c).strip() for c in (profile.get("target_cities") or []) if str(c).strip()]
    job_city = str(job.get("city") or "").strip().split("·")[0].strip()
    if target_cities and job_city and job_city in target_cities:
        city_points = 5

    education_points = 0
    wanted_edu = str(profile.get("education") or "").strip()
    job_edu = str(job.get("education") or "").strip()
    if wanted_edu and job_edu and (wanted_edu in job_edu or job_edu in wanted_edu):
        education_points = 5

    raw_total = 40 + keyword_points + salary_points + city_points + education_points
    score = raw_total
    if not matched:
        # 无关键词命中：薪资/城市/学历再符合也不能过线。
        score = min(score, 55)
    score = min(score, 100)
    # 原因要让用户对得上分数：每一项写清加了几分，封顶也要写出来。
    # 例子：基础40分 + 关键词0分（没命中） + 薪资10分（符合） + 城市5分（符合）
    #       + 学历0分（没对上） = 55分；无关键词命中，总分封顶55分，最终55分。
    if matched:
        keyword_note = f"关键词{keyword_points}分（命中：{'、'.join(matched[:5])}）"
    else:
        keyword_note = f"关键词{keyword_points}分（没命中）"
    if parsed is None:
        salary_note = f"薪资{salary_points}分（面议/没写）"
    elif salary_points == 10:
        salary_note = f"薪资{salary_points}分（符合）"
    else:
        salary_note = f"薪资{salary_points}分（不符合期望）"
    city_note = f"城市{city_points}分（{'符合' if city_points else '不在目标城市'}）"
    edu_note = f"学历{education_points}分（{'符合' if education_points else '没对上'}）"
    reason = f"基础40分 + {keyword_note} + {salary_note} + {city_note} + {edu_note} = {raw_total}分"
    if not matched:
        reason += f"；无关键词命中，总分封顶55分，最终{score}分"
    return score, reason


def salary_filter_result(salary: str, profile: dict) -> tuple[int, str]:
    """薪资硬过滤，采集增值层与 quick_score 共用同一条规则。

    返回 (score, reason)：score=0 表示拦截并携带原因，score=100 表示通过。
    """
    salary_min = _as_number(profile.get("salary_min", 0))
    salary_max = _as_number(profile.get("salary_max", 0))
    parsed_salary = _parse_salary_range_k(str(salary or ""))
    if parsed_salary is None:
        if _as_bool(profile.get("filter_unparsed_salary", True)):
            return 0, "薪资面议/无法解析，已过滤"
        return 100, "薪资面议/无法解析（已关闭过滤，交由 AI 判断）"

    job_salary_min, job_salary_max = parsed_salary
    if salary_min > 0 and job_salary_max < salary_min:
        return 0, f"薪资低于硬性要求: {_format_k(job_salary_max)}K < {_format_k(salary_min)}K"

    salary_ceil_ratio = max(_as_number(profile.get("salary_ceil_ratio", 1.5)), 1.0)
    if salary_max > 0 and job_salary_min > salary_max * salary_ceil_ratio:
        return 0, (
            f"薪资远超期望上限: 报价下限 {_format_k(job_salary_min)}K > "
            f"{_format_k(salary_max)}K × {salary_ceil_ratio:g}"
        )

    return 100, "预筛通过"



def _parse_salary_range_k(salary: str) -> tuple[float, float] | None:
    range_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK]?\s*-\s*(\d+(?:\.\d+)?)\s*[kK]", salary)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        return low, max(low, high)

    cn_range = _parse_cn_salary_range_k(salary)
    if cn_range:
        return cn_range

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK]", salary)
    if single_match:
        value = float(single_match.group(1))
        return value, value

    return None


_CN_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(千|万|[wW])?\s*[-~—]\s*(\d+(?:\.\d+)?)\s*(千|万|[wW])?\s*元?"
    r"|(\d+(?:\.\d+)?)\s*(千|万|[wW])?\s*元"
)
_YEARLY_RE = re.compile(r"/\s*(?:年|12\s*个月)|年薪")


def _cn_amount_to_k(value: str, unit: str | None) -> float:
    amount = float(value)
    if unit in ("万", "w", "W"):
        return amount * 10
    if unit == "千":
        return amount
    return amount / 1000


def _parse_cn_salary_range_k(salary: str) -> tuple[float, float] | None:
    """Parse 元/千/万 monthly ranges used by 智联 and 前程无忧 into K units."""
    match = _CN_RANGE_RE.search(salary)
    if not match:
        return None

    if match.group(1):
        # 「1-2万」只在末位带单位，首位沿用末位单位。
        low_unit = match.group(2) or match.group(4)
        if not (low_unit or match.group(4) or "元" in match.group(0)):
            # 无单位也无「元」字（如 "15-25"）不足以判断币种，交给 AI 判断。
            return None
        low = _cn_amount_to_k(match.group(1), low_unit)
        high = _cn_amount_to_k(match.group(3), match.group(4))
    else:
        low = high = _cn_amount_to_k(match.group(5), match.group(6))

    if low <= 0:
        return None
    if _YEARLY_RE.search(salary):
        low /= 12
        high /= 12
    return min(low, high), max(low, high)


def _as_number(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "是"}
    return True


def _format_k(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
