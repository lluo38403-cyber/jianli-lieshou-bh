"""User-facing Browser Runtime diagnostics."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console

from bosshunter.browser import evaluate, find_boss_tab, find_zhilian_tab
from bosshunter.browser.runtime import check_node_available, ensure_runtime, get_browser_config, get_runtime_url, runtime_health, runtime_targets


# 各平台页面状态检测脚本模板：只读已打开标签页的 DOM，不开新页、不发请求。
# __SEARCH_SELECTOR__ 处按平台填搜索框选择器；其余登录墙/拦截文案三平台通用。
_PAGE_STATE_SCRIPT_TEMPLATE = """
(() => {
  const text = document.body ? document.body.innerText : '';
  const searchInput = document.querySelector('__SEARCH_SELECTOR__');
  if (/验证码|滑块|访问频繁|频率限制|账号异常|拒绝访问/.test(text)) {
    return JSON.stringify({status: 'blocked', message: '__PLATFORM_NAME__页面受到验证码、频率限制或账号异常拦截'});
  }
  const strongLoginWallText = /登录查看更多|登录查看全部|立即登录/.test(text);
  const loginWallText = /请先登录|请登录|登录后(?:查看|继续|获取)|登录失效|账号登录|扫码登录/.test(text);
  const loginDialog = Boolean(document.querySelector('[role="dialog"], .login-dialog, [class*="login-modal"], [class*="login-dialog"]'));
  if (strongLoginWallText || (loginWallText && (!searchInput || loginDialog))) {
    return JSON.stringify({status: 'login_required', message: '__PLATFORM_NAME__页面要求登录'});
  }
  if (searchInput) {
    return JSON.stringify({status: 'ready', message: '已发现__PLATFORM_NAME__搜索框，未发现登录墙'});
  }
  return JSON.stringify({status: 'selector_changed', message: '未识别到__PLATFORM_NAME__搜索页结构'});
})()
"""

ZHILIAN_PAGE_STATE_SCRIPT = _PAGE_STATE_SCRIPT_TEMPLATE.replace(
    "__SEARCH_SELECTOR__",
    'input[placeholder="输入职位、公司等搜索"], input[placeholder*="职位、公司"]',
).replace("__PLATFORM_NAME__", "智联")

_JOB51_PAGE_STATE_SCRIPT = _PAGE_STATE_SCRIPT_TEMPLATE.replace(
    "__SEARCH_SELECTOR__",
    'input[placeholder*="职位"], input[placeholder*="搜索职位"], .search-input input, input.el-input__inner',
).replace("__PLATFORM_NAME__", "前程无忧")

_LIEPIN_PAGE_STATE_SCRIPT = _PAGE_STATE_SCRIPT_TEMPLATE.replace(
    "__SEARCH_SELECTOR__",
    'input[placeholder*="职位"], input[placeholder*="搜索"], .search-box input, .search-input input',
).replace("__PLATFORM_NAME__", "猎聘")

_PAGE_STATE_SCRIPTS = {
    "zhilian": ZHILIAN_PAGE_STATE_SCRIPT,
    "51job": _JOB51_PAGE_STATE_SCRIPT,
    "liepin": _LIEPIN_PAGE_STATE_SCRIPT,
}

_PLATFORM_DISPLAY_NAMES = {"zhilian": "智联", "51job": "前程无忧", "liepin": "猎聘"}


def _inspect_page(target: dict[str, Any] | None, platform: str, script: str) -> dict[str, str]:
    """Inspect only the visible DOM state of an existing tab (no new page)."""
    display = _PLATFORM_DISPLAY_NAMES.get(platform, platform)
    if not isinstance(target, dict):
        return {"status": "missing", "message": f"未发现{display}招聘页面"}
    target_id = str(target.get("targetId") or target.get("id") or "").strip()
    if not target_id:
        return {"status": "unknown", "message": f"{display}页面缺少可检查的标签页标识"}
    try:
        raw = evaluate(target_id, script, timeout=5)
    except Exception:
        return {"status": "unknown", "message": f"{display}页面状态检查失败，请重新检查浏览器连接"}

    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            return {str(key): str(value) for key, value in payload.items()}
    return {"status": "unknown", "message": f"{display}页面状态检查未返回有效结果"}


def inspect_platform_page(target: dict[str, Any] | None, platform: str) -> dict[str, str]:
    """Inspect an existing 智联 / 51job / 猎聘 tab's DOM login state."""
    script = _PAGE_STATE_SCRIPTS.get(platform)
    if script is None:
        return {"status": "unknown", "message": f"不支持的平台：{platform}"}
    return _inspect_page(target, platform, script)


