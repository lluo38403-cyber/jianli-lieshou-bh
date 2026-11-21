"""OS 级单实例互斥（v37）。

用户原话（2026-11-22 03:30）：“启动 exe 很慢、启动后同时跳出四五个窗口”。

根因核实：代码里没有任何 Mutex——现行防多开只靠 data/.instance.json +
端口探测 + 无窗口复用（v29 那套）。快速双击两次时，第二个进程在第一
个进程还没把服务 / 窗口建好之前就已经开始自己的启动流程：它要么以为
端口没人占、自己再起一份服务，要么走复用分支又开一个窗口——结果就是
两个进程、启动页 + 面板 + 工作浏览器加起来四五个窗口。

修法：程序入口最顶端（launcher.main() 与 bosshunter.main.cli() 的 web
分支，初始化本地服务、加载 UI 之前）先抢一个 Windows Named Mutex：

- Windows：ctypes 调 kernel32 CreateMutexW，名字固定、带 Global\\ 前缀，
  多用户会话下也唯一。第二个进程看到 ERROR_ALREADY_EXISTS（183）就
  立即退出，退出前把已有主窗口前置/激活（FindWindow + ShowWindow /
  SetForegroundWindow；窗口还没建好时等一小会儿再唤醒，不静默失败）。
- 非 Windows：保持现有文件锁行为（.instance.json + 端口探测）不变，
  这里直接放行，不引入新的锁。
- Named Mutex 随进程死亡自动释放：进程被强杀也不会留下锁文件把下次
  启动挡死——这一点由测试钉住（子进程 SIGKILL 后下一个进程仍能抢到锁）。
- 与现有退出清理共存：正常退出时 _install_shutdown_hooks（v33）照常清
  实例文件、停 Chrome；互斥锁只在进程退出时由 OS/atexit 释放，不碰
  .instance.json、不碰任何清理逻辑。同一进程内重复抢锁返回已持有的那
  把（launcher 抢过、cli 的 web 分支再抢不会误判成“已有实例”）。

本模块永不抛异常、永不挡启动：CreateMutex 失败（API 不可用等）时按
“没有 OS 锁”放行，走原有的 .instance.json + 端口探测兜底。
"""

from __future__ import annotations

import ctypes
import os
import time

# 固定名：换名就是换一把锁，两个版本会各跑各的。Global\ 前缀让它在
# 多用户会话（远程桌面多会话、服务会话）下也唯一。
MUTEX_NAME = r"Global\JianLiLieShouSingleInstance"

_ERROR_ALREADY_EXISTS = 183
# 主窗口标题：面板页 <title> 与启动页 <title>（见 frontend/index.html
# 与 app_window._splash_page_html）。Chrome --app 窗口标题就是页面标题。
_APP_WINDOW_TITLES = ("简历猎手", "简历猎手正在启动")
_SW_RESTORE = 9  # ShowWindow：最小化了就还原，没最小化时也是前置


class _NoOsMutex:
    """Sentinel：非 Windows、或 OS 锁不可用时“放行”的标记。

    不是一把真锁——调用方只凭它是不是 None 判断“能不能继续启动”，
    绝不能拿它当已持有锁的证据。
    """


_NO_OS_MUTEX = _NoOsMutex()

# 本进程已持有的锁（Windows 句柄或 _NO_OS_MUTEX 哨兵），None 表示
# 还没抢过。launcher 抢过之后 cli 的 web 分支再抢同一把锁时直接
# 返回它——同进程第二次 CreateMutexW 也会报 ERROR_ALREADY_EXISTS，
# 不加这一层会把自己误判成“已有实例”而退出。
_ACTIVE_LOCK = None


def _is_windows() -> bool:
    """是不是 Windows。单独成函数是为了测试能注入平台判断。"""
    return os.name == "nt"


def _get_kernel32():
    """Return ctypes.windll.kernel32, or None when unavailable."""
    try:
        return ctypes.windll.kernel32
    except Exception:
        return None


def _get_user32():
    """Return ctypes.windll.user32, or None when unavailable."""
    try:
        return ctypes.windll.user32
    except Exception:
        return None


