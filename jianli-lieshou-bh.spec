# -*- mode: python ; coding: utf-8 -*-
"""简历猎手（BossHunter 重建版）· PyInstaller 打包配置（onedir：一个文件夹，里面一个 exe）。

用法（Windows）：pip install . && pip install pyinstaller
    pyinstaller jianli-lieshou-bh.spec
产物在 dist/简历猎手/，双击里面的「简历猎手.exe」。
GitHub Actions 会在 Windows 机器上跑同一套流程（见 .github/workflows/build-exe.yml）。

只读资源打进包里（_MEIPASS），路径与源码里的 __file__ 查找保持一致：
- 前端构建产物 src/bosshunter/web/frontend/dist（打包前先 npm run build）
- config_schema.json、城市数据 data/*.json、浏览器运行时 runtime/（.mjs + site-patterns）
- patchright 的 driver（含 node，供浏览器运行时用，用户不用另装 Node.js）
可写数据（config.yaml、数据库、简历、日志）存在 exe 旁边的 data/ 里，
由启动器 launcher.py 把 --config 指到 exe 目录。
"""
import os

import patchright

block_cipher = None

_pr_driver = os.path.join(os.path.dirname(patchright.__file__), "driver")

a = Analysis(
    ["launcher.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        ("src/bosshunter/web/frontend/dist", "bosshunter/web/frontend/dist"),
        ("src/bosshunter/web/config_schema.json", "bosshunter/web"),
        ("src/bosshunter/data", "bosshunter/data"),
        ("src/bosshunter/browser/runtime", "bosshunter/browser/runtime"),
        (_pr_driver, "patchright/driver"),
        ("config.example.yaml", "."),
    ],
    hiddenimports=[
        "bosshunter",
        "bosshunter.main",
        "bosshunter.web.server",
        "bosshunter.browser",
        "bosshunter.browser.client",
        "bosshunter.browser.runtime",
        "bosshunter.browser.diagnostics",
        "bosshunter.browser.ownership",
        "patchright",
        "patchright.sync_api",
        "brotli",
        "yaml",
        "bottle",
        "anthropic",
        "httpx",
        "rich",
        "click",
        "markdown2",
        "openpyxl",
        "pypdf",
        "fonttools",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="简历猎手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="简历猎手",
)
