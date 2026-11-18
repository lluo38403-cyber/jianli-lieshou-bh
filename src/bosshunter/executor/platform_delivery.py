"""投递与监听适配器：智联招聘 / 前程无忧 51job / 猎聘。

BOSS 直聘走原有的聊天流（executor/sender.py 的 _send_greeting_once）。
这三个平台的投递是“打开岗位详情页 → 点投递/申请按钮 → 处理确认/选简历弹窗
→ 核验已投递”，不需要招呼语；监听是打开各平台消息中心，统计未读并取回
最新消息摘要（不代回、不代发）。

注意：现场 DOM 未经真实账号验证（本机没有招聘平台账号），选择器按各平台
公开的常规页面结构写：按钮按文字匹配（投递简历/申请职位等），确认弹窗按
文字匹配（确认/提交/选简历），成功按按钮文案变“已投递/已申请”或页面出现
成功提示判定。选择器失效时返回明确的失败原因（no_apply_button 等），
不会谎报成功。
"""

from __future__ import annotations

import json
import time
from typing import Any

from bosshunter.browser import close_tab, evaluate, new_tab, wait_for_load
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop

# 命中任一即视为岗位已关闭/下架（与 sender.JOB_CLOSED_MARKERS 同义，独立一份
# 以避免循环引用）。
JOB_CLOSED_MARKERS = (
    "访问的页面不存在", "您访问的页面不存在", "Oops!",
    "职位已关闭", "职位已经关闭", "该职位已关闭", "此职位已关闭", "岗位已关闭",
    "该职位已下线", "该职位已下架", "职位已下线", "职位已下架",
    "该职位已暂停招聘", "职位已暂停招聘", "职位暂停招聘", "停止招聘", "已停止招聘",
    "暂停招聘", "招聘已暂停", "该职位已招满", "职位已招满", "已招满",
    "该职位暂不招人", "暂不招人",
)

LOGIN_WALL_MARKERS = ("请先登录", "登录后投递", "需要登录", "请登录后查看", "登录后查看")

# 各平台投递按钮 / 已投递 / 确认弹窗 / 成功提示的文字匹配表。
DELIVER_SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    "zhilian": {
        "apply_texts": ("投递简历", "立即投递", "投递"),
        "applied_texts": ("已投递", "已申请", "投递成功"),
        "confirm_texts": ("确认投递", "确认", "确定", "提交"),
        "success_texts": ("投递成功", "简历已投递", "已投递", "投递完成"),
    },
    "51job": {
        "apply_texts": ("申请职位", "立即申请", "申请"),
        "applied_texts": ("已申请", "已投递", "申请成功"),
        "confirm_texts": ("确定", "确认", "提交申请", "申请"),
        "success_texts": ("申请成功", "已申请", "投递成功", "简历已投递"),
    },
    "liepin": {
        "apply_texts": ("投递简历", "立即投递", "投递"),
        "applied_texts": ("已投递", "已申请", "投递成功"),
        "confirm_texts": ("确认", "确定", "提交"),
        "success_texts": ("投递成功", "简历已投递", "已投递"),
    },
}

# 各平台消息中心地址（可在 monitor.inbox_urls 里覆盖）。
DEFAULT_INBOX_URLS = {
    "zhilian": "https://i.zhaopin.com/",
    "51job": "https://i.51job.com/",
    "liepin": "https://www.liepin.com/msg/",
}


def _parse_js_result(result: Any) -> dict:
    if not result:
        return {"success": False, "error": "no_response"}
    if isinstance(result, dict):
        return result
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return {"success": False, "error": "parse_error"}


def _stop_requested(stop_event) -> bool:
    return bool(stop_event and stop_event.is_set())


def _sleep_or_stop(seconds: float, stop_event) -> bool:
    if seconds <= 0:
        return _stop_requested(stop_event)
    deadline = time.monotonic() + seconds
    while True:
        if _stop_requested(stop_event):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(0.25, deadline - time.monotonic()))


def _click_button_js(texts: tuple[str, ...]) -> str:
    wanted = json.dumps(list(texts), ensure_ascii=False)
    return f"""
    (() => {{
        const wanted = {wanted};
        const isVisible = (el) => {{
            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height) return false;
            const style = getComputedStyle(el);
            return style.visibility !== 'hidden' && style.display !== 'none';
        }};
        const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'));
        for (const text of wanted) {{
            const hit = candidates.find((el) => {{
                if (!isVisible(el) || el.disabled) return false;
                const label = (el.innerText || el.textContent || '').trim();
                return label.includes(text) && label.length < 30;
            }});
            if (hit) {{
                hit.click();
                return JSON.stringify({{success: true, clicked: (hit.innerText || '').trim()}});
            }}
        }}
        return JSON.stringify({{success: false}});
    }})()
    """


