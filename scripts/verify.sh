#!/usr/bin/env bash
# 简历猎手 verify：打包前固定验证（验证闭环：每个结论都要有能核对的证据）。
# 用法：bash scripts/verify.sh [成品 zip 路径]
#   默认 zip：$HOME/workspace/releases/简历猎手-windows-v2.zip
# 退出码：0 = 全部硬检查通过；1 = 有硬检查失败。
# WARN 项（中转健康、缺 zip）不计入退出码：中转健康依赖沙箱出网，在沙箱里可能调不通；
# 上线前仍需另行核对（见报告“沙箱里验不了的缺口”）。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ZIP="${1:-$HOME/workspace/releases/简历猎手-windows-v2.zip}"
RELAY_HEALTH="http://43.139.24.90:8687/api/ai/health"
FAIL=0

section() { echo; echo "=== $1 ==="; }
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAIL=1; }
warn() { echo "WARN: $1"; }

# 1. 后端全量测试（临时目录跑，不污染 ./data/bosshunter.db）
# 坑：.venv 是指向原版 BossHunter/src 的 editable 安装，必须 PYTHONPATH=仓库根:src，
# 且入口模块是 bosshunter.main；tests 里有跨文件 import（tests.test_boss_search_regressions），
# 只加 src 会收集中断。
section "1. 后端测试"
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  fail "找不到 $ROOT/.venv/bin/python"
elif ! "$ROOT/.venv/bin/python" -m pytest --version >/dev/null 2>&1; then
  fail ".venv 里没有 pytest（需先装进 .venv，不要装系统 Python）"
else
  TMPDB="$(mktemp -d /tmp/jl-verify-db.XXXXXX)"
  (cd "$TMPDB" && PYTHONPATH="$ROOT:$ROOT/src" "$ROOT/.venv/bin/python" -m pytest "$ROOT/tests" -q -p no:cacheprovider > "$TMPDB/pytest.log" 2>&1)
  rc=$?
  tail -3 "$TMPDB/pytest.log"
  if [ $rc -eq 0 ]; then pass "后端测试全过（日志 $TMPDB/pytest.log）"; else fail "后端测试失败 rc=$rc（日志 $TMPDB/pytest.log）"; fi
fi

# 2. 前端测试
section "2. 前端测试"
FE="$ROOT/src/bosshunter/web/frontend"
if [ ! -d "$FE/node_modules" ]; then
  fail "前端 node_modules 缺失，先跑 npm ci"
else
  (cd "$FE" && npm test > /tmp/jl-verify-frontend-test.log 2>&1)
  rc=$?
  tail -4 /tmp/jl-verify-frontend-test.log
  if [ $rc -eq 0 ]; then pass "前端测试全过"; else fail "前端测试失败 rc=$rc（日志 /tmp/jl-verify-frontend-test.log）"; fi
fi

# 3. 前端构建
section "3. 前端构建"
(cd "$FE" && npm run build > /tmp/jl-verify-frontend-build.log 2>&1)
rc=$?
tail -4 /tmp/jl-verify-frontend-build.log
if [ $rc -eq 0 ]; then pass "tsc && vite build 成功"; else fail "前端构建失败 rc=$rc（日志 /tmp/jl-verify-frontend-build.log）"; fi

# 4. 静态检查：品牌残留（用户可见源码；代码内部模块名 bosshunter 不在此 grep 范围外——
# 实测 frontend/src 内 BossHunter/bosshunter/v2.4.0 均为零命中，见验证报告）
section "4. 品牌残留"
if grep -rniE 'BossHunter|v2\.4\.0|github\.com/powerycy|shengjidaguai-china|欢迎点 Star' "$FE/src" > /tmp/jl-verify-brand.log 2>&1; then
  fail "前端源码仍有品牌残留："
  cat /tmp/jl-verify-brand.log
else
  pass "前端源码无 BossHunter / v2.4.0 / 开源链接残留"
fi

# 5. 静态检查：用户端禁词（管理员面板除外；*.test.* 是断言“界面上没有这些字”的测试本身，不算）
section "5. 用户端禁词"
if grep -rn -E '密钥|模型' --exclude='*.test.tsx' --exclude='*.test.ts' "$FE/src/pages" "$FE/src/components" | grep -v 'AdminPage' > /tmp/jl-verify-words.log 2>&1; then
  fail "用户端仍有 密钥/模型 字眼："
  cat /tmp/jl-verify-words.log
else
  pass "用户端无 密钥/模型 字眼"
fi
if grep -rn -E 'api_key|API key' --exclude='*.test.tsx' --exclude='*.test.ts' "$FE/src" > /tmp/jl-verify-words2.log 2>&1; then
  fail "前端源码仍有 api_key / API key："
  cat /tmp/jl-verify-words2.log
else
  pass "前端源码无 api_key / API key"
fi

# 6. 成品 zip 复验
section "6. 成品 zip"
if [ ! -f "$ZIP" ]; then
  warn "找不到成品 zip（$ZIP），跳过 zip 检查"
