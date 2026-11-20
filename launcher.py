"""简历猎手 · exe 启动器（PyInstaller 打包入口）。

双击 exe 后：起本地面板（http://127.0.0.1:8686/），自动在独立窗口打开。
没有黑窗口了：exe 是无窗口程序，关掉面板窗口服务还在后台跑，
想停服务去任务管理器结束「简历猎手.exe」。运行日志写在 exe 旁边
data/logs/jianli-lieshou.log 里（超过 5MB 自动轮换一个 .old 备份）。

带参数运行时透传给 bosshunter 命令行，例如：
    简历猎手.exe --version
    简历猎手.exe web --port 8687 --no-open
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import sys
import time
from pathlib import Path

# 单个日志文件超过这个大小就轮换（启动时检查一次）。
_LOG_MAX_BYTES = 5 * 1024 * 1024


class _TeeOutput:
    """同时写两个流：日志文件 + 原来的 stdout/stderr。

    无窗口 exe 从资源管理器双击启动时，原来的 stdout/stderr 是无效
    句柄（往里写会报 OSError），那一路写失败就悄悄跳过，只保证日志
    文件一定写进去；CI 里重定向到文件的那一路是好的，两边都有。
    只实现 rich/click 用到的最小接口：write/flush/isatty/encoding。
    """

    def __init__(self, *streams: object) -> None:
        self._streams = [s for s in streams if s is not None]

    def write(self, data: str) -> int:
        for stream in self._streams:
            try:
                stream.write(data)  # type: ignore[attr-defined]
            except Exception:
                pass
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()  # type: ignore[attr-defined]
            except Exception:
                pass

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"


def _redirect_output_to_log(exe_dir: Path) -> object | None:
    """Frozen + web 模式：把 stdout/stderr 同时写进日志文件。

    返回 Tee 对象（已装好），失败返回 None（原样不动，绝不挡启动）。
    只在 web 模式调：--version 这类一次性命令的输出要留在控制台上。
    """
    log_dir = exe_dir / "data" / "logs"
    log_path = log_dir / "jianli-lieshou.log"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        if log_path.is_file() and log_path.stat().st_size > _LOG_MAX_BYTES:
            try:
                log_path.replace(log_dir / "jianli-lieshou.log.old")
            except OSError:
                pass
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    tee_out = _TeeOutput(getattr(sys, "stdout", None), log_file)
    tee_err = _TeeOutput(getattr(sys, "stderr", None), log_file)
    # 两路各一个 Tee、共用同一个日志文件：stdout 的内容不进 stderr
    # 的重定向文件（CI 里 out/err 是分开的两个文件），日志里顺序不乱。
    sys.stdout = tee_out  # type: ignore[assignment]
    sys.stderr = tee_err  # type: ignore[assignment]
    return tee_out


def _show_fatal_error_box(message: str) -> bool:
    """弹可见的启动失败框（只在 Windows 打包 exe 里；开发环境不弹真框）。"""
    if not (getattr(sys, "frozen", False) and os.name == "nt"):
        return False
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            f"简历猎手启动失败：\n{message}\n\n详情见 data\\logs\\jianli-lieshou.log",
            "简历猎手启动失败",
            0x10,  # MB_ICONERROR
        )
        return True
    except Exception:
        return False


def _report_startup_failure(exe_dir: Path, message: str, reason_code: str | None = None) -> None:
    """启动失败兜底（v29）：写失败标记文件 + 追加日志 + 弹可见错误框。永不抛异常。

    launcher 这一层是最后一道防线：bosshunter 里没预料到的异常
    （连 run_server 的可见报错都没走到的）到这里也要让用户看得见，
    不能静默退出。
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        marker = exe_dir / "data" / ".startup-failed.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {"message": message, "reason_code": reason_code, "time": stamp},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    try:
        log_dir = exe_dir / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "jianli-lieshou.log", "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] 启动失败[{reason_code or '-'}]：{message}\n")
    except OSError:
        pass
    try:
        _show_fatal_error_box(message)
    except Exception:
        pass


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundled_node() -> Path | None:
    """打包自带的 Node（patchright driver 里的 node），免得用户另装 Node.js。"""
    base = Path(getattr(sys, "_MEIPASS", "") or _exe_dir())
    for name in ("node.exe", "node"):
        candidate = base / "patchright" / "driver" / name
        if candidate.is_file():
            return candidate
    return None


def _ensure_config(exe_dir: Path) -> Path:
    """首次运行：把随包的 config.example.yaml 复制为 exe 旁的 config.yaml。

    打包方在 config.example.yaml 里预置的 AI 配置（含各服务商 Key）就这样
    随包发出去；只在 config.yaml 不存在时复制一次，绝不覆盖用户已有的配置。

    注意：打包 spec 里 config.example.yaml 是 datas ("config.example.yaml", ".")，
    one-dir 模式下它被打进 _MEIPASS（即 _internal/），不在 exe 旁边，所以
    frozen 时先在 _MEIPASS 里找，找不到再回落到 exe_dir。
    """
    config_path = exe_dir / "config.yaml"
    if not config_path.exists():
        candidates = []
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass) / "config.example.yaml")
        candidates.append(exe_dir / "config.example.yaml")
        for example in candidates:
            if example.is_file():
                shutil.copyfile(example, config_path)
                break
    return config_path


def main() -> None:
    exe_dir = _exe_dir()
    os.chdir(exe_dir)
    node = _bundled_node()
    if node is not None:
        # 浏览器运行时（cdp-proxy.mjs）优先用打包自带的 Node 22+
        os.environ.setdefault("BOSSHUNTER_NODE_PATH", str(node))
    config_path = _ensure_config(exe_dir) if getattr(sys, "frozen", False) else exe_dir / "config.yaml"
    args = sys.argv[1:] or ["web"]
    if getattr(sys, "frozen", False) and "web" in args:
        # 无窗口 exe：启动前就把输出接到日志文件，不然双击启动时
        # 往无效的 stdout 句柄写东西会直接炸掉启动（WinError 6）。
        # 只在 web 模式做：--version 这类一次性命令的输出要留在控制台上。
        _redirect_output_to_log(exe_dir)
    if not getattr(sys, "frozen", False):
        print("简历猎手正在启动，请稍等...")
        print("面板地址：http://127.0.0.1:8686/（关掉这个窗口就是停止服务）")
    sys.argv = ["bosshunter", "--config", str(config_path), *args]
    try:
        from bosshunter.main import cli

        cli()
    except SystemExit as exc:
        # v29：启动失败可见化。已在 run_server 里报过可见错误
        # （带 _startup_reported 标记）的不重复弹框；正常退出
        # （SystemExit(0)/None，如 --version）也不报。
        if exc.code not in (None, 0) and not getattr(exc, "_startup_reported", False):
            _report_startup_failure(
                exe_dir,
                str(exc.code) if isinstance(exc.code, str) else f"启动失败（退出码 {exc.code}）",
                reason_code="startup_failed",
            )
        raise
    except Exception as exc:
        _report_startup_failure(exe_dir, f"程序启动失败：{exc}", reason_code="startup_error")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