def _page_state_js() -> str:
    closed = json.dumps(list(JOB_CLOSED_MARKERS), ensure_ascii=False)
    login = json.dumps(list(LOGIN_WALL_MARKERS), ensure_ascii=False)
    return f"""
    (() => {{
        const text = document.body ? document.body.innerText : '';
        const title = document.title || '';
        const closedMarkers = {closed};
        const loginMarkers = {login};
        if (title.includes('访问的页面不存在') || closedMarkers.some((m) => text.includes(m))) {{
            return JSON.stringify({{state: 'closed'}});
        }}
        if (loginMarkers.some((m) => text.includes(m))) {{
            return JSON.stringify({{state: 'login_wall', title}});
        }}
        return JSON.stringify({{state: 'ok', title, url: location.href}});
    }})()
    """


def _verify_applied_js(spec: dict[str, tuple[str, ...]]) -> str:
    applied = json.dumps(list(spec["applied_texts"]), ensure_ascii=False)
    success = json.dumps(list(spec["success_texts"]), ensure_ascii=False)
    return f"""
    (() => {{
        const text = document.body ? document.body.innerText : '';
        const applied = {applied};
        const success = {success};
        if (success.some((m) => text.includes(m))) {{
            return JSON.stringify({{applied: true, how: 'success_text'}});
        }}
        const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
        const hit = buttons.find((el) => {{
            const label = (el.innerText || el.textContent || '').trim();
            return label && label.length < 30 && applied.some((m) => label.includes(m));
        }});
        if (hit) return JSON.stringify({{applied: true, how: 'button_text', label: hit.innerText.trim()}});
        return JSON.stringify({{applied: false}});
    }})()
    """


def _handle_post_apply_dialogs_js(spec: dict[str, tuple[str, ...]]) -> str:
    """处理投递后可能出现的选简历弹窗与确认弹窗：先选第一份简历，再点确认。"""
    confirm = json.dumps(list(spec["confirm_texts"]), ensure_ascii=False)
    return f"""
    (() => {{
        const actions = [];
        const isVisible = (el) => {{
            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height) return false;
            const style = getComputedStyle(el);
            return style.visibility !== 'hidden' && style.display !== 'none';
        }};
        // 选简历：点第一个可选的简历行/单选框。
        const resumeRows = Array.from(document.querySelectorAll('[class*="resume"] input[type="radio"], [class*="resume"] li, [class*="resume"] label'))
            .filter(isVisible);
        if (resumeRows.length) {{
            resumeRows[0].click();
            actions.push('picked_resume');
        }}
        const confirmTexts = {confirm};
        const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]'));
        for (const text of confirmTexts) {{
            const hit = buttons.find((el) => {{
                if (!isVisible(el) || el.disabled) return false;
                const label = (el.innerText || el.textContent || '').trim();
                return label.includes(text) && label.length < 30;
            }});
            if (hit) {{
                hit.click();
                actions.push('clicked_confirm:' + hit.innerText.trim());
                break;
            }}
        }}
        return JSON.stringify({{success: true, actions}});
    }})()
    """