else
  python3 - "$ZIP" <<'PY' > /tmp/jl-verify-zip.log 2>&1
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
bad = z.testzip()
names = z.namelist()
tops = sorted({n.split('/')[0] for n in names})
nonascii_no_utf8 = [n for n in names if any(ord(c) > 127 for c in n) and not (z.getinfo(n).flag_bits & 0x800)]
print(f"testzip: {bad}")
print(f"files: {len(names)}")
print(f"top-level: {tops}")
print(f"non-ascii WITHOUT utf8 flag: {len(nonascii_no_utf8)} {nonascii_no_utf8[:5]}")
sys.exit(0 if bad is None and tops == ['简历猎手-windows'] and not nonascii_no_utf8 else 1)
PY
  rc=$?
  cat /tmp/jl-verify-zip.log
  if [ $rc -eq 0 ]; then pass "zip 结构 OK（testzip 干净、顶层唯一、UTF-8 标志齐全）"; else fail "zip 结构检查失败"; fi
  unzip -t -q "$ZIP" > /tmp/jl-verify-unzip.log 2>&1
  if [ $? -eq 0 ]; then pass "unzip -t 全过"; else fail "unzip -t 有错误（见 /tmp/jl-verify-unzip.log）"; fi
  TMPZ="$(mktemp -d /tmp/jl-verify-zip.XXXXXX)"
  (cd "$TMPZ" && unzip -q -o "$ZIP" '简历猎手-windows/config.yaml' '简历猎手-windows/_internal/config.example.yaml' '简历猎手-windows/_internal/bosshunter/web/config_schema.json' 2>/dev/null)
  if grep -r 'sk-' "$TMPZ" > /tmp/jl-verify-sk.log 2>&1; then
    fail "config 文件含 sk- 字串："
    cat /tmp/jl-verify-sk.log
  else
    pass "config 文件无 sk- 字串"
  fi
  (cd "$TMPZ" && unzip -q -o "$ZIP" '简历猎手-windows/_internal/bosshunter/web/frontend/dist/*' 2>/dev/null)
  DIST="$TMPZ/简历猎手-windows/_internal/bosshunter/web/frontend/dist"
  if grep -q '<title>简历猎手</title>' "$DIST/index.html"; then pass "dist 标题为 简历猎手"; else fail "dist 标题不是 简历猎手"; fi
  KF=$(grep -o '@keyframes jl-[a-z-]*' "$DIST"/assets/*.css | sort -u | wc -l)
  if [ "$KF" -ge 6 ]; then pass "dist 动效 keyframes $KF 个"; else fail "dist 动效 keyframes 只有 $KF 个"; fi
  if grep -qi 'FB6013' -r "$DIST" > /tmp/jl-verify-orange.log 2>&1; then
    fail "dist 仍有旧橙色 #FB6013"
    cat /tmp/jl-verify-orange.log
  else
    pass "dist 无旧橙色 #FB6013"
  fi
  rm -rf "$TMPZ"
fi

# 7. 中转健康（WARN，不计入退出码：沙箱出网受限，直连 IP 有时通有时不通）
section "7. 中转健康"
if curl -sS -m 15 "$RELAY_HEALTH" | tee /tmp/jl-verify-relay.log | grep -q '"ok": true'; then
  pass "中转健康 OK：$(cat /tmp/jl-verify-relay.log)"
else
  warn "中转健康未确认（沙箱出网受限可能调不通，见 /tmp/jl-verify-relay.log；上线前需另行核对）"
fi

# 8. 安装/卸载口径一致性（v39）：Inno 脚本与 src/bosshunter/installer.py
# 必须是同一份口径（默认路径、清理清单、安全谓词、停进程判定）。
# Inno 编译本身只能在 Windows CI 里跑，Linux 上只核对两处文本一致。
section "8. 安装/卸载口径一致性"
if "$ROOT/.venv/bin/python" "$ROOT/scripts/check-installer-consistency.py" > /tmp/jl-verify-installer.log 2>&1; then
  pass "Inno 脚本与 installer.py 口径一致（日志 /tmp/jl-verify-installer.log）"
else
  fail "安装/卸载口径不一致（日志 /tmp/jl-verify-installer.log）"
  cat /tmp/jl-verify-installer.log
fi

# 9. 浏览器代理：后台标签页新开窗口要被最小化（v44 B 项）
# 假 Chrome + 真 cdp-proxy 联调；ws 取自前端 node_modules（第 2 节已保证存在）。
section "9. 浏览器代理后台标签最小化"
if node "$ROOT/scripts/test-cdp-proxy-minimize.mjs" > /tmp/jl-verify-proxy.log 2>&1; then
  pass "后台标签新开窗口被最小化、复用/前台窗口不动（日志 /tmp/jl-verify-proxy.log）"
else
  fail "浏览器代理最小化检查失败（日志 /tmp/jl-verify-proxy.log）"
  cat /tmp/jl-verify-proxy.log
fi

echo
if [ $FAIL -eq 0 ]; then echo "VERIFY: ALL HARD CHECKS PASSED"; else echo "VERIFY: FAILED"; fi
exit $FAIL
