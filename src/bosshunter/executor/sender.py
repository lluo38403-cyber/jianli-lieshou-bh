"""Sender module - Auto-send greetings with throttle control."""

import time
from bosshunter.employment import internship_rejection
from contextlib import nullcontext
import json
from threading import Event
from urllib.parse import urljoin
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from bosshunter.antibot import (
    DEFAULT_PLATFORM_ORDER,
    CircuitBreaker,
    group_jobs_by_platform,
    handle_page_risk,
    human_browse,
    micro_pause,
    platform_daily_limits,
    platform_display_name,
    resolve_risk_profile,
    sample_rest_delay,
    sample_stagger_delay,
    should_stage_rest,
)
from bosshunter.browser import (
    new_tab,
    close_tab,
    evaluate,
    click_at,
    get_page_targets,
    navigate,
    press_key,
    type_text,
    wait_for_load,
)
from bosshunter.db import (
    get_db, get_jobs_ready_to_send, update_job_status, update_job_last_error,
    add_history, add_risk_event, set_platform_safety_lock,
    count_sent_today, count_sent_today_by_platform,
    record_platform_login_state,
)
from bosshunter.ai.greeter import effective_greeting
from bosshunter.collection.capabilities import platform_supports
from bosshunter.executor.platform_delivery import deliver_job
from bosshunter.cancellation import get_pause_event, wait_while_paused
from bosshunter.throttle import RequestThrottle, ProgressiveBackoff
from bosshunter.platform_safety import PlatformAccessGuard, PlatformSafetyStop

console = Console()

CHAT_BUTTON_SELECTOR = (
    'a[redirect-url*="/web/geek/chat"], '
    'a[data-url*="/friend/add"], '
    'a.btn-startchat, '
    '[ka="job_detail_chat"], '
    '[ka^="go_chat"], '
    '[ka*="gochat"], '
    '.op-btn-chat, '
    '.btn-startchat-wrap'
)

# 命中任一即视为岗位已关闭/下架/停止招聘/招满。用于岗位详情页失败时推断真实业务状态。
JOB_CLOSED_MARKERS = (
    "访问的页面不存在", "您访问的页面不存在", "Oops!",
    "职位已关闭", "职位已经关闭", "该职位已关闭", "此职位已关闭", "岗位已关闭",
    "该职位已下线", "该职位已下架", "职位已下线", "职位已下架",
    "该职位已暂停招聘", "职位已暂停招聘", "职位暂停招聘", "停止招聘", "已停止招聘",
    "暂停招聘", "招聘已暂停", "该职位已招满", "职位已招满", "已招满",
    "该职位暂不招人", "暂不招人",
)

CHAT_BUTTON_SCRIPT_FOR_TESTS = """
(() => {
    const selectors = [
        'a[redirect-url*="/web/geek/chat"]',
        'a[data-url*="/friend/add"]',
        'a.btn-startchat',
        '[ka="job_detail_chat"]',
        '[ka^="go_chat"]',
        '[ka*="gochat"]',
        '.op-btn-chat',
        '.btn-startchat-wrap'
    ];
    const candidates = selectors.flatMap((selector, priority) =>
        Array.from(document.querySelectorAll(selector)).map((el) => ({el, selector, priority}))
    );
    const elementState = (el) => {
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        const visible = !!(
            rect.width && rect.height &&
            style.display !== 'none' &&
            style.visibility !== 'hidden' &&
            style.pointerEvents !== 'none'
        );
        const inViewport = visible && rect.bottom > 0 && rect.right > 0 &&
            rect.top < innerHeight && rect.left < innerWidth;
        const x = Math.min(Math.max(rect.x + rect.width / 2, 0), innerWidth - 1);
        const y = Math.min(Math.max(rect.y + rect.height / 2, 0), innerHeight - 1);
        const top = inViewport ? document.elementFromPoint(x, y) : null;
        const topmost = !!(top && (top === el || el.contains(top)));
        return {rect, visible, inViewport, topmost};
    };
    const isVisible = (el) => elementState(el).visible;
    const score = (item) => {
        const el = item.el;
        const text = (el.innerText || el.textContent || '').trim();
        const ka = el.getAttribute('ka') || '';
        const redirectUrl = el.getAttribute('redirect-url') || '';
        const dataUrl = el.getAttribute('data-url') || '';
        const tagName = String(el.tagName || '').toLowerCase();
        let value = 0;
        if (isVisible(el)) value += 1000;
        const state = elementState(el);
        if (state.inViewport) value += 500;
        if (state.topmost) value += 300;
        if (tagName === 'a') value += 200;
        if (redirectUrl.includes('/web/geek/chat')) value += 300;
        if (dataUrl.includes('/friend/add')) value += 250;
        if (el.classList && el.classList.contains('btn-startchat')) value += 120;
        if (text.includes('沟通')) value += 80;
        if (ka === 'job_detail_chat' || ka.includes('go_chat') || ka.includes('gochat')) value += 60;
        if (el.classList && el.classList.contains('btn-startchat-wrap')) value -= 100;
        return value - item.priority;
    };
    const matches = candidates
        .filter((item) => {
            const el = item.el;
            const text = (el.innerText || el.textContent || '').trim();
            const ka = el.getAttribute('ka') || '';
            const redirectUrl = el.getAttribute('redirect-url') || '';
            const dataUrl = el.getAttribute('data-url') || '';
            return (
                text.includes('沟通') ||
                redirectUrl.includes('/web/geek/chat') ||
                dataUrl.includes('/friend/add') ||
                ka === 'job_detail_chat' ||
                ka.includes('go_chat') ||
                ka.includes('gochat')
            );
        })
        .sort((a, b) => score(b) - score(a));
    const btn = matches[0] && matches[0].el;
    if (!btn) return JSON.stringify({
        success: false,
        error: 'no_chat_button',
        candidates: candidates.map((item) => {
            const el = item.el;
            const text = (el.innerText || el.textContent || '').trim();
            return {
                text,
                ka: el.getAttribute('ka'),
                className: String(el.className || ''),
                tagName: el.tagName,
                redirectUrl: el.getAttribute('redirect-url'),
                dataUrl: el.getAttribute('data-url'),
                visible: isVisible(el)
            };
        })
    });
    btn.scrollIntoView({block: 'center', inline: 'center'});
    const rect = btn.getBoundingClientRect();
    btn.click();
    return JSON.stringify({
        success: true,
        interaction: 'dom_click',
        x: rect.x + rect.width / 2,
        y: rect.y + rect.height / 2,
        button_text: (btn.innerText || btn.textContent || '').trim(),
        ka: btn.getAttribute('ka'),
        className: String(btn.className || ''),
        tagName: btn.tagName,
        redirectUrl: btn.getAttribute('redirect-url'),
        dataUrl: btn.getAttribute('data-url'),
        visible: isVisible(btn)
    });
})()
"""


def _parse_js_result(result) -> dict:
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
    if stop_event:
        return bool(stop_event.wait(seconds))
    time.sleep(seconds)
    return False


def _detect_greet_popup(target_id: str) -> dict:
    detect_popup_js = """
    (() => {
        const visible = (element) => element && !!(
            element.offsetWidth || element.offsetHeight || element.getClientRects().length
        );
        const explicitPreset = Array.from(document.querySelectorAll('.greet-boss-pop, .greet-pop'))
            .find((element) => visible(element));
        if (explicitPreset) {
            return JSON.stringify({success: true, popup: true, kind: 'preset_greeting'});
        }

        const startChat = Array.from(document.querySelectorAll('.dialog-wrap.startchat-dialog'))
            .find((element) => visible(element) && element.querySelector('textarea.input-area'));
        if (startChat) {
            return JSON.stringify({success: true, popup: true, kind: 'startchat_dialog'});
        }

        const preset = Array.from(document.querySelectorAll('.dialog-wrap'))
            .find((element) => {
                if (!visible(element)) return false;
                const text = (element.innerText || element.textContent || '').trim();
                const hasEditableGreeting = !!element.querySelector('textarea.input-area');
                return !hasEditableGreeting && /预设招呼语|默认招呼语|自动招呼语|打招呼语/.test(text);
            });
        if (preset) {
            return JSON.stringify({success: true, popup: true, kind: 'preset_greeting'});
        }
        return JSON.stringify({success: true, popup: false, kind: null});
    })()
    """
    return _parse_js_result(evaluate(target_id, detect_popup_js))


