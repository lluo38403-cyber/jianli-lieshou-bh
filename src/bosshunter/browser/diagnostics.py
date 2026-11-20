"""User-facing Browser Runtime diagnostics."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from rich.console import Console

from bosshunter.browser import evaluate
from bosshunter.browser.runtime import check_node_available, ensure_runtime, get_browser_config, get_runtime_url, runtime_health, runtime_targets
from bosshunter.lazy_module import lazy_module

# v30 启动提速：httpx 只在第一次真正发 HTTP 请求时才导入
# （启动首屏用不上）。代理是真实的模块全局名，函数里直接用、
# 测试照常 patch（见 bosshunter.lazy_module）。
httpx = lazy_module("httpx")


# browser.browser_choice 的合法取值（与 config.DEFAULTS 的 browser 段一致）。
BROWSER_CHOICES = ("auto", "chrome", "edge", "manual")

# 选择值 → 界面显示名（所选浏览器缺失、回落到另一个时以实际用的为准）。
_BROWSER_CHOICE_NAMES = {"auto": "浏览器", "chrome": "Chrome", "edge": "Edge", "manual": "浏览器"}


def _display_name_for_executable(path: str | None) -> str | None:
    """Return Chrome / Edge for a known executable filename, else None."""
    if not path:
        return None
    name = Path(path).name.lower()
    if "msedge" in name or name.startswith("edge"):
        return "Edge"
    if "chrome" in name or "chromium" in name:
        return "Chrome"
    return None


def resolve_browser_executable(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pick the browser executable honoring browser.browser_choice.

    返回 {"executable", "name", "choice", "fallback"}：
    - manual：只用 browser.executable_path（文件不存在时回落）；
    - chrome / edge：只用本机检测到的那一个（没找到时回落到另一个）；
    - auto：Chrome 优先，没有再用 Edge。
    executable_path 只在 manual 模式下参与检测：其他模式下先把它
    清掉再调 discover_browsers，否则手动留下的旧路径会悄悄抢占选择。
    仍找不到时 executable 为 None、name 为通用“浏览器”，调用方据此
    给面板中文提示，不要静默失败。
    """
    browser_cfg = get_browser_config(config)
    choice = str(browser_cfg.get("browser_choice") or "auto")
    if choice not in BROWSER_CHOICES:
        choice = "auto"

    manual_path = ""
    raw_path = browser_cfg.get("executable_path")
    if isinstance(raw_path, str) and raw_path.strip():
        manual_path = os.path.expandvars(raw_path.strip())

    # 非手动模式：手动路径不参与检测。
    effective_config: dict[str, Any] | None = config
    if choice != "manual" and isinstance(config, dict):
        effective_config = dict(config)
        browser_section = config.get("browser")
        if isinstance(browser_section, dict):
            section = dict(browser_section)
            section["executable_path"] = ""
            effective_config["browser"] = section
        elif "browser" not in effective_config:
            effective_config["browser"] = {"executable_path": ""}
    discovered = discover_browsers(effective_config)

    def _pick(kind: str) -> str | None:
        return discovered.get(kind) or None

    executable: str | None = None
    fallback = False
    if choice == "manual" and manual_path:
        try:
            if Path(manual_path).is_file():
                executable = manual_path
        except (OSError, ValueError):
            executable = None
        if executable is None:
            # 手动路径找不到了（删了/挪了）：回落到自动检测的 Chrome→Edge，
            # 别直接报没浏览器；界面仍可用，提示里会说明用的是回落的浏览器。
            executable = _pick("chrome") or _pick("edge")
            fallback = True
    elif choice == "chrome":
        executable = _pick("chrome")
        if executable is None:
            executable = _pick("edge")
            fallback = True
    elif choice == "edge":
        executable = _pick("edge")
        if executable is None:
            executable = _pick("chrome")
            fallback = True
    else:  # auto：Chrome 优先，没有再用 Edge。
        executable = _pick("chrome") or _pick("edge")

    name = _display_name_for_executable(executable)
    if name is None:
        # 没找到可用浏览器时按选择给名：指名要 Chrome/Edge 就报它，
        # 自动/手动就报通用名，界面不凭空编一个浏览器出来。
        name = _BROWSER_CHOICE_NAMES.get(choice, "浏览器")
    return {"executable": executable, "name": name, "choice": choice, "fallback": fallback}


def selected_browser_name(config: dict[str, Any] | None = None) -> str:
    """Return the display name of the browser the current choice resolves to."""
    return str(resolve_browser_executable(config).get("name") or "浏览器")


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
        ["点任意一个平台的“去登录”，程序会自动打开一个能连接的浏览器窗口。", "在打开的浏览器里登录要用的招聘网站，再点“检查一下能不能跑”。"],
    )


def run_browser_diagnostics(config: dict[str, Any] | None = None, allow_launch: bool = True) -> dict[str, Any]:
    """Collect Browser Runtime readiness state.

    allow_launch=False 时只连已有的可调试浏览器，绝不替用户拉新窗口
    （首页被动刷新、后台巡检、重新检测都用它）；只有用户点的“去登录”
    （或任务启动）才走默认 True。注意标签页一律从本轮已取到的 targets
    里按域名找——绝不走 browser facade 的 find_*_tab，那条路会经
    ensure_runtime 默认拉起浏览器，检测就变拉起了。
    """
    node = check_node_available()
    # 被动检查（首页/设置页每次打开都调）只等已经在跑的 runtime：5 秒
    # 没出现就不会再出现，等满 15 秒只是白白占着一个服务端线程——云电脑
    # 上每次打开页面都可能摊上这 30 多秒（ensure_runtime 还会重试一次）。
    # 用户主动点的“去登录”才保持 15 秒（要拉浏览器，值得等）。
    runtime_ready = ensure_runtime(
        config, allow_launch=allow_launch, wait_seconds=5.0 if not allow_launch else 15.0
    )
    runtime_url = get_runtime_url(config)
    health = runtime_health(config)
    targets = runtime_targets(config) if runtime_ready else None
    if runtime_ready:
        # `/targets` establishes the CDP connection. Refresh health afterwards
        # so browser product/version information is available to diagnostics.
        health = runtime_health(config) or health
    # 只读本轮已取到的 targets，不再为找标签页触发新的 ensure_runtime。
    boss_tab = _find_tab_by_domain(targets, "zhipin.com") if runtime_ready else None
    zhilian_tab = _find_tab_by_domain(targets, "zhaopin.com") if runtime_ready else None
    zhilian_page = inspect_zhilian_page(zhilian_tab) if runtime_ready and zhilian_tab else None
    # 51job / 猎聘：复用已取到的 targets 找标签页，只读已打开页面的 DOM，
    # 不新开页面、不触发额外请求。
    job51_tab = _find_tab_by_domain(targets, "51job.com") if runtime_ready else None
    liepin_tab = _find_tab_by_domain(targets, "liepin.com") if runtime_ready else None
    job51_page = inspect_51job_page(job51_tab) if runtime_ready and job51_tab else None
    liepin_page = inspect_liepin_page(liepin_tab) if runtime_ready and liepin_tab else None
    browser_product, browser_name = _browser_identity(health)
    discovered = discover_browsers(config)
    selected = resolve_browser_executable(config)
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
        # 当前浏览器选择（供“我的设置”与面板文案用）：choice 是配置值，
        # selected_browser_name 是按选择实际解析到的浏览器显示名。
        "browser_choice": selected.get("choice"),
        "selected_browser_name": selected.get("name"),
        "selected_browser_executable": selected.get("executable"),
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
