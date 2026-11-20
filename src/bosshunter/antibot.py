"""反爬虫规避与风控防御：投递链路的防检测层。

本模块只做一件事：让自动投递更像真人在操作，并在平台风控出现时
及时停手、把控制权交还给用户，而不是硬闯。

架构说明（先读懂再改）：
- 本项目不启动浏览器：浏览器是用户自己的 Chrome，由启动脚本
  （scripts/windows/start_bosshunter.ps1、scripts/macos/start_bosshunter.sh）
  或面板自动拉起（launch_debuggable_browser）用 --remote-debugging-port
  拉起，Python 通过 CDP 桥接服务连上去。
- 指纹层面的“抹除”只有一条：保持用户真实指纹稳定。以前启动参数里
  还有 --disable-blink-features=AutomationControlled，v20 拿掉了——
  CDP 直连的真人 Chrome 里 navigator.webdriver 本来就是 false，这参数
  除了让 Chrome 顶上挂一条“不受支持的命令行标记”警告，什么都没多做。
- 刻意不做 User-Agent / 分辨率随机化：这是用户每天都在用的真人
  Chrome，同一个登录账号每次换一个 UA / 分辨率，反而是更强的机器人
  信号（指纹不一致）。真正的防检测靠行为随机化 + 及时停手。
- navigator.webdriver 在 CDP 直连的真人 Chrome 里本身就是 false，
  check_webdriver_flag() 只用来在本地验证时自检，不做门禁。

四个机制（与 lucky 定的四点一一对应）：
1. 指纹：只剩真实指纹保持稳定（启动参数里的
   --disable-blink-features=AutomationControlled 已在 v20 拿掉，见上）。
2. 人性化行为：micro_pause() 做 3-8 秒高斯延迟；human_browse() 在点
   投递前模拟上下滚动浏览，而不是干等；分平台日上限 + 全局日上限。
3. 验证码阻断：handle_page_risk() 检测到滑块/安全验证页就建一条
   verification_hold，投递暂停，等用户在浏览器里手动验证完点“继续”
   才恢复；连续失败触发熔断（见 CircuitBreaker）。
4. 错峰：投递队列按平台分组（采集顺序），组与组之间插一段高斯随机
   停顿，避免同一瞬间多平台高频请求。

所有阈值都从配置 throttle 段读（见 config.DEFAULTS["throttle"]），
评分逻辑碰不到它们；改阈值只改配置，不改代码。
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime
from threading import Event
from typing import Any, Callable

from bosshunter.db import (
    breaker_is_suspended,
    breaker_record_failure,
    breaker_record_success,
    breaker_suspended_platforms,
    count_sent_today_by_platform as _db_count_sent_today_by_platform,
    create_verification_hold,
    get_verification_hold,
)

# 启动脚本里给 Chrome 加的参数（两个启动脚本都要同步）。
# v20 已空：--disable-blink-features=AutomationControlled 拿掉了——
# CDP 直连的真人 Chrome 里 navigator.webdriver 本来就是 false，这参数
# 除了让 Chrome 顶上挂一条“不受支持的命令行标记”警告，什么都没多做。
# 常量留着（空元组），免得别处引用断掉；改参数请改
# scripts/windows/start_bosshunter.ps1 和 scripts/macos/start_bosshunter.sh。
CHROME_STEALTH_ARGS: tuple[str, ...] = ()

# 平台展示名（面板文案用）。
PLATFORM_NAMES = {
    "boss": "BOSS 直聘",
    "zhilian": "智联招聘",
    "51job": "前程无忧",
    "liepin": "猎聘",
}

DEFAULT_PLATFORM_ORDER = ("boss", "zhilian", "51job", "liepin")


def platform_display_name(platform: str) -> str:
    """Return the plain-language name shown in the panel."""
    return PLATFORM_NAMES.get(str(platform or "").strip() or "boss", str(platform))


# ---------------------------------------------------------------------------
# 风控档位参数表（v22 可视化）：档位决定投递节奏
# ---------------------------------------------------------------------------
# 标准模式 = v9 上线以来的现状（3–8 秒微停顿、15–30 秒浏览、
# 30–120 秒错峰、60–180 秒投递间隔）；稳妥拉长间隔与休息，极速缩短间隔。
# 开关（random_interval / stage_rest / auto_pause_on_verify / daily_cap）
# 逐项控制这些参数是否生效，见 resolve_risk_profile()。
RISK_TIERS: dict[str, dict[str, Any]] = {
    "steady": {
        "micro_delay": (5.0, 12.0),
        "browse": (20.0, 40.0),
        "detail_browse": (8.0, 16.0),
        "stagger": (60.0, 180.0),
        "interval": (120.0, 300.0),
        "rest_every": 5,
        "rest": (60.0, 120.0),
    },
    "standard": {
        "micro_delay": (3.0, 8.0),
        "browse": (15.0, 30.0),
        "detail_browse": (6.0, 12.0),
        "stagger": (30.0, 120.0),
        "interval": (60.0, 180.0),
        "rest_every": 10,
        "rest": (30.0, 60.0),
    },
    "fast": {
        "micro_delay": (1.0, 3.0),
        "browse": (8.0, 15.0),
        "detail_browse": (3.0, 6.0),
        "stagger": (10.0, 45.0),
        "interval": (30.0, 90.0),
        "rest_every": 15,
        "rest": (15.0, 30.0),
    },
}

RISK_TIER_DEFAULT = "steady"


def resolve_risk_profile(throttle_config: dict) -> dict[str, Any]:
    """把档位 + 开关解析成投递循环实际用的节奏参数。

    返回的键直接覆盖 throttle_config 里的同名键：微停顿、浏览时长、
    详情浏览、错峰、投递间隔都按档位表走；random_interval 关了，微停顿
    就是 0（真的不随机停顿）；stage_rest / auto_pause_on_verify /
    daily_cap_enabled 只返回布尔开关，调用方按开关决定做不做。
    daily_cap 优先读新字段，没有才读老字段 daily_limit，再没有默认 120。
    """
    cfg = throttle_config if isinstance(throttle_config, dict) else {}
    tier = str(cfg.get("risk_tier") or RISK_TIER_DEFAULT).strip() or RISK_TIER_DEFAULT
    table = RISK_TIERS.get(tier, RISK_TIERS[RISK_TIER_DEFAULT])
    tier = tier if tier in RISK_TIERS else RISK_TIER_DEFAULT
    random_interval = cfg.get("random_interval", True) is not False
    micro_low, micro_high = table["micro_delay"]
    if not random_interval:
        micro_low, micro_high = 0.0, 0.0
    browse_low, browse_high = table["browse"]
    detail_low, detail_high = table["detail_browse"]
    stagger_low, stagger_high = table["stagger"]
    interval_low, interval_high = table["interval"]
    rest_every, (rest_low, rest_high) = table["rest_every"], table["rest"]
    if "daily_cap" in cfg:
        raw_cap = cfg.get("daily_cap")
    elif "daily_limit" in cfg:
        raw_cap = cfg.get("daily_limit")
    else:
        raw_cap = 120
    try:
        daily_cap = max(1, int(raw_cap))
    except (TypeError, ValueError):
        daily_cap = 120
    return {
        "risk_tier": tier,
        "micro_delay_min": float(micro_low),
        "micro_delay_max": float(micro_high),
        "browse_duration_min": float(browse_low),
        "browse_duration_max": float(browse_high),
        "detail_browse_min": float(detail_low),
        "detail_browse_max": float(detail_high),
        "platform_stagger_min": float(stagger_low),
        "platform_stagger_max": float(stagger_high),
        "interval_min": float(interval_low),
        "interval_max": float(interval_high),
        "rest_every": int(rest_every),
        "rest_min": float(rest_low),
        "rest_max": float(rest_high),
        "random_interval": bool(random_interval),
        "stage_rest": cfg.get("stage_rest", True) is not False,
        "auto_pause_on_verify": cfg.get("auto_pause_on_verify", True) is not False,
        "daily_cap_enabled": cfg.get("daily_cap_enabled", True) is not False,
        "daily_cap": daily_cap,
    }


def sample_rest_delay(
    low: float = 30.0,
    high: float = 60.0,
    rng: random.Random | None = None,
) -> float:
    """Uniform rest between stage breaks, clipped to [low, high]."""
    source = rng or random
    return max(low, min(high, source.uniform(low, high)))


def should_stage_rest(sent_count: int, rest_every: int, has_more: bool) -> bool:
    """连投满 rest_every 个、后面还有岗位时，才歇一次。"""
    try:
        every = int(rest_every)
    except (TypeError, ValueError):
        return False
    return bool(has_more) and every > 0 and sent_count > 0 and sent_count % every == 0


# ---------------------------------------------------------------------------
# 1. 人性化行为：高斯微延迟 + 模拟滚动浏览
# ---------------------------------------------------------------------------

def sample_micro_delay(
    low: float = 3.0,
    high: float = 8.0,
    rng: random.Random | None = None,
) -> float:
    """Sample one human-like pause between two actions.

    Gaussian with mean (low+high)/2 and std (high-low)/4, clipped to
    [low, high]. Default 3-8s: mean 5.5s, std 1.25s, ~95% lands inside.
    """
    source = rng or random
    mean = (low + high) / 2.0
    std = (high - low) / 4.0
    return max(low, min(high, source.gauss(mean, std)))


def micro_pause(
    stop_event: Event | None = None,
    low: float = 3.0,
    high: float = 8.0,
    rng: random.Random | None = None,
    sleep_fn: Callable[[float], bool] | None = None,
) -> bool:
    """Wait one sampled micro-delay. Returns True when stopping was requested."""
    delay = sample_micro_delay(low, high, rng)
    if sleep_fn is not None:
        return bool(sleep_fn(delay))
    if stop_event is not None:
        return bool(stop_event.wait(delay))
    time.sleep(delay)
    return False


def human_browse(
    target_id: str,
    stop_event: Event | None = None,
    *,
    min_s: float = 15.0,
    max_s: float = 30.0,
    scroll_fn: Callable[[str, int], Any] | None = None,
    rng: random.Random | None = None,
    sleep_fn: Callable[[float], bool] | None = None,
    clock_fn: Callable[[], float] | None = None,
) -> bool:
    """Browse a job page like a person: scroll up/down in random steps.

    Spends a random budget within [min_s, max_s] scrolling instead of
    idling. Returns True when stopping was requested. ``scroll_fn`` takes
    (target_id, y_pixels); ``sleep_fn`` returns True when stopping.
    """
    source = rng or random
    clock = clock_fn or time.monotonic

    def _sleep(seconds: float) -> bool:
        if seconds <= 0:
            return bool(stop_event and stop_event.is_set())
        if sleep_fn is not None:
            return bool(sleep_fn(seconds))
        if stop_event is not None:
            return bool(stop_event.wait(seconds))
        time.sleep(seconds)
        return False

    if scroll_fn is None:
        from bosshunter.browser import scroll as _scroll

        scroll_fn = _scroll
    budget = source.uniform(min_s, max_s)
    deadline = clock() + budget
    # Settle briefly so the page finishes rendering before the first scroll.
    if _sleep(source.uniform(0.6, 1.5)):
        return True
    while clock() < deadline:
        if stop_event is not None and stop_event.is_set():
            return True
        if source.random() < 0.75:
            step = int(source.uniform(200, 700))
        else:
            step = -int(source.uniform(100, 400))
        try:
            scroll_fn(target_id, step)
        except Exception:
            # A failed scroll must never fail the delivery itself.
            pass
        remaining = deadline - clock()
        pause = min(source.uniform(0.8, 2.5), max(0.0, remaining))
        if _sleep(pause):
            return True
    return False


# ---------------------------------------------------------------------------
# 2. 验证码 / 风控页检测
# ---------------------------------------------------------------------------

CAPTCHA_DETECT_JS = """
(() => {
    const text = (document.body?.innerText || '').slice(0, 10000);
    const url = String(location.href || '');
    const title = String(document.title || '');
    // 页面还没加载完不判定：内容还没渲染出来时“没有操作按钮 + 提到
    // 验证码”很容易误判（v27：用户没收到验证码却被判验证码、停掉
    // 整轮）。加载中一律返回无风险，调用方稍后会再查。
    if (document.readyState !== 'complete') {
        return JSON.stringify({risk: null, url});
    }
    const captchaEl = document.querySelector(
        '.geetest_panel, .captcha, [class*="captcha"], [id*="captcha"], '
        + 'iframe[src*="captcha"], iframe[src*="verify"], [class*="geetest"], '
        + '[class*="slider-verify"], [class*="nc_wrapper"]'
    );
    if (captchaEl) return JSON.stringify({risk: 'captcha', evidence: 'captcha_element', detail: '页面出现了验证码组件', url});
    if (/captcha|security-check|\\/verify|geetest/i.test(url)) {
        return JSON.stringify({risk: 'captcha', evidence: 'captcha_url', detail: '页面地址指向验证码/验证页', url});
    }
    if (/\\/web\\/user\\/(?:login|\\?ka=header-login)/i.test(url)) {
        return JSON.stringify({risk: 'login_required', evidence: 'login_url', url});
    }
    const pageUrl = new URL(url);
    const blockedPath = /(?:^|\\/)(?:403|forbidden|access-denied)(?:\\/|$)/i.test(pageUrl.pathname);
    const blockedStatus = Array.from(pageUrl.searchParams).some(([key, value]) =>
        /^(?:code|status|error|error_code|status_code)$/i.test(key)
        && /^(?:403|forbidden|access-denied)$/i.test(value)
    );
    if (blockedPath || blockedStatus) {
        return JSON.stringify({risk: 'blocked', evidence: 'blocked_url', url});
    }
    if (/^(?:403(?:\\s+forbidden)?|forbidden|access denied|访问被拒绝|账号异常|账号受限)/i.test(title.trim())) {
        return JSON.stringify({risk: 'blocked', evidence: 'blocked_title', url});
    }
    // Text signals only count when the page shows no normal action button:
    // a job page whose description merely mentions verification must not trip.
    const hasAction = /立即沟通|沟通|投递简历|立即投递|申请职位|立即申请/.test(text);
    // 纯文字信号只在“挑战页”语境下才算（v27）：关键字出现在看得见的
    // 验证弹窗/验证框里，或者页面标题就是验证提示，或者整页几乎没有
    // 别的正文（真正的验证页除挑战外没什么内容）。岗位描述、页脚、
    // 侧边栏里顺带提到“验证码”不算——空结果页本身就没有操作按钮，
    // 不能只凭一个词就停掉整轮。detail 写清判据，供面板展示依据。
    const keywordMatch = /(拖动滑块|请完成安全验证|安全验证|请完成验证|验证码)/.exec(text);
    const challengeHit = keywordMatch && Array.from(
        document.querySelectorAll(
            '[class*="verify"], [class*="captcha"], [class*="geetest"], '
            + '[class*="slider"], [class*="nc_"], [role="dialog"], dialog, .modal, .popup'
        ) || []
    ).find((el) => {
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && (el.innerText || '').includes(keywordMatch[1]);
    });
    if (!hasAction && keywordMatch) {
        if (challengeHit) {
            return JSON.stringify({risk: 'captcha', evidence: 'captcha_page', detail: '验证弹窗里出现“' + keywordMatch[1] + '”', url});
        }
        if (/验证码|安全验证|完成验证|拖动滑块/.test(title)) {
            return JSON.stringify({risk: 'captcha', evidence: 'captcha_page', detail: '页面标题提示验证', url});
        }
        if (text.trim().length < 1500) {
            return JSON.stringify({risk: 'captcha', evidence: 'captcha_page', detail: '页面几乎没有正文，只看到“' + keywordMatch[1] + '”', url});
        }
    }
    if (!hasAction && /操作频繁|访问频繁|请求频繁|稍后再试|频率限制/.test(text) && text.trim().length < 1500) {
        return JSON.stringify({risk: 'rate_limit', evidence: 'rate_limit_page', url});
    }
    if (!hasAction && /请先登录|登录后投递|需要登录/.test(text)) {
        return JSON.stringify({risk: 'login_required', evidence: 'login_text', url});
    }
    return JSON.stringify({risk: null, url});
})()
"""


def _parse_js_result(result: Any) -> dict:
    if not result:
        return {"risk": None}
    if isinstance(result, dict):
        return result
    try:
        parsed = json.loads(result)
        return parsed if isinstance(parsed, dict) else {"risk": None}
    except (json.JSONDecodeError, TypeError):
        return {"risk": None}


def detect_page_risk(evaluate_fn: Callable[[str, str], Any], target_id: str) -> dict:
    """Run the risk detector in the open page. Returns {risk, evidence, detail, url}."""
    try:
        raw = evaluate_fn(target_id, CAPTCHA_DETECT_JS)
    except Exception:
        return {"risk": None}
    result = _parse_js_result(raw)
    risk = result.get("risk")
    if risk not in {"captcha", "rate_limit", "blocked", "login_required"}:
        return {"risk": None, "url": result.get("url", "")}
    return {
        "risk": risk,
        "evidence": result.get("evidence", ""),
        "detail": result.get("detail", ""),
        "url": result.get("url", ""),
    }


def check_webdriver_flag(evaluate_fn: Callable[[str, str], Any], target_id: str) -> bool | None:
    """Self-check only: True when navigator.webdriver is set (bot flag present).

    Returns None when the page cannot be evaluated. This is a diagnostic,
    never a gate: a real user Chrome attached over CDP reports undefined.
    """
    try:
        raw = evaluate_fn(target_id, "JSON.stringify({webdriver: navigator.webdriver})")
    except Exception:
        return None
    parsed = _parse_js_result(raw)
    value = parsed.get("webdriver")
    if value is None:
        return None
    return bool(value)


def wait_for_manual_verification(
    db,
    hold_id: int,
    stop_event: Event | None = None,
    *,
    timeout_s: float = 7200.0,
    poll_s: float = 2.0,
    clock_fn: Callable[[], float] | None = None,
    sleep_fn: Callable[[float], bool] | None = None,
) -> bool:
    """Wait until the user resolves the hold (or stops / times out).

    Returns True only when the hold was resolved. Polls the DB so the
    panel's resolve endpoint (another thread/connection) can release us.
    """
    clock = clock_fn or time.monotonic
    deadline = clock() + max(0.0, timeout_s)
    while True:
        if stop_event is not None and stop_event.is_set():
            return False
        hold = get_verification_hold(db, hold_id)
        if hold and hold.get("resolved_at"):
            return True
        if clock() >= deadline:
            return False
        remaining = min(poll_s, max(0.0, deadline - clock()))
        if sleep_fn is not None:
            if sleep_fn(remaining):
                return False
        elif stop_event is not None:
            if stop_event.wait(remaining):
                return False
        else:
            time.sleep(remaining)


def handle_page_risk(
    target_id: str,
    platform: str,
    throttle_config: dict,
    *,
    evaluate_fn: Callable[[str, str], Any] | None = None,
    max_holds: int = 3,
) -> str | None:
    """Detect risk on the open page and, for captchas, hand control to the user.

    Returns None when the page is clear, else an error string:
    'captcha' (still blocked after max_holds), 'rate_limit', 'blocked',
    'login_required', 'stopped', 'verification_timeout'.

    On captcha: creates a verification hold, tells the panel (via the
    workbench log) to ask the user to verify manually in the browser and
    press Continue, then waits. After the user continues, the page is
    re-checked; a fresh hold is created if it is still blocked.
    """
    if evaluate_fn is None:
        from bosshunter.browser import evaluate as _evaluate

        evaluate_fn = _evaluate
    db = throttle_config.get("_db")
    stop_event = throttle_config.get("_workbench_stop_event")
    workbench_log = throttle_config.get("_workbench_log")
    timeout_minutes = throttle_config.get("captcha_wait_timeout_minutes", 120)
    try:
        timeout_s = max(60.0, float(timeout_minutes) * 60.0)
    except (TypeError, ValueError):
        timeout_s = 7200.0
    # 测试专用覆盖（与 _chat_button_attempts 等既有测试旋钮同类）：只在测试里传。
    try:
        override_s = float(throttle_config.get("_captcha_wait_timeout_s"))
        timeout_s = max(0.05, override_s)
    except (TypeError, ValueError):
        pass
    name = platform_display_name(platform)

    def _log(message: str) -> None:
        if callable(workbench_log):
            workbench_log(message)
        else:
            print(message)

    holds_used = 0
    while holds_used < max_holds:
        detected = detect_page_risk(evaluate_fn, target_id)
        risk = detected.get("risk")
        if not risk:
            return None
        if risk in {"rate_limit", "blocked", "login_required"}:
            return str(risk)
        # risk == 'captcha'
        # 风控保护开关：关了就不自动建 hold 等用户验证，只提示一声就返回，
        # 这次投递按失败记（熔断照常计数），不再替用户停在那等。
        if not throttle_config.get("auto_pause_on_verify", True):
            _log(
                f"触发平台安全验证（{name}），自动暂停已关："
                "请在浏览器中手动完成验证，验证完再继续投递"
            )
            return "captcha"
        if db is None:
            return "captcha"
        holds_used += 1
        hold_id = create_verification_hold(
            db,
            platform,
            url=str(detected.get("url") or ""),
            evidence=str(detected.get("evidence") or ""),
        )
        _log(
            f"触发平台安全验证（{name}），投递已暂停："
            "请在浏览器中手动完成验证后点击继续"
        )
        resolved = wait_for_manual_verification(
            db, hold_id, stop_event, timeout_s=timeout_s
        )
        if not resolved:
            if stop_event is not None and stop_event.is_set():
                return "stopped"
            return "verification_timeout"
    return "captcha"


# ---------------------------------------------------------------------------
# 3. 熔断：连续失败挂起该平台当日任务
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Per-platform circuit breaker, persisted in SQLite.

    A failure that is not a business skip (job closed, no button, already
    applied, needs login) counts. After ``max_failures`` consecutive
    failures the platform is suspended until local midnight; a success
    resets the counter. Suspension is checked before every job, so the
    rest of that platform's queue is deferred, not retried.
    """

    def __init__(self, db, max_failures: int = 3) -> None:
        self._db = db
        try:
            self._max_failures = max(1, int(max_failures))
        except (TypeError, ValueError):
            self._max_failures = 3

    def record_failure(self, platform: str) -> bool:
        """Record one failure. Returns True when this failure suspended the platform."""
        return breaker_record_failure(self._db, platform, self._max_failures)

    def record_success(self, platform: str) -> None:
        breaker_record_success(self._db, platform)

    def is_suspended(self, platform: str) -> bool:
        return breaker_is_suspended(self._db, platform)

    def suspended_platforms(self) -> list[str]:
        return breaker_suspended_platforms(self._db)