def _is_preset_greeting_popup(state: dict) -> bool:
    if state.get("action") == "no_popup":
        return False
    if not state.get("popup"):
        return False
    return state.get("kind") in {None, "preset_greeting"}


def _confirm_preset_greeting(target_id: str) -> dict:
    result = _parse_js_result(evaluate(target_id, """
    (() => {
        const visible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return !!(rect.width && rect.height && style.display !== 'none'
                && style.visibility !== 'hidden' && style.pointerEvents !== 'none');
        };
        const dialogs = Array.from(document.querySelectorAll('.greet-boss-pop, .greet-pop, .dialog-wrap'))
            .filter(visible);
        const popup = dialogs.find((element) =>
            element.matches('.greet-boss-pop, .greet-pop')
            || /预设招呼语|默认招呼语|自动招呼语|打招呼语/.test(
                (element.innerText || element.textContent || '').trim()
            )
        );
        if (!popup) return JSON.stringify({success: false, error: 'preset_popup_missing'});
        const buttons = Array.from(popup.querySelectorAll(
            '[ka="dialog_confirm"], .btn-sure, button, [role="button"]'
        )).filter((element) => {
            if (!visible(element) || element.disabled || element.classList.contains('disabled')) return false;
            const text = (element.innerText || element.textContent || '').trim();
            return element.matches('[ka="dialog_confirm"], .btn-sure')
                || /确定|确认|继续|开始沟通|立即沟通/.test(text);
        });
        const button = buttons[0];
        if (!button) return JSON.stringify({success: false, error: 'preset_confirm_missing'});
        button.scrollIntoView({block: 'center', inline: 'center'});
        button.click();
        return JSON.stringify({success: true, action: 'preset_confirmed'});
    })()
    """))
    if not result.get("success"):
        return {
            **result,
            "history_detail": "检测到平台招呼语，但无法确认招呼语弹窗",
            "skip_backoff": True,
        }
    return {"success": True, "action": "preset_confirmed"}


def _submit_startchat_greeting(target_id: str, greeting: str) -> dict:
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    input_state = _parse_js_result(evaluate(target_id, """
    (() => {
        const visible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return !!(rect.width && rect.height && style.display !== 'none'
                && style.visibility !== 'hidden' && style.pointerEvents !== 'none');
        };
        const dialog = Array.from(document.querySelectorAll('.dialog-wrap.startchat-dialog'))
            .find(visible);
        const input = dialog && Array.from(dialog.querySelectorAll('textarea.input-area, textarea'))
            .find(visible);
        if (!input) return JSON.stringify({success: false, error: 'startchat_input_missing'});
        input.scrollIntoView({block: 'center', inline: 'center'});
        const rect = input.getBoundingClientRect();
        return JSON.stringify({
            success: true,
            x: rect.x + rect.width / 2,
            y: rect.y + rect.height / 2
        });
    })()
    """))
    if not input_state.get("success"):
        return {
            **input_state,
            "history_detail": "首次沟通弹窗中未找到招呼语输入框",
            "skip_backoff": True,
        }
    if not click_at(target_id, f"{input_state['x']},{input_state['y']}"):
        return {"success": False, "error": "startchat_input_focus_failed", "skip_backoff": True}
    if not press_key(target_id, "SelectAll") or not press_key(target_id, "Backspace"):
        return {"success": False, "error": "startchat_input_clear_failed", "skip_backoff": True}
    if not type_text(target_id, greeting, human=True):
        return {"success": False, "error": "startchat_trusted_input_failed", "skip_backoff": True}

    submit_state = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const visible = (element) => {{
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return !!(rect.width && rect.height && style.display !== 'none'
                && style.visibility !== 'hidden' && style.pointerEvents !== 'none');
        }};
        const dialog = Array.from(document.querySelectorAll('.dialog-wrap.startchat-dialog'))
            .find(visible);
        const input = dialog && Array.from(dialog.querySelectorAll('textarea.input-area, textarea'))
            .find(visible);
        if (!input || normalize(input.value) !== normalize({greeting_escaped})) {{
            return JSON.stringify({{success: false, error: 'startchat_input_not_filled'}});
        }}
        const buttons = Array.from(dialog.querySelectorAll(
            '.send-message, [ka="dialog_confirm"], .btn-sure, .btn-send, '
            + '.send-btn, button, [role="button"]'
        )).filter((element) => {{
            if (!visible(element) || element.disabled || element.classList.contains('disabled')) return false;
            const text = (element.innerText || element.textContent || '').trim();
            return element.matches(
                '.send-message, [ka="dialog_confirm"], .btn-sure, .btn-send, .send-btn'
            ) || /发送|确定|开始沟通|立即沟通/.test(text);
        }});
        const button = buttons[0];
        if (!button) return JSON.stringify({{success: false, error: 'startchat_submit_missing'}});
        button.scrollIntoView({{block: 'center', inline: 'center'}});
        const rect = button.getBoundingClientRect();
        return JSON.stringify({{
            success: true,
            x: rect.x + rect.width / 2,
            y: rect.y + rect.height / 2
        }});
    }})()
    """))
    if not submit_state.get("success"):
        return {
            **submit_state,
            "history_detail": "首次沟通招呼语未被 BOSS 输入组件接受",
            "skip_backoff": True,
        }
    if not click_at(target_id, f"{submit_state['x']},{submit_state['y']}"):
        return {
            "success": False,
            "error": "startchat_submit_click_failed",
            "history_detail": "首次沟通招呼语已填写，但真实提交点击失败",
            "skip_backoff": True,
        }
    return {"success": True, "action": "first_contact_submitted"}


def _navigate_to_chat_redirect(target_id: str, click_result: dict) -> bool:
    """Reuse BOSS's own chat destination without foregrounding the job tab."""
    redirect_url = str(click_result.get("redirectUrl") or "").strip()
    if not redirect_url.startswith("/web/geek/chat"):
        return False
    return navigate(target_id, urljoin("https://www.zhipin.com", redirect_url))


def _handle_greet_popup(
    target_id: str, greeting: str, click_result: dict | None = None,
) -> dict:
    state = _detect_greet_popup(target_id)
    if not state.get("success"):
        return {
            "success": False,
            "error": state.get("error", "popup_detection_failed"),
            "history_detail": "无法识别首次沟通页面状态",
            "skip_backoff": True,
        }
    if _is_preset_greeting_popup(state):
        return _confirm_preset_greeting(target_id)
    if state.get("kind") == "startchat_dialog":
        if click_result and _navigate_to_chat_redirect(target_id, click_result):
            return {"success": True, "action": "startchat_redirected"}
        return {
            "success": False,
            "error": "startchat_redirect_unavailable",
            "history_detail": "首次沟通弹窗缺少可验证的聊天地址，已停止发送且未切换前台",
            "skip_backoff": True,
        }
    return {"success": True, "action": "no_popup"}