def inspect_zhilian_page(target: dict[str, Any] | None) -> dict[str, str]:
    """Inspect only the visible DOM state of an existing Zhilian tab."""
    return inspect_platform_page(target, "zhilian")


def inspect_51job_page(target: dict[str, Any] | None) -> dict[str, str]:
    """Inspect only the visible DOM state of an existing 51job tab."""
    return inspect_platform_page(target, "51job")


def inspect_liepin_page(target: dict[str, Any] | None) -> dict[str, str]:
    """Inspect only the visible DOM state of an existing Liepin tab."""
    return inspect_platform_page(target, "liepin")


def _find_tab_by_domain(targets: list[dict[str, Any]] | None, domain: str) -> dict[str, Any] | None:
    """Find a tab by URL domain from an already-fetched target list.

    复用 run_browser_diagnostics 已取到的 targets，不再为每个平台
    单独调一次 get_page_targets（每次都会重新触发 ensure_runtime）。
    """
    for target in targets or []:
        if isinstance(target, dict) and domain in str(target.get("url") or ""):
            return target
    return None


def discover_browsers(config: dict[str, Any] | None = None) -> dict[str, str | None]:
    """Find installed Chrome / Edge executables without raising.

    Windows 常见安装路径 + 注册表 App Paths 为主，Linux/mac 路径为辅；
    也认用户手动选的 browser.executable_path。找不到只返回 None，
    绝不报错——调用方据此区分“没装浏览器”和“装了但连不上”。
    """
    found: dict[str, str | None] = {"chrome": None, "edge": None}

    def _classify(path: str) -> str:
        return "edge" if "msedge" in Path(path).name.lower() else "chrome"

    def _take(path: str | None) -> None:
        if not path:
            return
        try:
            if not Path(path).is_file():
                return
        except (OSError, ValueError):
            return
        kind = _classify(path)
        if found[kind] is None:
            found[kind] = str(path)

    try:
        configured = get_browser_config(config).get("executable_path")
        if isinstance(configured, str) and configured.strip():
            _take(os.path.expandvars(configured.strip()))
    except Exception:
        pass

    try:
        if os.name == "nt":
            for env_var, subpath, kind in (
                ("ProgramFiles", "Google\\Chrome\\Application\\chrome.exe", "chrome"),
                ("ProgramFiles(x86)", "Google\\Chrome\\Application\\chrome.exe", "chrome"),
                ("LOCALAPPDATA", "Google\\Chrome\\Application\\chrome.exe", "chrome"),
                ("ProgramFiles(x86)", "Microsoft\\Edge\\Application\\msedge.exe", "edge"),
                ("ProgramFiles", "Microsoft\\Edge\\Application\\msedge.exe", "edge"),
                ("LOCALAPPDATA", "Microsoft\\Edge\\Application\\msedge.exe", "edge"),
            ):
                root = os.environ.get(env_var)
                if root and found[kind] is None:
                    _take(str(Path(root) / subpath))
            # 注册表 App Paths：覆盖改过默认安装位置的情况。
            try:
                import winreg

                for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                    for exe, kind in (("chrome.exe", "chrome"), ("msedge.exe", "edge")):
                        if found[kind] is not None:
                            continue
                        try:
                            with winreg.OpenKey(
                                hive, f"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{exe}"
                            ) as key:
                                value, _ = winreg.QueryValueEx(key, "")
                            _take(str(value))
                        except OSError:
                            continue
            except (ImportError, OSError):
                pass
        else:
            for name, kind in (
                ("google-chrome", "chrome"),
                ("google-chrome-stable", "chrome"),
                ("chromium", "chrome"),
                ("chromium-browser", "chrome"),
                ("microsoft-edge", "edge"),
                ("microsoft-edge-stable", "edge"),
                ("microsoft-edge-dev", "edge"),
            ):
                if found[kind] is None:
                    _take(shutil.which(name))
            for path, kind in (
                ("/opt/google/chrome/chrome", "chrome"),
                ("/usr/bin/google-chrome", "chrome"),
                ("/usr/bin/chromium", "chrome"),
                ("/usr/bin/chromium-browser", "chrome"),
                ("/opt/microsoft/msedge/msedge", "edge"),
                ("/usr/bin/microsoft-edge", "edge"),
                ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chrome"),
                ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge", "edge"),
            ):
                if found[kind] is None:
                    _take(path)
    except Exception:
        pass
    return found