# ---------------------------------------------------------------------------
# 4. 分平台日上限 + 错峰分组
# ---------------------------------------------------------------------------

def platform_daily_limits(throttle_config: dict) -> dict[str, int]:
    """Per-platform daily send caps. Defaults to 80 per platform."""
    raw = throttle_config.get("daily_limit_per_platform")
    limits: dict[str, int] = {}
    if isinstance(raw, dict):
        for platform, value in raw.items():
            try:
                limits[str(platform)] = max(1, int(value))
            except (TypeError, ValueError):
                continue
    for platform in DEFAULT_PLATFORM_ORDER:
        limits.setdefault(platform, 80)
    return limits


def count_sent_today_by_platform(db, platform: str) -> int:
    """How many sends this platform already did today (local day)."""
    return _db_count_sent_today_by_platform(db, platform)


def group_jobs_by_platform(
    jobs: list[dict],
    order: tuple[str, ...] = DEFAULT_PLATFORM_ORDER,
) -> list[tuple[str, list[dict]]]:
    """Group jobs by platform in collection order, keeping in-group order."""
    groups: dict[str, list[dict]] = {}
    for job in jobs:
        platform = str(job.get("source_platform") or "boss").strip() or "boss"
        groups.setdefault(platform, []).append(job)
    ordered = [(platform, groups[platform]) for platform in order if platform in groups]
    ordered.extend(
        (platform, items) for platform, items in groups.items() if platform not in order
    )
    return ordered


def sample_stagger_delay(
    low: float = 30.0,
    high: float = 120.0,
    rng: random.Random | None = None,
) -> float:
    """Gaussian pause between platform groups, clipped to [low, high]."""
    source = rng or random
    mean = (low + high) / 2.0
    std = (high - low) / 4.0
    return max(low, min(high, source.gauss(mean, std)))


def stagger_pause(
    stop_event: Event | None = None,
    low: float = 30.0,
    high: float = 120.0,
    rng: random.Random | None = None,
    sleep_fn: Callable[[float], bool] | None = None,
) -> bool:
    """Wait one sampled inter-platform stagger. Returns True when stopping."""
    delay = sample_stagger_delay(low, high, rng)
    if sleep_fn is not None:
        return bool(sleep_fn(delay))
    if stop_event is not None:
        return bool(stop_event.wait(delay))
    time.sleep(delay)
    return False


def local_day_end() -> datetime:
    """Today 23:59:59 local time (breaker suspension target)."""
    return datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)


def end_of_today_timestamp() -> str:
    """'YYYY-MM-DD HH:MM:SS' for local end of day (SQLite TEXT compare)."""
    return local_day_end().strftime("%Y-%m-%d %H:%M:%S")
