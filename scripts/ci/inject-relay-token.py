#!/usr/bin/env python3
"""打包时把中转钥匙注入 config.example.yaml（CI 专用）。

用法：RELAY_APP_TOKEN=<新钥匙> python scripts/ci/inject-relay-token.py [config.example.yaml 路径]

- 钥匙只从环境变量 RELAY_APP_TOKEN 取，绝不写进命令、日志或仓库。
- 只替换空占位（relay_token: ""）；示例里已有非空值时拒绝，避免把
  真值覆盖掉；secret 缺失或为空时直接报错退出，绝不打出没钥匙的包。
- 成功后只打印“已注入”，不打印钥匙本身。
"""

import os
import re
import sys

PLACEHOLDER_RE = re.compile(r'^(\s*relay_token:\s*)""\s*(#.*)?$')
INJECTED_RE = re.compile(r'^\s*relay_token:\s*"[^"]+"\s*$')


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else "config.example.yaml"
    token = os.environ.get("RELAY_APP_TOKEN", "").strip()
    if not token:
        print("错误：环境变量 RELAY_APP_TOKEN 为空——请在仓库 Settings → Secrets 里建好再打包", file=sys.stderr)
        return 2
    if any(ch in token for ch in ('"', "\\", "\n", "\r")):
        print("错误：RELAY_APP_TOKEN 含引号或换行，无法安全写入 YAML", file=sys.stderr)
        return 2
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as exc:
        print(f"错误：读不到 {path}：{exc}", file=sys.stderr)
        return 2
    lines = text.splitlines()
    replaced = 0
    for i, line in enumerate(lines):
        if PLACEHOLDER_RE.match(line):
            lines[i] = f'{PLACEHOLDER_RE.match(line).group(1)}"{token}"'
            replaced += 1
    if replaced != 1:
        print(f"错误：{path} 里 relay_token 空占位不是恰好 1 处（找到 {replaced} 处），拒绝注入", file=sys.stderr)
        return 2
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    # 回读核对：恰好 1 行非空 relay_token，且文件仍是合法 YAML 结构。
    after = open(path, encoding="utf-8").read().splitlines()
    if sum(1 for line in after if INJECTED_RE.match(line)) != 1:
        print(f"错误：注入后 {path} 里 relay_token 行数不对，构建停下", file=sys.stderr)
        return 2
    print("中转钥匙已注入 config.example.yaml（值未打印）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