def _deliver_via_detail_page(job: dict, throttle_config: dict, platform: str) -> tuple[dict, str | None]:
    """打开岗位详情页完成一次投递。返回 (result, target_id)。"""
    spec = DELIVER_SPECS[platform]
    stop_event = throttle_config.get("_workbench_stop_event")
    url = str(job.get("url") or "").strip()
    if not url:
        return {"success": False, "error": "no_job_url", "history_detail": "岗位没有详情页地址，无法投递", "skip_backoff": True}, None

    access_guard = throttle_config.get("_platform_access_guard")
    if isinstance(access_guard, PlatformAccessGuard):
        try:
            access_guard.reserve("job_page")
        except PlatformSafetyStop as exc:
            return {
                "success": False,
                "error": exc.reason,
                "history_detail": "为了账户安全，已达到平台页面访问上限或仍处于风险冷却",
            }, None

    target_id = new_tab(url, background=True)
    if not target_id:
        return {"success": False, "error": "open_page_failed", "history_detail": "无法打开岗位详情页", "skip_backoff": True}, None
    if _stop_requested(stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    wait_for_load(target_id, timeout=15)
    if _sleep_or_stop(2, stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    state = _parse_js_result(evaluate(target_id, _page_state_js()))
    if state.get("state") == "closed":
        close_tab(target_id)
        return {"success": False, "error": "job_page_unavailable", "history_detail": "岗位已关闭或下架", "skip_backoff": True}, None
    if state.get("state") == "login_wall":
        close_tab(target_id)
        return {"success": False, "error": "needs_login", "history_detail": "平台要求先登录，请在浏览器里登录该平台后重试", "skip_backoff": True}, None

    # 先核验：可能已经投过。
    verify = _parse_js_result(evaluate(target_id, _verify_applied_js(spec)))
    if verify.get("applied"):
        close_tab(target_id)
        return {"success": True, "already_applied": True, "history_detail": "该岗位已投递过"}, None

    clicked = None
    for _ in range(10):
        if _stop_requested(stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
        clicked = _parse_js_result(evaluate(target_id, _click_button_js(spec["apply_texts"])))
        if clicked.get("success"):
            break
        if _sleep_or_stop(1, stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
    if not clicked or not clicked.get("success"):
        state = _parse_js_result(evaluate(target_id, _page_state_js()))
        close_tab(target_id)
        if state.get("state") == "login_wall":
            return {"success": False, "error": "needs_login", "history_detail": "平台要求先登录，请在浏览器里登录该平台后重试", "skip_backoff": True}, None
        return {"success": False, "error": "no_apply_button", "history_detail": "详情页没找到投递按钮（页面结构可能变了）", "skip_backoff": True}, None

    if _sleep_or_stop(2, stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
    _parse_js_result(evaluate(target_id, _handle_post_apply_dialogs_js(spec)))
    if _sleep_or_stop(2, stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    verify = _parse_js_result(evaluate(target_id, _verify_applied_js(spec)))
    close_tab(target_id)
    if verify.get("applied"):
        return {"success": True, "history_detail": f"已投递（{verify.get('how', '')}）"}, None
    return {
        "success": False,
        "error": "apply_unverified",
        "history_detail": "点了投递按钮，但页面上没确认到已投递状态，请人工检查",
        "skip_backoff": True,
    }, None


def deliver_job(job: dict, throttle_config: dict) -> tuple[dict, str | None]:
    """按岗位来源平台投递一次。返回 (result, target_id)，与 BOSS 发送器同形。"""
    platform = str(job.get("source_platform") or "").strip()
    if platform not in DELIVER_SPECS:
        return {
            "success": False,
            "error": "unsupported_platform",
            "history_detail": f"不支持的平台：{platform or '未知'}",
            "skip_backoff": True,
        }, None
    return _deliver_via_detail_page(job, throttle_config, platform)


def check_inbox(platform: str, config: dict) -> dict:
    """打开一个平台的消息中心，统计未读并取回最新消息摘要。

    不代回、不代发；只报告“有 N 条未读 + 最新几条摘要”，由用户去平台里看。
    登录墙 / 打不开 / 结构对不上时如实返回原因。
    """
    inbox_urls = dict(DEFAULT_INBOX_URLS)
    overrides = (config.get("monitor", {}) or {}).get("inbox_urls") or {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if value:
                inbox_urls[str(key)] = str(value)
    url = inbox_urls.get(platform)
    if not url:
        return {"platform": platform, "error": "no_inbox_url", "unread": 0, "samples": []}
    stop_event = config.get("_workbench_stop_event")

    target_id = new_tab(url, background=True)
    if not target_id:
        return {"platform": platform, "error": "open_page_failed", "unread": 0, "samples": []}
    try:
        wait_for_load(target_id, timeout=15)
        if _sleep_or_stop(2, stop_event):
            return {"platform": platform, "error": "stopped", "unread": 0, "samples": []}
        result = _parse_js_result(evaluate(target_id, """
        (() => {
            const text = document.body ? document.body.innerText : '';
            const loginMarkers = ['请先登录', '登录后查看', '需要登录'];
            if (loginMarkers.some((m) => text.includes(m))) {
                return JSON.stringify({login_wall: true, unread: 0, samples: []});
            }
            const unreadEls = Array.from(document.querySelectorAll(
                '[class*="unread"], [class*="badge"], [class*="red-dot"], [class*="new-msg"]'
            )).filter((el) => {
                const rect = el.getBoundingClientRect();
                if (!rect.width || !rect.height) return false;
                const label = (el.innerText || el.textContent || '').trim();
                return label !== '' && label !== '0';
            });
            let unread = 0;
            for (const el of unreadEls) {
                const num = parseInt((el.innerText || '').trim(), 10);
                unread += Number.isNaN(num) ? 1 : num;
            }
            const rows = Array.from(document.querySelectorAll(
                '[class*="message"], [class*="chat-item"], [class*="msg-item"], [class*="conversation"]'
            )).filter((el) => {
                const rect = el.getBoundingClientRect();
                return rect.width && rect.height;
            }).slice(0, 5).map((el) => (el.innerText || '').trim().slice(0, 80)).filter(Boolean);
            return JSON.stringify({login_wall: false, unread, samples: rows});
        })()
        """))
        if result.get("login_wall"):
            return {"platform": platform, "error": "needs_login", "unread": 0, "samples": []}
        return {
            "platform": platform,
            "error": result.get("error") or "",
            "unread": int(result.get("unread") or 0),
            "samples": list(result.get("samples") or []),
        }
    finally:
        close_tab(target_id)
