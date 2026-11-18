"""简历猎手 · exe 启动器（PyInstaller 打包入口）。

双击 exe 后：起本地面板（http://127.0.0.1:8686/），自动打开浏览器，
黑窗口不要关——关掉就是停止服务。数据（配置、数据库、简历）存在 exe
旁边 data/ 文件夹里，不会丢。

带参数运行时透传给 bosshunter 命令行，例如：
    简历猎手.exe --version
    简历猎手.exe web --port 8687 --no-open
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


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


def main() -> None:
    exe_dir = _exe_dir()
    os.chdir(exe_dir)
    node = _bundled_node()
    if node is not None:
        # 浏览器运行时（cdp-proxy.mjs）优先用打包自带的 Node 22+
        os.environ.setdefault("BOSSHUNTER_NODE_PATH", str(node))
    config_path = exe_dir / "config.yaml"
    args = sys.argv[1:] or ["web"]
    if not getattr(sys, "frozen", False):
        print("简历猎手正在启动，请稍等...")
        print("面板地址：http://127.0.0.1:8686/（关掉这个窗口就是停止服务）")
    sys.argv = ["bosshunter", "--config", str(config_path), *args]
    from bosshunter.main import cli

    cli()


if __name__ == "__main__":
    main()