def _chat_target_matches_job(target_id: str, job: dict) -> bool:
    job_id = json.dumps(str(job.get("id") or ""), ensure_ascii=False)
    company = json.dumps(str(job.get("company") or ""), ensure_ascii=False)
    title = json.dumps(str(job.get("title") or ""), ensure_ascii=False)
    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, '').toLowerCase();
        const expectedId = normalize({job_id});
        const expectedCompany = normalize({company});
        const expectedTitle = normalize({title});
        const activeRoots = Array.from(new Set([
            document.querySelector('.chat-conversation'),
            document.querySelector('.friend-content.selected')
        ].filter(Boolean)));
        const activeText = normalize(
            activeRoots.map((element) => element.innerText || element.textContent || '').join(' ')
        );
        const activeHtml = activeRoots.map((element) => element.outerHTML || '').join(' ');
        const idMatch = !!expectedId && (
            location.href.includes(expectedId) ||
            activeHtml.includes(expectedId)
        );
        const identityMatch = !!expectedCompany && activeText.includes(expectedCompany) &&
            (!expectedTitle || activeText.includes(expectedTitle));
        return JSON.stringify({{success: true, matches: idMatch || identityMatch}});
    }})()
    """))
    return bool(result.get("success") and result.get("matches"))


def _wait_for_chat_page(
    target_id: str,
    stop_event,
    attempts: int = 20,
    job: dict | None = None,
    excluded_target_ids: set[str] | None = None,
    log=None,
) -> dict:
    """等聊天页打开；每次浏览器调用后都查停下，免得一次等几十分钟。

    浏览器慢的时候，20 次尝试里每次的 evaluate / 标签页扫描都可能
    耗时几十秒，之前只有每轮开头查停下、中间不记日志——面板上看
    就是卡在“等待发送”不动。现在每步都查，5 轮记一条进度。
    """
    for attempt in range(attempts):
        if _sleep_or_stop(0.5, stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        url_now = evaluate(target_id, "location.pathname")
        if _stop_requested(stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        if url_now and "/web/geek/chat" in url_now:
            if job is None or _chat_target_matches_job(target_id, job):
                return {"success": True, "target_id": target_id}
            if _stop_requested(stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        if job is not None:
            for candidate in get_page_targets():
                if _stop_requested(stop_event):
                    close_tab(target_id)
                    return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
                candidate_id = str(candidate.get("targetId") or "")
                candidate_url = str(candidate.get("url") or "")
                if (
                    candidate_id
                    and candidate_id != target_id
                    and "/web/geek/chat" in candidate_url
                    and _chat_target_matches_job(candidate_id, job)
                ):
                    return {"success": True, "target_id": candidate_id, "opened_new_tab": True}
                if _stop_requested(stop_event):
                    close_tab(target_id)
                    return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        if callable(log) and (attempt + 1) % 5 == 0:
            log(f"还在等聊天页打开，第 {attempt + 1} 次…")
    return {"success": False, "error": "chat_navigation_timeout"}


def _click_chat_button(target_id: str, stop_event, attempts: int = 30) -> dict:
    click_chat_js = CHAT_BUTTON_SCRIPT_FOR_TESTS

    last_result: dict = {"success": False, "error": "no_chat_button"}
    for attempt in range(max(1, attempts)):
        if _stop_requested(stop_event):
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
        last_result = _parse_js_result(evaluate(target_id, click_chat_js))
        if last_result.get("success"):
            return last_result
        if attempt < attempts - 1 and _sleep_or_stop(1, stop_event):
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}

    return last_result


def _adopt_chat_target(current_target_id: str, chat_ready: dict) -> str:
    """Switch to a matching chat tab and close the superseded job tab."""
    next_target_id = str(chat_ready.get("target_id") or current_target_id)
    if next_target_id != current_target_id:
        close_tab(current_target_id)
    return next_target_id


def _message_delivery_state(target_id: str, greeting: str | None) -> str:
    """Return 'delivered' | 'pending' | 'failed' | 'missing' for one chat.

    greeting 为 None 时不认具体文案，只认“会话里有没有我发出去的消息”——
    预设招呼语弹窗点确定后，BOSS 已经替我们发了它自己的预设文案，那条
    就是本次投递的消息，文案不是我们的，按文案认不到。
    """
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '')
            .replace(/[\\u200b-\\u200f\\ufeff]/g, '')
            .replace(/\\s+/g, ' ')
            .trim();
        const removeDeliveryLabels = (value) => normalize(value)
            .replace(/(发送中|已读|未读|送达|发送成功|重试|重新发送)$/g, '')
            .trim();
        const textMatchesExpected = (value, expectedText) => {{
            if (expectedText === null) return true;
            const text = removeDeliveryLabels(value);
            return text === expectedText || text.includes(expectedText);
        }};
        const messageText = (node) => {{
            const contentNode = node.querySelector(
                '.message-content, .text, .content, .message-text, '
                + '[class*="message-content"], [class*="msg-content"], [class*="text"]'
            );
            return normalize(contentNode ? contentNode.innerText || contentNode.textContent : node.innerText || node.textContent);
        }};
        const expected = {greeting_escaped} === null ? null : normalize({greeting_escaped});
        const ownMessages = Array.from(document.querySelectorAll(
            '.chat-record .message-item.item-myself, .chat-record .item-myself, '
            + '.chat-record .message-item.item-self, .chat-record [class*="item-my"]'
        ));
        const matching = ownMessages.filter((node) => textMatchesExpected(messageText(node), expected));
        const messageList = document.querySelector('.chat-record');
        const vue = messageList && messageList.__vue__;
        const records = vue && Array.isArray(vue.list$) ? vue.list$ : [];
        const matchingRecords = records.filter((message) => {{
            if (!message || !message.isSelf) return false;
            const text = message.text || message.lastText || message.content || message.message || message.body || '';
            return textMatchesExpected(text, expected);
        }});
        if (!matching.length && !matchingRecords.length) {{
            return JSON.stringify({{success: true, state: 'missing'}});
        }}
        const domStates = matching.map((node) => {{
            const statusNode = node.querySelector('.message-status');
            const statusClass = statusNode ? String(statusNode.className || '') : '';
            if (statusClass.includes('status-error')) return 'failed';
            if (statusClass.includes('status-loading')) return 'pending';
            return 'delivered';
        }});
        const recordStates = matchingRecords.map((record) => {{
            const status = Number(record.status);
            if (status === 4) return 'failed';
            if (status === 0) return 'pending';
            return 'delivered';
        }});
        const states = [...domStates, ...recordStates];
        const state = states.includes('delivered') ? 'delivered'
            : states.includes('pending') ? 'pending' : 'failed';
        return JSON.stringify({{success: true, state}});
    }})()
    """))
    if not result.get("success"):
        return "missing"
    return str(result.get("state") or "missing")


def _verify_greeting_in_chat_list(
    job: dict,
    greeting: str,
    stop_event,
    attempts: int = 6,
) -> bool:
    """Confirm an ambiguous send from the matching BOSS chat-list row.

    A cleared input only proves that the page handled the submit action. Require
    the target company and complete greeting in the same row before recording a
    success, so this fallback cannot cause an automatic duplicate or false sent
    state.
    """
    company = " ".join(str(job.get("company") or "").split())
    expected = " ".join(str(greeting or "").split())
    if not company or not expected:
        return False

    target_id = new_tab("https://www.zhipin.com/web/geek/chat", background=True)
    if not target_id:
        return False

    try:
        wait_for_load(target_id, timeout=10, stop_event=stop_event)
        # 收到停止立即中断：不进聊天列表核验，直接返回“未核验”（调用方不记成功）。
        if _stop_requested(stop_event):
            return False
        company_json = json.dumps(company, ensure_ascii=False)
        expected_json = json.dumps(expected, ensure_ascii=False)
        expression = f"""
        (() => {{
			const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
            const expectedCompany = normalize({company_json});
            const expectedGreeting = normalize({expected_json});
            const rows = Array.from(document.querySelectorAll('li[role=listitem]'));
            const matched = rows.some((row) => {{
                const nameBox = row.querySelector('.name-box');
                const spans = nameBox ? Array.from(nameBox.querySelectorAll('span')) : [];
                const actualCompany = normalize(spans.length >= 2 ? spans[1].textContent : '');
                const lastMessage = row.querySelector('.last-msg-text, .last-msg, .message-text');
                const actualMessage = normalize(lastMessage ? lastMessage.textContent : row.innerText);
                const companyMatches = actualCompany && (
                    actualCompany === expectedCompany
                    || actualCompany.includes(expectedCompany)
                    || expectedCompany.includes(actualCompany)
                );
                return companyMatches && actualMessage.includes(expectedGreeting);
            }});
            return JSON.stringify({{success: true, matched}});
        }})()
        """
        for attempt in range(max(1, attempts)):
            if _stop_requested(stop_event):
                return False
            result = _parse_js_result(evaluate(target_id, expression, timeout=5))
            if result.get("success") and result.get("matched"):
                return True
            if attempt + 1 < attempts and _sleep_or_stop(1, stop_event):
                return False
        return False
    finally:
        close_tab(target_id)