def _browser_problem(chrome_connected: bool, discovered: dict[str, str | None]) -> tuple[str | None, str, list[str]]:
    """Classify a browser failure in plain Chinese (no commands, no paths to run).

    error_kind: None 正常 / 'not_found' 未找到可用浏览器 /
    'connection_failed' 运行时连接失败。
    """
    if chrome_connected:
        return None, "浏览器连接正常。", ["可以直接开始采集或投递任务。"]
    if not discovered.get("chrome") and not discovered.get("edge"):
        return (
            "not_found",
            "这台电脑上没找到 Chrome 或 Edge 浏览器。",
            ["请先安装 Chrome 或 Edge 浏览器。", "装好后重新打开软件，再点一次重新检测。"],
        )
    return (
        "connection_failed",
        "浏览器已经装好了，但是现在连不上。",
        ["点“一键启动浏览器”，程序会自动打开一个能连接的浏览器窗口。", "在打开的浏览器里登录要用的招聘网站，再点“检查一下能不能跑”。"],
    )


def run_browser_diagnostics(config: dict[str, Any] | None = None, allow_launch: bool = True) -> dict[str, Any]:
    """Collect Browser Runtime readiness state.

    allow_launch=False 时只连已有的可调试浏览器，绝不替用户拉新窗口
    （首页被动刷新、后台巡检用）；用户点的重新检测、检查、去登录走
    默认 True，程序会自动拉起带调试端口的浏览器实例。
    """
    node = check_node_available()
    runtime_ready = ensure_runtime(config, allow_launch=allow_launch)
    runtime_url = get_runtime_url(config)
    health = runtime_health(config)
    targets = runtime_targets(config) if runtime_ready else None
    if runtime_ready:
        # `/targets` establishes the CDP connection. Refresh health afterwards
        # so browser product/version information is available to diagnostics.
        health = runtime_health(config) or health
    boss_tab = find_boss_tab() if runtime_ready else None
    zhilian_tab = find_zhilian_tab() if runtime_ready else None
    zhilian_page = inspect_zhilian_page(zhilian_tab) if runtime_ready and zhilian_tab else None
    # 51job / 猎聘：复用已取到的 targets 找标签页，只读已打开页面的 DOM，
    # 不新开页面、不触发额外请求。
    job51_tab = _find_tab_by_domain(targets, "51job.com") if runtime_ready else None
    liepin_tab = _find_tab_by_domain(targets, "liepin.com") if runtime_ready else None
    job51_page = inspect_51job_page(job51_tab) if runtime_ready and job51_tab else None
    liepin_page = inspect_liepin_page(liepin_tab) if runtime_ready and liepin_tab else None
    browser_product, browser_name = _browser_identity(health)
    discovered = discover_browsers(config)
    chrome_connected = isinstance(targets, list)
    error_kind, message, next_steps = _browser_problem(chrome_connected, discovered)

    errors: list[str] = []
    if not node.get("available") and not runtime_ready:
        errors.append("Node.js is required for BossHunter Browser Runtime.")
    if not runtime_ready:
        if health and health.get("runtime") != "bosshunter":
            errors.append("Runtime port is occupied by a non-BossHunter service.")
        else:
            errors.append("BossHunter Browser Runtime is not ready.")
    if runtime_ready and targets is None:
        errors.append("Chrome is not connected to Browser Runtime.")

    return {
        "node": node,
        "runtime": runtime_ready,
        "chrome": chrome_connected,
        "targets": targets or [],
        "boss_tab": boss_tab,
        "zhilian_tab": zhilian_tab,
        "zhilian_page": zhilian_page,
        "job51_tab": job51_tab,
        "job51_page": job51_page,
        "liepin_tab": liepin_tab,
        "liepin_page": liepin_page,
        "errors": errors,
        "runtime_url": runtime_url,
        "health": health,
        "browser_product": browser_product,
        "browser_name": browser_name,
        "discovered": discovered,
        "error_kind": error_kind,
        "message": message,
        "next_steps": next_steps,
    }