def acquire_single_instance_mutex(_kernel32=None, _is_windows=None):
    """抢单实例锁。

    返回：Windows 上首次抢到返回互斥句柄；另一个进程已持有返回 None；
    非 Windows、或 kernel32 不可用、或 CreateMutexW 失败返回
    _NO_OS_MUTEX 哨兵（放行，走原有的 .instance.json + 端口探测）。
    同一进程重复调用返回第一次抢到的那把锁。永不抛异常。
    """
    global _ACTIVE_LOCK
    if _ACTIVE_LOCK is not None:
        return _ACTIVE_LOCK
    is_windows = _is_windows if _is_windows is not None else _is_windows()
    if not is_windows:
        _ACTIVE_LOCK = _NO_OS_MUTEX
        return _ACTIVE_LOCK
    kernel32 = _kernel32 if _kernel32 is not None else _get_kernel32()
    if kernel32 is None:
        _ACTIVE_LOCK = _NO_OS_MUTEX
        return _ACTIVE_LOCK
    try:
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    except Exception:
        _ACTIVE_LOCK = _NO_OS_MUTEX
        return _ACTIVE_LOCK
    if not handle:
        _ACTIVE_LOCK = _NO_OS_MUTEX
        return _ACTIVE_LOCK
    try:
        already_exists = kernel32.GetLastError() == _ERROR_ALREADY_EXISTS
    except Exception:
        already_exists = False
    if already_exists:
        # 另一个进程已持有：我们这次拿到的句柄只是个引用，关掉它。
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass
        return None
    _ACTIVE_LOCK = handle
    return handle


def release_single_instance_mutex(handle=None, _kernel32=None) -> None:
    """释放单实例锁（正常退出时调；异常退出/强杀由 OS 自动释放）。

    handle 不给时释放本进程持有的那把；哨兵锁不用释放。幂等、永不抛异常。
    """
    global _ACTIVE_LOCK
    target = handle if handle is not None else _ACTIVE_LOCK
    if target is None or target is _NO_OS_MUTEX:
        if handle is None:
            _ACTIVE_LOCK = None
        return
    kernel32 = _kernel32 if _kernel32 is not None else _get_kernel32()
    try:
        if kernel32 is not None:
            kernel32.CloseHandle(target)
    except Exception:
        pass
    finally:
        if handle is None or handle is _ACTIVE_LOCK:
            _ACTIVE_LOCK = None


def _find_main_window(user32):
    """Find the existing main window handle by page title, or None."""
    for title in _APP_WINDOW_TITLES:
        try:
            hwnd = user32.FindWindowW(None, title)
        except Exception:
            hwnd = None
        if hwnd:
            return hwnd
    return None


def bring_existing_window_to_front(
    timeout: float = 5.0,
    interval: float = 0.2,
    _user32=None,
    _sleep=None,
    _clock=None,
) -> bool:
    """把已有实例的主窗口前置/激活。

    窗口还没建好时等一小会儿再唤醒（默认最多等 5 秒、每 0.2 秒找一次），
    到时还没窗口返回 False（调用方照常退出，不静默假装成功）。
    非 Windows 返回 False。永不抛异常。
    """
    sleep = _sleep if _sleep is not None else time.sleep
    clock = _clock if _clock is not None else time.monotonic
    user32 = _user32 if _user32 is not None else _get_user32()
    if user32 is None:
        return False
    deadline = clock() + max(0.0, float(timeout))
    while True:
        hwnd = _find_main_window(user32)
        if hwnd:
            try:
                user32.ShowWindow(hwnd, _SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
            except Exception:
                return False
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def ensure_single_instance(_kernel32=None, _user32=None, _is_windows=None, _activate_timeout: float = 5.0):
    """入口用的单实例守门：抢到锁返回锁（继续启动）；已有实例时把它的
    窗口前置后返回 None（调用方立即退出）。

    非 Windows 返回哨兵锁（继续启动，文件锁行为不变）。永不抛异常。
    """
    lock = acquire_single_instance_mutex(_kernel32=_kernel32, _is_windows=_is_windows)
    if lock is not None:
        return lock
    try:
        bring_existing_window_to_front(timeout=_activate_timeout, _user32=_user32)
    except Exception:
        pass
    return None


__all__ = [
    "MUTEX_NAME",
    "acquire_single_instance_mutex",
    "release_single_instance_mutex",
    "bring_existing_window_to_front",
    "ensure_single_instance",
]