def _submit_chat_message_background(target_id: str, greeting: str) -> dict:
    """Use BossHunter's original Vue submit path without foregrounding Chrome."""
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({{success: false, error: 'no_chat_input'}});

        let vue = null;
        let element = input;
        for (let index = 0; index < 15 && element; index += 1) {{
            if (element.__vue__) {{
                vue = element.__vue__;
                break;
            }}
            element = element.parentElement;
        }}
        if (!vue || typeof vue.handleSubmit !== 'function') {{
            return JSON.stringify({{success: false, error: 'legacy_submit_unavailable'}});
        }}

        input.innerText = {greeting_escaped};
        input.dispatchEvent(new InputEvent('input', {{
            bubbles: true,
            inputType: 'insertText',
            data: {greeting_escaped}
        }}));
        if (vue._data) vue._data.enableSubmit = true;
        vue.handleSubmit();
        return JSON.stringify({{success: true, action: 'chat_submitted_background'}});
    }})()
    """))
    if result.get("success"):
        return {"success": True, "action": "chat_submitted_background"}
    return result


def _fill_chat_input(target_id: str, greeting: str) -> dict:
    greeting_escaped = json.dumps(greeting, ensure_ascii=False)
    input_state = _parse_js_result(evaluate(target_id, """
    (() => {
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({success: false, error: 'no_chat_input'});
        return JSON.stringify({success: true});
    })()
    """))
    if not input_state.get("success"):
        return input_state
    if not click_at(target_id, "#chat-input"):
        return {"success": False, "error": "chat_input_focus_failed"}
    if not press_key(target_id, "SelectAll") or not press_key(target_id, "Backspace"):
        return {"success": False, "error": "chat_input_clear_failed"}
    if not type_text(target_id, greeting, human=True):
        return {"success": False, "error": "trusted_input_failed"}

    result = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        const input = document.querySelector('#chat-input');
        if (!input) return JSON.stringify({{success: false, error: 'no_chat_input'}});
        const sendButton = document.querySelector('.btn-send');
        const disabled = !sendButton || sendButton.disabled || sendButton.classList.contains('disabled');
        const matches = normalize(input.innerText || input.textContent) === normalize({greeting_escaped});
        return JSON.stringify({{
            success: matches,
            error: matches ? null : 'input_not_filled',
            send_button: !!sendButton,
            disabled
        }});
    }})()
    """))
    if result.get("success") and result.get("disabled"):
        if type_text(target_id, " ") and press_key(target_id, "Backspace"):
            result = _parse_js_result(evaluate(target_id, f"""
            (() => {{
                const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const input = document.querySelector('#chat-input');
                const sendButton = document.querySelector('.btn-send');
                const disabled = !sendButton || sendButton.disabled || sendButton.classList.contains('disabled');
                const matches = !!input && normalize(input.innerText || input.textContent) === normalize({greeting_escaped});
                return JSON.stringify({{success: matches, error: matches ? null : 'input_not_filled', disabled}});
            }})()
            """))
    return result


# JS: 在岗位详情页点击"立即沟通"并发送招呼语
JS_SEND_GREETING = """
(async (greeting) => {
    // 找到"立即沟通"按钮
    const btn = document.querySelector('.btn-startchat, .op-btn-chat, [ka="job_detail_chat"]');
    if (!btn) return JSON.stringify({success: false, error: 'no_chat_button'});

    btn.click();
    await new Promise(r => setTimeout(r, 2000));

    // 等待聊天输入框出现
    const input = document.querySelector('.chat-input textarea, .chat-input [contenteditable], .input-area textarea');
    if (!input) return JSON.stringify({success: false, error: 'no_input_box'});

    // 输入招呼语
    if (input.tagName === 'TEXTAREA') {
        input.value = greeting;
        input.dispatchEvent(new Event('input', {bubbles: true}));
    } else {
        input.innerHTML = greeting;
        input.dispatchEvent(new Event('input', {bubbles: true}));
    }

    await new Promise(r => setTimeout(r, 500));

    // 点击发送
    const sendBtn = document.querySelector('.btn-send, .send-btn, [class*="send"]');
    if (sendBtn) {
        sendBtn.click();
        await new Promise(r => setTimeout(r, 1000));
        return JSON.stringify({success: true});
    }

    return JSON.stringify({success: false, error: 'no_send_button'});
})(arguments[0])
"""


def _micro_delay_range(throttle_config: dict) -> tuple[float, float]:
    """Human-like pause between two clicks, from config (default 3-8s)."""
    try:
        low = float(throttle_config.get("micro_delay_min", 3))
        high = float(throttle_config.get("micro_delay_max", 8))
    except (TypeError, ValueError):
        low, high = 3.0, 8.0
    low = max(0.0, low)
    return low, max(low, high)


def _stagger_range(throttle_config: dict) -> tuple[float, float]:
    """Pause between platform groups, from config (default 30-120s)."""
    try:
        low = float(throttle_config.get("platform_stagger_min", 30))
        high = float(throttle_config.get("platform_stagger_max", 120))
    except (TypeError, ValueError):
        low, high = 30.0, 120.0
    low = max(0.0, low)
    return low, max(low, high)


def _record_logged_out(throttle_config: dict, platform: str, detail: str) -> None:
    """投递中发现掉线时记一笔持久登录态（带熔断联动）。

    只写库、不拦投递流程：记库失败（例如测试里没带 _db）时静默跳过，
    绝不因为一笔记账动作把一次投递搞崩。检测本身仍走 antibot 的
    handle_page_risk（内含 detect_page_risk），这里只补记账。
    """
    db = throttle_config.get("_db") if isinstance(throttle_config, dict) else None
    if db is None:
        return
    try:
        record_platform_login_state(db, platform, "logged_out", detail)
    except Exception:
        pass


def _page_risk_result(
    target_id: str, platform: str, throttle_config: dict
) -> dict | None:
    """Run the captcha/risk check on the open page.

    Returns a send-result dict when the page is blocked, else None when
    the page is clear (the caller continues the job).
    """
    risk = handle_page_risk(target_id, platform, throttle_config)
    if not risk:
        return None
    if risk == "stopped":
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}
    if risk == "login_required":
        _record_logged_out(throttle_config, platform, "投递时页面提示需要先登录")
        return {"success": False, "error": "needs_login", "history_detail": "平台要求先登录，请在浏览器里登录该平台后重试", "skip_backoff": True}
    if risk == "verification_timeout":
        return {"success": False, "error": "verification_timeout", "history_detail": "等待手动验证超时，任务已停止"}
    return {"success": False, "error": risk, "history_detail": "页面触发平台风控，请稍后重试"}