def _browser_identity(health: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Return browser identity, including fallback support for older runtimes."""
    health = health or {}
    product = health.get("browserProduct")
    name = health.get("browserName")
    if product or name:
        return product, name

    port = health.get("chromePort")
    if not port:
        return None, None
    try:
        result = httpx.get(f"http://127.0.0.1:{int(port)}/json/version", timeout=2, trust_env=False)
        result.raise_for_status()
        product = result.json().get("Browser")
    except (httpx.HTTPError, TypeError, ValueError):
        return None, None

    if isinstance(product, str) and product.startswith("Edg/"):
        return product, "Microsoft Edge"
    if isinstance(product, str) and product.startswith("Chrome/"):
        return product, "Google Chrome"
    return product if isinstance(product, str) else None, None


def print_browser_diagnostics(config: dict[str, Any] | None = None, console: Console | None = None) -> bool:
    """Print actionable runtime diagnostics. Returns True when runtime and Chrome are ready."""
    out = console or Console()
    out.print("[bold]正在检测 Browser Runtime...[/bold]")
    result = run_browser_diagnostics(config)

    node = result["node"]
    if node.get("available"):
        out.print(f"[green]✓[/green] Node.js 可用: {node.get('version') or 'unknown'}")
    else:
        out.print("[red]✗[/red] Node.js 不可用")
        out.print("  BossHunter 内置浏览器运行时需要 Node.js。请安装 Node.js 22+ 后重试。")

    if result["runtime"]:
        out.print(f"[green]✓[/green] BossHunter CDP Runtime 已启动: {result['runtime_url']}")
    else:
        out.print(f"[red]✗[/red] BossHunter CDP Runtime 未就绪: {result['runtime_url']}")
        if any("non-BossHunter" in error for error in result["errors"]):
            out.print("  端口已被非 BossHunter 服务占用，请停止旧的 CDP Proxy 或在 config.yaml 中修改 browser.proxy_port。")

    if result["chrome"]:
        out.print("[green]✓[/green] 已连接 Chrome")
    elif any("non-BossHunter" in error for error in result["errors"]):
        out.print("[yellow]![/yellow] 暂未检测 Chrome：Browser Runtime 端口被占用，无法启动内置 Runtime。")
    else:
        out.print("[red]✗[/red] 未发现 Chrome 调试端口。")
        out.print("  请打开 Chrome，访问 chrome://inspect/#remote-debugging，勾选 Allow remote debugging。")
        out.print("  或使用: chrome.exe --remote-debugging-port=9222")

    if result["boss_tab"]:
        out.print(f"[green]✓[/green] 发现 BOSS直聘 页面: {result['boss_tab'].get('title', '')}")
    else:
        out.print("[yellow]![/yellow] 未发现 BOSS直聘 页面，请在 Chrome 中打开 www.zhipin.com 并登录")

    return bool(result["runtime"] and result["chrome"])
