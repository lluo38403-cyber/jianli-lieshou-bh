"""Browser facade backed by BossHunter's built-in Browser Runtime."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bosshunter.browser.client import RuntimeClient
from bosshunter.browser.runtime import (
    ensure_runtime,
    get_runtime_url,
    pick_chrome_debug_port,
    runtime_health,
    runtime_targets,
    set_browser_config,
)

_logger = logging.getLogger(__name__)

CDP_PROXY_URL = get_runtime_url()
CDP_DIRECT_URL = "http://localhost:9222"
CDP_DEFAULT_URL = "http://127.0.0.1:9222"
NAV_TIMEOUT_MS = 15000


def configure(config: dict[str, Any] | None = None) -> None:
    """Set process-wide browser configuration used by runtime helpers."""
    set_browser_config(config)


def _client() -> RuntimeClient:
    return RuntimeClient()


def _ready(allow_launch: bool = True, target_url: str | None = None) -> bool:
    from bosshunter.browser.ownership import browser_execution
    with browser_execution():
        return ensure_runtime(allow_launch=allow_launch, target_url=target_url)


def check_chrome_connection() -> dict | None:
    """Check whether Browser Runtime can connect to Chrome."""
    if not _ready():
        return None
    return _client().health()


def get_page_targets() -> list[dict]:
    """Get page targets from Browser Runtime."""
    if not _ready():
        return []
    return _client().targets()


def find_boss_tab() -> dict | None:
    """Find a BOSS直聘 tab in Chrome."""
    for target in get_page_targets():
        if "zhipin.com" in target.get("url", ""):
            return target
    return None


def find_zhilian_tab() -> dict | None:
    """Find a 智联招聘 tab in Chrome without opening or logging in."""
    for target in get_page_targets():
        url = str(target.get("url", ""))
        if "zhaopin.com" in url:
            return target
    return None


def find_51job_tab() -> dict | None:
    """Find a 前程无忧 51job tab in Chrome without opening or logging in."""
    for target in get_page_targets():
        url = str(target.get("url", ""))
        if "51job.com" in url:
            return target
    return None


def find_liepin_tab() -> dict | None:
    """Find a 猎聘 tab in Chrome without opening or logging in."""
    for target in get_page_targets():
        url = str(target.get("url", ""))
        if "liepin.com" in url:
            return target
    return None


def new_tab(url: str, background: bool = True) -> str | None:
    """Open a tab and return its target ID.

    v54：默认后台打开——投递/采集/监测的标签都不该抢前台；只有用户
    主动点的“去登录”才显式传 background=False。以前默认 False，
    任何一个漏传的调用点都会前台建标签、闪用户一下。
    """
    if not _ready(target_url=url):
        return None
    return _client().new_tab(url, background=background)


def minimize_tab(target_id: str) -> bool:
    """Minimize the window holding this tab (v54: site-opened tabs)."""
    if not _ready():
        return False
    return _client().minimize(target_id)


def activate_tab(target_id: str) -> bool:
    """Bring a tab (and its window) to the front."""
    if not _ready():
        return False
    return _client().activate(target_id)


def _same_site_host(left: str, right: str) -> bool:
    """True when two URLs belong to the same site (last two host labels).

    Good enough for the four platforms (all *.com); not a general
    public-suffix check, and it never needs to be one here.
    """
    left_host = (urlparse(left).hostname or "").lower()
    right_host = (urlparse(right).hostname or "").lower()
    if not left_host or not right_host:
        return False
    left_parts = left_host.split(".")
    right_parts = right_host.split(".")
    return left_parts[-2:] == right_parts[-2:]


def _normalize_url(url: str) -> str:
    return url.rstrip("/") or "/"


def _chrome_port_check() -> dict[str, Any]:
    """Compare the Chrome port the runtime actually connected to with the expected one.

    打开成功后核对一次落点：runtime_health() 上报的 chromePort 与
    pick_chrome_debug_port() 的期望端口不一致，说明代理连上的不是
    工作浏览器（例如个人 Chrome 抢在了前面），登录页可能落在另一个
    窗口。只做核对、不拦、不回退：调用方据 port_mismatch 决定是否提醒。
    任一端口拿不到时无法比对，按一致处理（port_mismatch=False）。
    """
    try:
        health = runtime_health()
        actual = health.get("chromePort") if isinstance(health, dict) else None
        expected = pick_chrome_debug_port()
        actual_port = int(actual) if actual is not None else None
        mismatch = actual_port is not None and expected is not None and actual_port != expected
        if mismatch:
            _logger.warning(
                "去登录落点与期望不一致：代理实际连的 Chrome 端口是 %s，期望端口是 %s",
                actual_port,
                expected,
            )
        return {"port_mismatch": mismatch, "chrome_port": actual_port}
    except Exception as exc:
        _logger.warning("去登录落点校验失败：%s", exc)
        return {"port_mismatch": False, "chrome_port": None}


def open_platform_tab(url: str) -> dict[str, Any]:
    """Open a platform page in the work browser, reusing its tab if open.

    v27：登录按钮点一次就该有明确结果。已打开对应标签页时切过去
    （登录页本身或站点首页：直接置前；不是目标页时先导航过去），
    不再新开一个；没打开才新开。browser_started 表示这次调用之前
    浏览器还没起来、是这次现拉的——前端据此给“工作浏览器已启动”
    的反馈，而不是含糊的“已打开”。
    只在“去登录”用：采集/投递的 new_tab 语义不变（永远新开）。
    成功返回里带 port_mismatch / chrome_port（见 _chrome_port_check）：
    代理实际连的 Chrome 端口与期望端口不一致时前端可据此提醒用户。
    失败时仍只报“浏览器没连上…”，不加回退。
    """
    was_up = runtime_targets() is not None
    if not _ready(target_url=url):
        return {
            "target_id": None,
            "browser_started": False,
            "reused_tab": False,
            "message": "浏览器没连上：工作浏览器没启动起来，再点一次试试。",
        }
    for target in get_page_targets():
        target_url = str(target.get("url") or "")
        target_id = str(target.get("targetId") or "")
        if not target_id or not _same_site_host(target_url, url):
            continue
        # 只复用“已经是目标页”或“站点首页”的标签页：别的页面（岗位
        # 列表、详情页）可能是用户正在看的东西，不抢过来导航走。
        target_path = urlparse(target_url).path or "/"
        if _normalize_url(target_url) != _normalize_url(url) and target_path not in ("", "/"):
            continue
        if _normalize_url(target_url) != _normalize_url(url):
            navigate(target_id, url)
        activate_tab(target_id)
        return {
            "target_id": target_id,
            "browser_started": not was_up,
            "reused_tab": True,
            "message": "已经打开了，直接切过去了。",
            **_chrome_port_check(),
        }
    # 去登录必须前台：用户要亲眼看见登录页、亲手登录，所以这里显式
    # 传 background=False（v54 起 new_tab 默认后台，不写就错了）。
    target_id = _client().new_tab(url, background=False)
    if not target_id:
        return {
            "target_id": None,
            "browser_started": not was_up,
            "reused_tab": False,
            "message": "浏览器没连上：工作浏览器没启动起来，再点一次试试。",
        }
    return {
        "target_id": target_id,
        "browser_started": not was_up,
        "reused_tab": False,
        "message": "已在工作浏览器打开。",
        **_chrome_port_check(),
    }


def close_tab(target_id: str) -> bool:
    """Close a tab by target ID."""
    if not _ready():
        return False
    return _client().close_tab(target_id)


def navigate(target_id: str, url: str) -> bool:
    """Navigate a tab to a URL."""
    if not _ready():
        return False
    return _client().navigate(target_id, url)


def evaluate(target_id: str, expression: str, timeout: float = 30) -> Any:
    """Execute JavaScript in a tab and return the runtime value."""
    if not _ready():
        return None
    return _client().evaluate(target_id, expression, timeout)


def click(target_id: str, selector: str) -> bool:
    """Click an element by CSS selector using DOM click."""
    if not _ready():
        return False
    return _client().click(target_id, selector)


def click_at(target_id: str, selector_or_xy: str) -> bool:
    """Click by selector or x,y coordinates using CDP mouse events."""
    if not _ready():
        return False
    return _client().click_at(target_id, selector_or_xy)


def type_text(target_id: str, text: str, human: bool = False) -> bool:
    """Insert text using CDP input events."""
    if not _ready():
        return False
    return _client().type_text(target_id, text, human=human)


def press_key(target_id: str, key: str) -> bool:
    """Press a supported browser key using CDP keyboard events."""
    if not _ready():
        return False
    return _client().press_key(target_id, key)


def set_files(target_id: str, selector: str, files: list[str]) -> bool:
    """Set files on a file input."""
    if not _ready():
        return False
    return _client().set_files(target_id, selector, files)


def scroll(target_id: str, y: int = 0, direction: str = "") -> bool:
    """Scroll a page."""
    if not _ready():
        return False
    return _client().scroll(target_id, y=y, direction=direction)


def screenshot(target_id: str, file_path: str | Path, *, selector: str = "") -> bool:
    """Capture a screenshot to a file."""
    if not _ready():
        return False
    return _client().screenshot(target_id, file_path, selector=selector)


def print_pdf(target_id: str, file_path: str | Path) -> bool:
    """Render a target as PDF to a file."""
    if not _ready():
        return False
    return _client().print_pdf(target_id, file_path)


def get_page_info(target_id: str) -> dict | None:
    """Get page title, URL, and ready state."""
    if not _ready():
        return None
    return _client().info(target_id)


def wait_for_load(target_id: str, timeout: float = 10.0, stop_event=None) -> bool:
    """Wait for page ready state to become complete.

    Returns False on timeout or when ``stop_event`` is set. Callers must
    check the stop event after a False return instead of treating it as a
    page-load failure to retry: a stop interrupt is a normal interrupt,
    not a load failure.
    """
    start = time.time()
    while time.time() - start < timeout:
        if stop_event is not None and stop_event.is_set():
            return False
        info = get_page_info(target_id)
        if info and info.get("ready") == "complete":
            return True
        if stop_event is not None:
            # 收到停止立即中断：不等满 0.5 秒轮询间隔。
            if stop_event.wait(0.5):
                return False
        else:
            time.sleep(0.5)
    return False