def _detect_job_closed_on_page(target_id: str) -> dict | None:
    """Attempt to confirm whether the opened job page reports the job as closed/down.

    Used as a fallback when a step on the job-detail page fails (e.g. no chat button),
    so we can surface the real business conclusion ("job is closed") instead of only the
    technical step that failed. Returns a send-result dict when a closed marker is found.
    """
    markers = list(JOB_CLOSED_MARKERS)
    probe = _parse_js_result(evaluate(target_id, f"""
    (() => {{
        const text = document.body ? document.body.innerText : '';
        const title = document.title || '';
        const markers = {json.dumps(markers, ensure_ascii=False)};
        const hit = title.includes('访问的页面不存在') || markers.some((m) => text.includes(m));
        if (!hit) return JSON.stringify({{closed: false}});
        const snippet = markers.find((m) => text.includes(m)) || '职位已关闭';
        return JSON.stringify({{closed: true, snippet}});
    }})()
    """))
    if probe.get("closed"):
        return {
            "success": False,
            "error": "job_page_unavailable",
            "history_detail": "岗位已关闭或下架",
            "skip_backoff": True,
        }
    return None


def _send_greeting_once(job: dict, greeting: str, throttle_config: dict) -> tuple[dict, str | None]:
    stop_event = throttle_config.get("_workbench_stop_event")
    existing_target_ids = {
        str(target.get("targetId") or "")
        for target in get_page_targets()
        if target.get("targetId")
    }
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
    target_id = new_tab(job["url"], background=True)
    if not target_id:
        return {"success": False, "error": "open_page_failed", "history_detail": "无法打开页面", "skip_backoff": True}, None

    if _stop_requested(stop_event):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    browse_min = throttle_config.get("browse_duration_min", 15)
    browse_max = throttle_config.get("browse_duration_max", 30)
    if throttle_config.get("browse_before_greet", True):
        try:
            browse_min = float(browse_min)
            browse_max = float(browse_max)
        except (TypeError, ValueError):
            browse_min, browse_max = 15.0, 30.0
        browse_max = max(browse_min, browse_max)
        # 像真人一样滚动浏览详情页，而不是打开就干等。
        if human_browse(target_id, stop_event, min_s=browse_min, max_s=browse_max):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    page_check_js = f"""
    (() => {{
        const text = document.body ? document.body.innerText : '';
        const title = document.title || '';
        const closedMarkers = {json.dumps(list(JOB_CLOSED_MARKERS), ensure_ascii=False)};
        if (
            title.includes('访问的页面不存在') ||
            closedMarkers.some((marker) => text.includes(marker))
        ) {{
            return JSON.stringify({{
                success: false,
                error: 'job_page_unavailable',
                history_detail: '岗位已关闭或下架',
                skip_backoff: true
            }});
        }}
        return JSON.stringify({{success: true}});
    }})()
    """
    page_check = _parse_js_result(evaluate(target_id, page_check_js))
    if not page_check.get("success"):
        close_tab(target_id)
        return page_check, None

    # 风控检查：滑块/安全验证页出现时暂停，等用户手动验证后继续。
    risk_result = _page_risk_result(target_id, "boss", throttle_config)
    if risk_result is not None:
        close_tab(target_id)
        return risk_result, None

    micro_low, micro_high = _micro_delay_range(throttle_config)
    if micro_pause(stop_event, micro_low, micro_high):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    chat_button_attempts = int(throttle_config.get("_chat_button_attempts", 30))
    result1a = _click_chat_button(target_id, stop_event, chat_button_attempts)
    if not result1a.get("success"):
        closed_result = _detect_job_closed_on_page(target_id)
        close_tab(target_id)
        if closed_result:
            return closed_result, None
        return {"success": False, "error": "no_chat_button", "history_detail": "无法找到沟通按钮", "skip_backoff": True}, None

    if micro_pause(stop_event, micro_low, micro_high):
        close_tab(target_id)
        return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None

    popup_result = _handle_greet_popup(target_id, greeting, result1a)
    if not popup_result.get("success"):
        return popup_result, target_id
    contact_action = str(popup_result.get("action") or "no_popup")

    navigation_attempts = int(throttle_config.get("_chat_navigation_attempts", 20))
    chat_ready = _wait_for_chat_page(
        target_id,
        stop_event,
        navigation_attempts,
        job,
        excluded_target_ids=existing_target_ids,
        log=throttle_config.get("_workbench_log"),
    )
    if chat_ready.get("success"):
        target_id = _adopt_chat_target(target_id, chat_ready)
    if chat_ready.get("error") == "stopped":
        return chat_ready, None
    if not chat_ready.get("success") and contact_action not in {
        "first_contact_submitted",
        "preset_confirmed",
        "startchat_redirected",
    }:
        console.print("[yellow]    ! 沟通按钮未跳转聊天页，尝试真实点击兜底[/yellow]")
        if click_at(target_id, CHAT_BUTTON_SELECTOR):
            if _sleep_or_stop(1, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            popup_result = _handle_greet_popup(target_id, greeting, result1a)
            if not popup_result.get("success"):
                return popup_result, target_id
            contact_action = str(popup_result.get("action") or "no_popup")
            chat_ready = _wait_for_chat_page(
                target_id,
                stop_event,
                navigation_attempts,
                job,
                excluded_target_ids=existing_target_ids,
                log=throttle_config.get("_workbench_log"),
            )
            if chat_ready.get("success"):
                target_id = _adopt_chat_target(target_id, chat_ready)
            if chat_ready.get("error") == "stopped":
                return chat_ready, None

    if not greeting and throttle_config.get("silent_mode") and chat_ready.get("success"):
        # 静默模式：不发自定义招呼语，仅打开沟通会话建立联系。
        close_tab(target_id)
        return {
            "success": True,
            "silent_contact": True,
            "history_detail": "静默模式：已打开沟通会话，未发送自定义招呼语",
        }, None

    if not chat_ready.get("success"):
        if contact_action == "first_contact_submitted":
            if _verify_greeting_in_chat_list(job, greeting, stop_event):
                close_tab(target_id)
                return {
                    "success": True,
                    "verified": True,
                    "first_contact": True,
                    "verified_from_chat_list": True,
                }, None
            return {
                "success": False,
                "error": "first_contact_navigation_unverified",
                "history_detail": "首次招呼语已经提交，但未能进入对应会话验证结果；为避免重复发送，请人工检查",
                "skip_backoff": True,
            }, target_id
        if contact_action == "preset_confirmed":
            # 预设文案已经发出去了，但没进聊天页就没法核验是哪一条——
            # 不认成功、也不重发（重发就是第二条），只报“请人工检查”。
            return {
                "success": False,
                "error": "preset_contact_navigation_unverified",
                "history_detail": "预设招呼语已经提交，但未能进入对应会话验证结果；为避免重复发送，请人工检查",
                "skip_backoff": True,
            }, target_id
        return {
            "success": False,
            "error": "no_chat_input",
            "history_detail": "发送失败: 未进入具体聊天会话，可能是BOSS继续沟通跳转失败",
            "skip_backoff": True,
        }, target_id

    verification_attempts = int(throttle_config.get("_send_verification_attempts", 20))
    if contact_action in {"first_contact_submitted", "preset_confirmed"}:
        # 预设弹窗点确定后，BOSS 已经发了它自己的预设文案——这条就是本次
        # 投递的那一条消息，绝不能再发我们的打招呼。之前这里只认
        # first_contact_submitted，preset_confirmed 会一路走到聊天页再发
        # 一条，同一 HR 一次收到两条（截图里第一条是 BOSS 预设文案，第二
        # 条才是我们的固定打招呼）。只核验会话里有没有我方已送达的消息：
        # 预设文案不是我们的文案，按文案认不到，用 None 认任意一条；聊天
        # 列表核验也认我们的文案，预设走不通，直接跳过。
        preset = contact_action == "preset_confirmed"
        verify_text = None if preset else greeting
        rejected_detail = (
            "预设招呼语被标记为发送失败" if preset else "首次沟通招呼语被标记为发送失败"
        )
        label = "预设招呼语" if preset else "首次招呼语"
        for _ in range(max(1, verification_attempts)):
            if _sleep_or_stop(0.5, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            delivery_state = _message_delivery_state(target_id, verify_text)
            if _stop_requested(stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            if delivery_state == "failed":
                return {
                    "success": False,
                    "error": "first_contact_send_rejected",
                    "history_detail": rejected_detail,
                    "skip_backoff": True,
                }, target_id
            if delivery_state == "delivered":
                if _sleep_or_stop(2, stop_event):
                    close_tab(target_id)
                    return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
                if _message_delivery_state(target_id, verify_text) == "delivered":
                    close_tab(target_id)
                    return {
                        "success": True,
                        "verified": True,
                        "first_contact": True,
                    }, None
                if not preset and _verify_greeting_in_chat_list(job, greeting, stop_event):
                    close_tab(target_id)
                    return {
                        "success": True,
                        "verified": True,
                        "first_contact": True,
                        "verified_from_chat_list": True,
                    }, None
                return {
                    "success": False,
                    "error": "first_contact_send_not_stable",
                    "history_detail": f"{label}曾出现但未稳定保留在会话中",
                    "skip_backoff": True,
                }, target_id
        if not preset and _verify_greeting_in_chat_list(job, greeting, stop_event):
            close_tab(target_id)
            return {
                "success": True,
                "verified": True,
                "first_contact": True,
                "verified_from_chat_list": True,
            }, None
        return {
            "success": False,
            "error": "first_contact_delivery_unverified",
            "history_detail": f"{label}已提交，但会话中未确认对应消息；为避免重复发送，请人工检查",
            "skip_backoff": True,
        }, target_id

    # 聊天页也可能弹出安全验证：先检查，等用户处理完再发。
    risk_result = _page_risk_result(target_id, "boss", throttle_config)
    if risk_result is not None:
        close_tab(target_id)
        return risk_result, None

    existing_state = _message_delivery_state(target_id, greeting)
    if existing_state == "delivered":
        close_tab(target_id)
        return {"success": True, "already_present": True, "verified": True}, None
    if existing_state in {"pending", "failed"}:
        return {
            "success": False,
            "error": f"existing_message_{existing_state}",
            "history_detail": f"会话中已有状态为 {existing_state} 的相同消息，请人工检查",
            "skip_backoff": True,
        }, target_id

    submit_result = _submit_chat_message_background(target_id, greeting)
    if not submit_result.get("success"):
        # 后台提交报失败不等于没发出去：evaluate 本身超时/解析失败时，
        # vue.handleSubmit() 可能已经把消息发出去了。先认一下会话里有
        # 没有这条消息——已经在（发送中也算）就直接走核验，别再手填一遍，
        # 否则同一 HR 一次收到两条一模一样的招呼（v45 复现）。
        # 入口处已经确认过会话里没有这条消息，所以这里看到的只能是本次
        # 后台提交发出去的；真没有（missing）才走手填兜底。
        recheck_state = _message_delivery_state(target_id, greeting)
        if recheck_state in {"delivered", "pending"}:
            submit_result = {"success": True, "action": "chat_submitted_background"}
        elif recheck_state == "failed":
            return {
                "success": False,
                "error": "background_send_rejected",
                "history_detail": "后台提交的招呼语被标记为发送失败，未再重发，请人工检查",
                "skip_backoff": True,
            }, target_id
    if not submit_result.get("success"):
        input_result = _fill_chat_input(target_id, greeting)
        if not input_result.get("success"):
            return input_result, target_id
        if input_result.get("disabled"):
            return {
                "success": False,
                "error": "send_button_unavailable",
                "history_detail": "招呼语已填入，但发送按钮不可用，未标记成功",
                "skip_backoff": True,
            }, target_id
        if micro_pause(stop_event, micro_low, micro_high):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
        if not click_at(target_id, ".btn-send:not(.disabled)"):
            return {
                "success": False,
                "error": "send_button_click_failed",
                "history_detail": "招呼语已填入，但发送按钮点击失败，未标记成功",
                "skip_backoff": True,
            }, target_id

    for _ in range(max(1, verification_attempts)):
        if _sleep_or_stop(0.5, stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
        delivery_state = _message_delivery_state(target_id, greeting)
        if _stop_requested(stop_event):
            close_tab(target_id)
            return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
        if delivery_state == "failed":
            return {
                "success": False,
                "error": "send_rejected_after_click",
                "history_detail": "BOSS 将消息标记为发送失败，未记录为已发送",
                "skip_backoff": True,
            }, target_id
        if delivery_state == "delivered":
            if _sleep_or_stop(2, stop_event):
                close_tab(target_id)
                return {"success": False, "error": "stopped", "history_detail": "用户已请求停止", "skip_backoff": True}, None
            if _message_delivery_state(target_id, greeting) == "delivered":
                close_tab(target_id)
                return {"success": True, "verified": True}, None
            if _verify_greeting_in_chat_list(job, greeting, stop_event):
                close_tab(target_id)
                return {
                    "success": True,
                    "verified": True,
                    "verified_from_chat_list": True,
                }, None
            return {
                "success": False,
                "error": "send_not_stable",
                "history_detail": "消息曾出现但未稳定保留在会话中，未记录为已发送",
                "skip_backoff": True,
            }, target_id

    if _verify_greeting_in_chat_list(job, greeting, stop_event):
        close_tab(target_id)
        return {
            "success": True,
            "verified": True,
            "verified_from_chat_list": True,
        }, None

    return {
        "success": False,
        "error": "send_not_confirmed",
        "history_detail": "已点击发送，但会话中未确认对应招呼语，未记录为已发送",
        "skip_backoff": True,
    }, target_id


def _dispatch_send(job: dict, greeting: str, throttle_config: dict) -> tuple[dict, str | None]:
    """按平台分发投递。

    BOSS 直聘走聊天招呼语流（静默模式时不发自定义招呼语，仅打开沟通会话）；
    智联 / 前程无忧 / 猎聘走详情页投递（不需要招呼语）。
    """
    platform = str(job.get("source_platform") or "boss").strip() or "boss"
    if platform == "boss":
        if throttle_config.get("silent_mode"):
            greeting = ""
        return _send_greeting_once(job, greeting, throttle_config)
    return deliver_job(job, throttle_config)


def send_greetings(config: dict, force: bool = False, db_path=None) -> int:
    """Send generated greetings. Returns count of successfully sent.

    ``db_path`` lets web callers pin the runtime database; without it the
    module-level default (CWD-relative) is used for CLI compatibility.
    """
    throttle_config = dict(config.get("throttle", {}))
    # 风控保护（v22 可视化）：档位 + 开关解析成实际用的节奏参数，直接
    # 覆盖 throttle_config 里的同名键——微停顿、浏览时长、详情浏览、
    # 错峰、投递间隔都按档位表走；开关关了对应保护就不生效。
    risk_profile = resolve_risk_profile(throttle_config)
    throttle_config.update(risk_profile)
    db = get_db(db_path)
    stop_event = config.get("_workbench_stop_event")
    workbench_job_ids = {str(job_id) for job_id in config.get("_workbench_job_ids", [])}
    send_report = {
        "requested_count": len(workbench_job_ids),
        "eligible_count": 0,
        "scheduled_count": 0,
        "attempted_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "deferred_count": len(workbench_job_ids),
        "quota_deferred_count": 0,
        "already_sent": 0,
        "daily_limit": 0,
        "remaining_quota": 0,
        "per_platform_limits": {},
        "platform_suspended": [],
        "stop_reason": None,
    }
    # Keep the integer return value for CLI/backward compatibility while giving
    # the web workflow enough detail to distinguish failures from quota deferrals.
    config["_workbench_send_report"] = send_report
    if isinstance(stop_event, Event):
        throttle_config["_workbench_stop_event"] = stop_event
    access_guard = PlatformAccessGuard(db, config, "send")
    throttle_config["_platform_access_guard"] = access_guard
    # 防检测层要用的上下文：数据库连接（验证码 hold / 熔断）与面板日志。
    throttle_config["_db"] = db
    throttle_config["_workbench_log"] = config.get("_workbench_log")
    try:
        access_guard.ensure_unlocked()
    except PlatformSafetyStop as exc:
        send_report["stop_reason"] = exc.reason
        console.print("[yellow]为了账户安全，平台风险冷却尚未结束，已停止投递[/yellow]")
        db.close()
        return 0

    jobs = get_jobs_ready_to_send(db)
    type_blocked = [job for job in jobs if internship_rejection(job, config)]
    send_report["employment_blocked_ids"] = [str(job["id"]) for job in type_blocked]
    for job in type_blocked:
        console.print(f"[yellow]跳过岗位：{job.get('title', '')}（{internship_rejection(job, config)}）[/yellow]")
    jobs = [job for job in jobs if not internship_rejection(job, config)]
    # Review choices made during generation require a separate send confirmation.
    pending_review_ids = config.get("_workbench_pending_review_ids", set())
    jobs = [job for job in jobs if str(job["id"]) not in pending_review_ids]
    if workbench_job_ids:
        jobs = [job for job in jobs if str(job["id"]) in workbench_job_ids]
    unsupported_jobs = [
        job for job in jobs
        if not platform_supports(str(job.get("source_platform") or "boss"), "deliver")
    ]
    if unsupported_jobs:
        send_report["unsupported_platform_ids"] = [str(job["id"]) for job in unsupported_jobs]
        for job in unsupported_jobs:
            console.print(f"[yellow]跳过岗位：{job.get('company', '')}｜{job.get('title', '')}（来源平台未提供发送适配器）[/yellow]")
        jobs = [job for job in jobs if job not in unsupported_jobs]
    send_report["eligible_count"] = len(jobs)
    send_report["deferred_count"] = len(workbench_job_ids) if workbench_job_ids else len(jobs)

    if not jobs:
        console.print("[yellow]没有已生成招呼语的待发送岗位，请先运行 bosshunter greet[/yellow]")
        send_report["stop_reason"] = "no_ready_jobs"
        db.close()
        return 0

    # Check daily limit: global cap + per-platform caps (local calendar day).
    # 上限开关关了就是不限（remaining_quota 为 None），开着才按 daily_cap 走。
    daily_cap_enabled = bool(risk_profile["daily_cap_enabled"])
    daily_limit = int(risk_profile["daily_cap"])
    interval_min = throttle_config.get("interval_min", 60)
    interval_max = throttle_config.get("interval_max", 180)

    # Count today's sent (local day, not UTC).
    already_sent = count_sent_today(db)

    remaining_quota = daily_limit - already_sent if daily_cap_enabled else None
    send_report["already_sent"] = already_sent
    send_report["daily_limit"] = daily_limit if daily_cap_enabled else 0
    send_report["remaining_quota"] = max(remaining_quota, 0) if remaining_quota is not None else 0
    if remaining_quota is not None and remaining_quota <= 0:
        console.print(f"[yellow]今日已达发送上限 ({daily_limit})[/yellow]")
        send_report["quota_deferred_count"] = len(jobs)
        send_report["stop_reason"] = "daily_limit"
        db.close()
        return 0

    per_platform_limits = platform_daily_limits(throttle_config)
    send_report["per_platform_limits"] = per_platform_limits
    sent_by_platform: dict[str, int] = {}
    for job in jobs:
        platform = str(job.get("source_platform") or "boss").strip() or "boss"
        if platform not in sent_by_platform:
            sent_by_platform[platform] = count_sent_today_by_platform(db, platform)
    jobs_to_send: list[dict] = []
    quota_deferred = 0
    for job in jobs:
        if remaining_quota is not None and len(jobs_to_send) >= remaining_quota:
            quota_deferred += 1
            continue
        platform = str(job.get("source_platform") or "boss").strip() or "boss"
        if sent_by_platform.get(platform, 0) >= per_platform_limits.get(platform, 80):
            console.print(
                f"[yellow]今日 {platform_display_name(platform)} 已达单平台上限 "
                f"({per_platform_limits.get(platform, 80)})，其余岗位留到明天[/yellow]"
            )
            quota_deferred += 1
            continue
        sent_by_platform[platform] = sent_by_platform.get(platform, 0) + 1
        jobs_to_send.append(job)
    send_report["scheduled_count"] = len(jobs_to_send)
    send_report["quota_deferred_count"] = quota_deferred
    if send_report["quota_deferred_count"]:
        send_report["stop_reason"] = "daily_limit"
    throttle = RequestThrottle(delay_min=interval_min, delay_max=interval_max)
    backoff = ProgressiveBackoff()
    breaker = CircuitBreaker(db, throttle_config.get("breaker_max_consecutive_failures", 3))
    sent_count = 0
    workbench_log = config.get("_workbench_log")

    # 错峰：投递队列按平台分组（采集顺序），组与组之间插一段随机停顿，
    # 避免同一瞬间多平台高频请求；组内顺序不变。
    platform_order = config.get("collection", {}).get("default_order") or list(DEFAULT_PLATFORM_ORDER)
    grouped_jobs = group_jobs_by_platform(
        jobs_to_send, tuple(str(p) for p in platform_order)
    )
    ordered_jobs = [job for _, group in grouped_jobs for job in group]
    group_starts: dict[int, str] = {}
    offset = 0
    for group_platform, group in grouped_jobs:
        group_starts[offset] = group_platform
        offset += len(group)
    stagger_low, stagger_high = _stagger_range(throttle_config)

    quota_label = f"{already_sent}/{daily_limit}" if daily_cap_enabled else f"{already_sent}（今日上限已关）"
    console.print(f"[bold]准备发送 {len(ordered_jobs)} 条招呼语[/bold] (今日已发 {quota_label})")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console
    ) as progress:
        task = progress.add_task("发送中", total=len(ordered_jobs))

        for index, job in enumerate(ordered_jobs):
            if _stop_requested(stop_event):
                console.print("[yellow]已请求停止，结束发送[/yellow]")
                send_report["stop_reason"] = "stopped"
                break

            # 暂停关卡：每发一条之前都等一下；暂停时不发下一条，停下随时能停。
            if wait_while_paused(get_pause_event(config), stop_event, workbench_log):
                console.print("[yellow]已请求停止，结束发送[/yellow]")
                send_report["stop_reason"] = "stopped"
                break

            if index in group_starts and index > 0:
                stagger_s = sample_stagger_delay(stagger_low, stagger_high)
                if callable(workbench_log):
                    workbench_log(
                        f"切换到{platform_display_name(group_starts[index])}，"
                        f"先歇 {int(stagger_s)} 秒再继续"
                    )
                progress.update(task, description="换平台，歇一会...")
                if _sleep_or_stop(stagger_s, stop_event):
                    console.print("[yellow]已请求停止，结束发送[/yellow]")
                    send_report["stop_reason"] = "stopped"
                    break

            current_job = f"{job.get('company') or '公司未知'}｜{job.get('title') or '职位未知'}"
            next_job = ordered_jobs[index + 1] if index + 1 < len(ordered_jobs) else None
            next_label = (
                f"下一条：{next_job.get('company') or '公司未知'}｜{next_job.get('title') or '职位未知'}"
                if next_job else "下一条：无"
            )

            # Wait between sends (except first)
            if sent_count > 0:
                if callable(workbench_log):
                    workbench_log(
                        f"招呼语进度 {index + 1}/{len(ordered_jobs)}\n等待发送：{current_job}\n{next_label}"
                    )
                progress.update(task, description="等待间隔...")
                if throttle.wait(stop_event):
                    console.print("[yellow]已请求停止，结束发送[/yellow]")
                    send_report["stop_reason"] = "stopped"
                    break

            activity_guard = config.get("_workbench_greeting_activity")
            with activity_guard(job["id"], "sending") if callable(activity_guard) else nullcontext(True) as acquired:
                if not acquired:
                    progress.update(task, advance=1)
                    continue
                if callable(activity_guard):
                    row = db.execute("SELECT * FROM jobs WHERE id = ? AND deleted_at IS NULL", (job["id"],)).fetchone()
                    if row is None or row["status"] not in {"ready", "approved"} or row["greeting_selection"] == "pending":
                        progress.update(task, advance=1)
                        continue
                    job = dict(row)
                # 发送时取当前生效文案（v38）：不再直接读库里生成时存下的
                # 快照——开关关着、或固定文案改过，都以当前配置为准；开关
                # 开着且有定制文案时用定制文案，没生成则回退固定文案发，
                # 不阻塞队列。判定与岗位列表展示共用 effective_greeting。
                greeting = effective_greeting(config, job)
                platform = str(job.get("source_platform") or "boss").strip() or "boss"
                if breaker.is_suspended(platform):
                    console.print(f"[yellow]    ! {platform_display_name(platform)}今日已挂起（连续失败触发熔断），跳过[/yellow]")
                    progress.update(task, advance=1)
                    continue
                if not greeting and platform == "boss" and not throttle_config.get("silent_mode"):
                    update_job_status(db, job["id"], "error")
                    update_job_last_error(db, job["id"], "该岗位没有已生成的招呼语，无法发送", "no_greeting")
                    send_report["attempted_count"] += 1
                    send_report["failed_count"] += 1
                    progress.update(task, advance=1)
                    continue

                if callable(workbench_log):
                    workbench_log(
                        f"招呼语进度 {index + 1}/{len(ordered_jobs)}\n正在发送：{current_job}\n{next_label}"
                    )
                progress.update(task, description=f"发送: {job['company'][:10]} - {job['title'][:15]}")

                result_data, failed_target_id = _dispatch_send(job, greeting, throttle_config)
                if result_data.get("error") == "stopped":
                    send_report["stop_reason"] = "stopped"
                    break
                if result_data.get("error") == "no_chat_input" and failed_target_id:
                    console.print("[yellow]    ! 未进入具体聊天会话，重新打开岗位页再试一次[/yellow]")
                    close_tab(failed_target_id)
                    result_data, failed_target_id = _dispatch_send(job, greeting, throttle_config)
                    if result_data.get("error") == "stopped":
                        send_report["stop_reason"] = "stopped"
                        break

                send_report["attempted_count"] += 1

                if not result_data.get("success"):
                    console.print(f"[yellow]    ! 发送失败，已记录并关闭任务页面: {result_data.get('error', 'unknown')}[/yellow]")
                    if failed_target_id:
                        close_tab(failed_target_id)
                        failed_target_id = None

                if result_data.get("success"):
                    throttle.mark()
                    update_job_status(db, job["id"], "sent")
                    update_job_last_error(db, job["id"], "")
                    add_history(db, job["id"], "sent", greeting[:50])
                    sent_count += 1
                    send_report["sent_count"] = sent_count
                    backoff.record_success()
                    breaker.record_success(platform)
                else:
                    error = result_data.get("error", "unknown")
                    if error in {"daily_platform_page_limit", "persistent_risk_lock"}:
                        send_report["stop_reason"] = error
                        console.print("[yellow]为了账户安全，已达到平台页面访问上限或仍处于风险冷却，停止投递[/yellow]")
                        break
                    send_report["failed_count"] += 1
                    history_detail = result_data.get("history_detail", f"发送失败: {error}")
                    update_job_status(db, job["id"], "error")
                    update_job_last_error(db, job["id"], history_detail, error)
                    add_history(db, job["id"], "error", history_detail)
                    if not result_data.get("skip_backoff"):
                        if breaker.record_failure(platform):
                            console.print(f"\n[red]⚠ {platform_display_name(platform)}连续失败已触发熔断，今日不再投这个平台[/red]")
                            add_risk_event(db, "platform_suspended", f"{platform} 连续失败，已挂起当日任务")
                            send_report["platform_suspended"].append(platform)
                    if result_data.get("skip_backoff"):
                        progress.update(task, advance=1)
                        continue

                    throttle.mark()

                    # Progressive backoff on errors
                    pause_duration = backoff.record_error()
                    add_risk_event(db, "send_error", f"{error} (连续{backoff._consecutive_errors}次)")

                    # If we encounter rate limiting, stop immediately.
                    # Captcha no longer stops the whole run: the hold flow
                    # already gave the user a chance, and an unresolved
                    # captcha is counted by the breaker per platform.
                    if error in ["rate_limit", "blocked"]:
                        console.print(f"\n[red]⚠ 检测到风控信号: {error}，安全暂停[/red]")
                        add_risk_event(db, error, f"触发风控: {error}")
                        lock_minutes = config.get("safety", {}).get("risk_lock_minutes", 10)
                        try:
                            lock_minutes = max(int(lock_minutes), 1)
                        except (TypeError, ValueError):
                            lock_minutes = 10
                        set_platform_safety_lock(db, error, minutes=lock_minutes)
                        send_report["stop_reason"] = error
                        break

                    # If too many consecutive errors, pause
                    if backoff.should_pause_long:
                        console.print(f"\n[red]⚠ 连续错误过多，暂停 {int(pause_duration/60)} 分钟[/red]")
                        add_risk_event(db, "backoff_pause", f"暂停{int(pause_duration)}秒")
                        lock_minutes = config.get("safety", {}).get("risk_lock_minutes", 10)
                        try:
                            lock_minutes = max(int(lock_minutes), 1)
                        except (TypeError, ValueError):
                            lock_minutes = 10
                        set_platform_safety_lock(db, "consecutive_errors", minutes=lock_minutes)
                        send_report["stop_reason"] = "consecutive_errors"
                        break
                    elif pause_duration > 0:
                        console.print(f"\n[yellow]  错误退避: 额外等待 {int(pause_duration)}秒[/yellow]")
                        if _sleep_or_stop(pause_duration, stop_event):
                            send_report["stop_reason"] = "stopped"
                            break

                # 阶段性自动休息（v22 风控保护）：连投满一档的数量、后面还有
                # 岗位时歇一次；开关关了就不歇。停下请求优先，随时能停。
                if (
                    result_data.get("success")
                    and risk_profile["stage_rest"]
                    and should_stage_rest(
                        sent_count, risk_profile["rest_every"], index + 1 < len(ordered_jobs)
                    )
                ):
                    rest_s = sample_rest_delay(risk_profile["rest_min"], risk_profile["rest_max"])
                    if callable(workbench_log):
                        workbench_log(f"连投了 {sent_count} 个，先歇 {int(rest_s)} 秒再继续")
                    progress.update(task, description="歇一会再继续...")
                    if _sleep_or_stop(rest_s, stop_event):
                        console.print("[yellow]已请求停止，结束发送[/yellow]")
                        send_report["stop_reason"] = "stopped"
                        break

                progress.update(task, advance=1)

    console.print(f"\n[green]✓ 成功发送 {sent_count} 条[/green]")
    report_total = len(workbench_job_ids) if workbench_job_ids else len(jobs)
    send_report["sent_count"] = sent_count
    send_report["deferred_count"] = max(
        report_total - sent_count - send_report["failed_count"],
        0,
    )
    db.close()
    return sent_count
