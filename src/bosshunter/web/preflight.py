"""Structured, user-facing checks before starting a workbench task."""

from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

from bosshunter.lazy_module import lazy_module

# v30 启动提速：httpx 只在第一次真正发 HTTP 请求时才导入
# （启动首屏用不上）。代理是真实的模块全局名，函数里直接用、
# 测试照常 patch（见 bosshunter.lazy_module）。
httpx = lazy_module("httpx")

from bosshunter import __version__
from bosshunter.ai.credentials import (
	get_ai_api_key,
	get_ai_base_url,
	get_ai_base_url_source,
	get_ai_service,
	get_relay_health,
	get_relay_url,
	get_service_credentials,
)
from bosshunter.browser.diagnostics import run_browser_diagnostics
from bosshunter.collection.orchestrator import normalize_collection_options

VALID_MODES = {"full", "collect", "rescore", "monitor"}

# BOSS 登录页精确判定，与采集侧 boss.py 的 JS 正则同一口径：
# /\/web\/user\/(?:login|\?ka=header-login)/i —— 只有登录页才算离线。
# 绝不能再用 "/user/" 宽松子串：已登录用户停在个人中心页
# https://www.zhipin.com/web/user/ 也会被误判掉线，写库后还会触发
# 熔断停用该平台直到当天结束（v10 有意规则，见 db.record_platform_login_state）。
_BOSS_LOGIN_URL_RE = re.compile(r"/web/user/(?:login|\?ka=header-login)", re.IGNORECASE)


def is_boss_login_url(url: str | None) -> bool:
	"""True only when a BOSS tab URL is on the login page (see _BOSS_LOGIN_URL_RE)."""
	if not url:
		return False
	return bool(_BOSS_LOGIN_URL_RE.search(str(url)))


def _login_state(login_states: dict | None, platform: str) -> str:
	"""Normalize one platform's login state from a login_states mapping.

	值可以是字符串（"logged_in"）或 DB 行那样的 dict（{"status": ...}），
	取不到 / 不是字符串时一律按 "unknown" 处理，调用方保持原有行为。
	"""
	if not isinstance(login_states, dict):
		return "unknown"
	value = login_states.get(platform)
	if isinstance(value, dict):
		value = value.get("status")
	if isinstance(value, str):
		return value.strip().lower()
	return "unknown"


# 四平台的固定顺序：默认顺序、已登录兜底都按它排，不依赖 dict 顺序。
_PLATFORM_ORDER = ("boss", "zhilian", "51job", "liepin")


def _enabled_platforms(config: dict | None) -> list[str]:
	"""Return the platforms the user enabled in config, in canonical order.

	只认 config.platforms.<平台>.enabled，不硬编码任何平台：BOSS 关了
	就不在里面，智联/前程无忧/猎聘开了就在里面。
	"""
	if not isinstance(config, dict):
		return []
	configured = config.get("platforms")
	if not isinstance(configured, dict):
		return []
	return [
		platform
		for platform in _PLATFORM_ORDER
		if isinstance(configured.get(platform), dict) and configured[platform].get("enabled")
	]


def resolve_check_platforms(
	config: dict | None,
	collection_options: dict | None = None,
	login_states: dict | None = None,
) -> list[str]:
	"""Resolve which platforms a preflight browser check should validate.

	优先级（v37②a：按用户打算用的平台动态校验，不再死绑 BOSS）：
	1. 本次任务明确选的平台（collection_options.platform_order）；
	2. 配置里启用的平台（config.platforms.*.enabled）；
	3. 配置没写任何启用信息时，实际已登录的平台；
	4. 以上全都没有，才回落到 ["boss"]（全新/旧配置的兜底，不拦新用户）。
	"""
	if isinstance(collection_options, dict):
		order = collection_options.get("platform_order")
		if isinstance(order, list):
			selected = [str(value).strip() for value in order if str(value).strip()]
			if selected:
				return selected
	enabled = _enabled_platforms(config)
	if enabled:
		return enabled
	if isinstance(login_states, dict):
		logged_in = [
			platform for platform in _PLATFORM_ORDER if _login_state(login_states, platform) == "logged_in"
		]
		if logged_in:
			return logged_in
	return ["boss"]


def collect_preflight_checks(mode: str, config: dict, options: dict | None = None, allow_launch: bool = False, login_states: dict | None = None) -> list[dict[str, str]]:
	"""Collect configuration, AI connectivity, and browser readiness checks.

	默认只做检测，不拉起浏览器（“检查一下能不能跑”走这里）；
	allow_launch=True 只给真正启动任务的入口（Agent 任务启动门禁），
	那时拉起是用户主动要跑任务的一部分。
	"""
	collection_options = None
	full_options: dict | None = None
	full_options_error: ValueError | None = None
	if mode == "collect":
		try:
			collection_options = normalize_collection_options(config, options)
		except ValueError as exc:
			collection_options = None
			checks = [_check("collection_options", "采集参数", "error", str(exc), "请在岗位采集窗口修正平台、关键词、城市或目标数量。", "config")]
		else:
			checks = _configuration_checks(mode, config, collection_options)
			checks.extend(_collection_city_checks(collection_options))
	elif mode == "full":
		# v37②a：全流程的浏览器检查也要按本次实际选的平台来，不能再
		# 用 collection_options=None 时的默认集合（以前只查 BOSS）。
		# 这里先算出 full_options，后面城市检查和平台能力检查复用同一份。
		try:
			full_options = normalize_collection_options(config, options)
		except ValueError as exc:
			full_options_error = exc
		checks = _configuration_checks(mode, config, options)
	else:
		checks = _configuration_checks(mode, config, options)
	if mode not in VALID_MODES:
		return checks
	ai_required = mode in {"full", "rescore"} or (
		mode == "collect" and bool(collection_options and collection_options.get("auto_score"))
	)
	ai_configured = bool(get_ai_api_key(config)) or get_ai_service(config) in {"local", "relay"}
	if ai_required and not ai_configured:
		# 未配置 AI 不阻塞：评分走本地规则，招呼语走固定模板。
		checks.append(_check("ai_connection", "AI 接口连接", "pass", "未配置 AI，使用本地规则评分", "未配置 AI 服务也能跑全流程，评分走本地规则。"))

	if not ai_required:
		# Pure collection must not perform an AI connectivity request. This keeps
		# ``auto_score=false`` genuinely AI-free even when credentials are saved.
		checks.append(_check("ai_connection", "AI 接口连接", "pass", "纯采集不需要 AI", "关闭“采集后自动评分”时不会调用 AI。"))
		try:
			checks.extend(check_browser_connection(deepcopy(config), collection_options, allow_launch=allow_launch, login_states=login_states))
		except Exception:
			checks.append(_check("browser_runtime", "浏览器连接", "error", "浏览器连接检测失败", "请重新启动简历猎手后再试。", "browser"))
		return checks

	browser_options = collection_options if mode == "collect" else full_options
	with ThreadPoolExecutor(max_workers=2) as executor:
		pending_checks: list = []
		if ai_configured:
			pending_checks.append((
				executor.submit(check_ai_connection, deepcopy(config), True),
				_check("ai_connection", "AI 接口连接", "error", "AI 接口检测失败", "请检查 AI 设置后重试。", "config"),
			))
		pending_checks.append((
			executor.submit(check_browser_connection, deepcopy(config), browser_options, allow_launch, login_states),
			_check("browser_runtime", "浏览器连接", "error", "浏览器连接检测失败", "请重新启动简历猎手后再试。", "browser"),
		))
		for future, fallback in pending_checks:
			try:
				checks.extend(future.result())
			except Exception:
				checks.append(fallback)
	if mode == "full":
		if full_options_error is not None:
			checks.append(
				_check(
					"full_flow_platform",
					"全流程采集设置",
					"error",
					str(full_options_error),
					"请在全流程采集设置中选择平台并填写关键词、城市和目标数量。",
					"config",
				)
			)
		elif full_options is not None:
			checks.extend(_collection_city_checks(full_options))
			checks.append(
				_check(
					"full_flow_platform",
					"全流程平台能力",
					"pass",
					"已选择的平台均接入全流程",
					f"执行顺序：{' → '.join(full_options.get('platform_order', []))}；只有支持投递的平台可进入全流程。",
				)
			)
	return checks


def _collection_city_checks(options: dict) -> list[dict[str, str]]:
	boss = options.get("platforms", {}).get("boss", {})
	unresolved = [city for city in boss.get("cities", []) if not boss.get("city_codes", {}).get(city)]
	if not unresolved:
		return []
	return [_check(
		"boss_cities", "BOSS 搜索地区", "warning", f"将跳过未识别地区：{'、'.join(unresolved)}",
		"其他有效城市继续采集；原配置保留。省份不会自动替换成省会；如需该地区，请填写具体城市或提供已确认的 BOSS 地区编码。", "config",
	)]


def check_ai_connection(config: dict, required: bool = True) -> list[dict[str, str]]:
	"""Validate AI credentials and perform a no-token model-list request."""
	return [*_check_ai_connection(config, required), *_python_runtime_checks()]


def _check_ai_connection(config: dict, required: bool) -> list[dict[str, str]]:
	"""Run the AI configuration and connectivity checks."""
	ai_cfg = config.get("ai", {}) if isinstance(config.get("ai"), dict) else {}
	severity = "error" if required else "warning"
	provider = str(ai_cfg.get("provider") or "anthropic")
	service = get_ai_service(config)
	# 智谱免费档：限并发、配置也可能还没放好，任何异常都只提示 warning，
	# 不拦启动；任务失败时回落本地规则。
	if service == "zhipu":
		severity = "warning"
	if service == "relay":
		# 云端中转：不需要本地 Key，只探健康接口；任何异常都只提示
		# warning，不拦启动；任务失败时回落本地规则。
		severity = "warning"
		health = get_relay_health(config, timeout=8)
		if health and health.get("ok"):
			provider = str(health.get("provider") or "云端中转").strip() or "云端中转"
			model = str(health.get("model") or "未知").strip() or "未知"
			ai_state = "开启" if health.get("ai_enabled") else "关闭"
			return [
				_check(
					"ai_connection",
					"AI 接口连接",
					"pass",
					f"云端中转连接正常（{provider}/{model}）",
					f"中转地址：{get_relay_url(config)}；服务端 AI 开关：{ai_state}。"
					"本次仅检测中转健康接口，未发送生成请求。",
				)
			]
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				"warning",
				"中转连接失败，将回落本地规则",
				"云端中转暂时连不上，评分走本地规则，招呼语用固定模板；"
				"请稍后重试，或联系管理员检查中转服务。",
				"config",
			)
		]
	if service == "local":
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				"pass",
				"本地规则评分，不调用 AI",
				"评分按本地规则算，招呼语用固定模板，定制简历发原简历；不用联网等 AI。",
			)
		]
	credential = get_ai_api_key(config)
	base_url_value = get_ai_base_url(config)
	base_url_source = get_ai_base_url_source(config)
	model = str(ai_cfg.get("model") or "").strip()
	if not credential:
		return [
			_check(
				"ai_credentials",
				"AI 服务",
				severity,
				"AI 服务未配置",
				"AI 服务未配置，无法生成 AI 回复或定制简历；单独监测仍可启动。",
				"config",
			)
		]
	if not model:
		return [
			_check(
				"ai_model",
				"AI 模型",
				severity,
				"尚未填写模型名称",
				"打开“配置 → AI 设置”，填写接口支持的模型名称。",
				"config",
			)
		]

	base_url = str(base_url_value or "").strip()
	if service == "custom" and provider == "openai_compatible" and not base_url:
		return [
			_check(
				"ai_base_url",
				"AI Base URL",
				severity,
				"兼容 API 缺少 Base URL",
				"打开“配置 → AI 设置”，填写服务商提供的 Base URL。",
				"config",
			)
		]

	if provider == "openai_compatible":
		models_url = f"{base_url.rstrip('/')}/models"
		headers = {"Authorization": f"Bearer {credential}"}
	else:
		models_url = (
			f"{base_url.rstrip('/')}/v1/models"
			if base_url
			else "https://api.anthropic.com/v1/models"
		)
		# 凭证块整体优先：config 任一凭证字段非空就不读 env，与 credentials 解析保持一致。
		service_creds = get_service_credentials(config, "anthropic")
		config_api_key = service_creds.get("api_key") or ai_cfg.get("api_key")
		config_auth_token = service_creds.get("auth_token") or ai_cfg.get("auth_token")
		if config_api_key or config_auth_token:
			auth_token = config_auth_token
		else:
			auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
		headers = (
			{"Authorization": f"Bearer {auth_token}"}
			if auth_token
			else {"x-api-key": str(credential)}
		)
		headers["anthropic-version"] = "2023-06-01"

	try:
		result = httpx.get(models_url, headers=headers, timeout=8, follow_redirects=True, trust_env=False)
	except httpx.TimeoutException:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				"AI 接口连接超时",
				"请检查 Base URL、网络或代理设置，然后重新检测。",
				"config",
			)
		]
	except httpx.InvalidURL:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				"AI 接口地址无效",
				"请检查 Base URL 是否填写正确；IPv6 地址需用方括号包裹，如 http://[::1]:8000。",
				"config",
			)
		]
	except httpx.HTTPError:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				"无法连接 AI 接口",
				"请检查 Base URL、网络或代理设置，然后重新检测。",
				"config",
			)
		]
	except TypeError:
		# Dependency initialization errors may echo request data, so never expose
		# the original exception text in this user-facing diagnostic.
		return [
			_check(
				"ai_runtime",
				"AI 运行环境",
				severity,
				"AI 请求尚未发出：本地 Python/HTTPX 运行环境异常",
				f"当前 Python {sys.version.split()[0]}、HTTPX {httpx.__version__}、简历猎手 {__version__}；"
				"请使用稳定版 Python 3.11、3.12 或 3.13，并重新创建虚拟环境。",
			)
		]

	if result.status_code in {401, 403}:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				"AI 服务鉴权失败",
				"服务商拒绝了请求，请联系客服检查服务配置。",
				"config",
			)
		]
	if result.status_code in {404, 405}:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				"warning",
				"接口可访问，但无法自动验证模型",
				"该兼容服务未提供模型列表接口；简历猎手启动时仍会尝试消息接口。",
				"config",
			)
		]
	if result.status_code == 429:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				"warning",
				"AI 接口已连接，但当前被限流",
				"请检查额度或稍后重试。",
				"config",
			)
		]
	if result.status_code >= 400:
		return [
			_check(
				"ai_connection",
				"AI 接口连接",
				severity,
				f"AI 接口返回异常状态 {result.status_code}",
				"请检查 Base URL 和服务商状态。",
				"config",
			)
		]

	return [
		_check(
			"ai_connection",
			"AI 接口连接",
			"pass",
			"智谱接口连接正常" if service == "zhipu" else "AI 接口连接正常",
			f"已验证服务配置，当前模型：{model}；服务地址来源：{base_url_source or '官方默认'}。"
			"本次仅检测模型列表接口，未发送生成请求；评分等任务的参数兼容性需在实际调用时验证。",
		)
	]


def _python_runtime_checks() -> list[dict[str, str]]:
	"""Warn when dependency behavior may differ on a prerelease interpreter."""
	if sys.version_info.releaselevel == "final":
		return []
	return [
		_check(
			"python_runtime",
			"Python 运行环境",
			"warning",
			"当前使用的是 Python 预发布版本",
			f"检测到 Python {sys.version.split()[0]}；预发布解释器可能与 HTTP 客户端不兼容。"
			"请使用稳定版 Python 3.11、3.12 或 3.13，并重新创建虚拟环境。",
		)
	]


def check_browser_connection(config: dict, collection_options: dict | None = None, allow_launch: bool = False, login_states: dict | None = None) -> list[dict[str, str]]:
	"""Translate Browser Runtime diagnostics into actionable checks.

	只做检测，不拉起浏览器（“检查一下能不能跑”是检测；拉起只在
	用户点“去登录”或真正启动找工作/投递任务时）。
	allow_launch=True 只给任务启动门禁传。
	"""
	result = run_browser_diagnostics(config, allow_launch=allow_launch)
	checks: list[dict[str, str]] = []
	node = result.get("node") or {}
	errors = [str(error) for error in result.get("errors") or []]

	if result.get("runtime"):
		checks.append(
			_check(
				"browser_runtime",
				"浏览器运行组件",
				"pass",
				"简历猎手浏览器运行组件正常",
				f"本地连接地址：{result.get('runtime_url') or '已就绪'}。",
			)
		)
	elif not node.get("available"):
		checks.append(
			_check(
				"browser_runtime",
				"浏览器运行组件",
				"error",
				"缺少可用的 Node.js",
				"简历猎手无法启动浏览器连接组件，请安装 Node.js 22+ 后重启。",
				"browser",
			)
		)
	else:
		if any("non-BossHunter" in error for error in errors):
			message = "浏览器连接端口被其他程序占用"
			detail = "请停止占用本地 Runtime 端口的程序，或修改 browser.proxy_port 后重启。"
		else:
			message = "简历猎手浏览器运行组件未启动"
			detail = "点任意一个平台的“去登录”，程序会自动打开工作浏览器并连上；还不行就点“再检查一次”。"
		checks.append(_check("browser_runtime", "浏览器运行组件", "error", message, detail, "browser"))

	if result.get("runtime") and not result.get("chrome"):
		checks.append(
			_check(
				"chrome_connection",
				"Chrome 远程调试",
				"error",
				"没有连接到开启远程调试的 Google Chrome",
				"点任意一个平台的“去登录”，程序会自动打开一个能连接的浏览器窗口，再点一次检查。",
				"browser",
			)
		)
	elif result.get("chrome"):
		product = str(result.get("browser_product") or "").strip()
		browser_name = str(result.get("browser_name") or "").strip()
		# Chrome 与 Edge 都可用：Edge 走同一套 CDP 远程调试，不再当错误。
		supported_browser = (
			browser_name in {"Google Chrome", "Google Chrome Canary", "Microsoft Edge"}
			if browser_name
			else not product or product.startswith(("Chrome/", "Edg/"))
		)
		if not supported_browser:
			detected_browser = browser_name or product
			checks.append(
				_check(
					"chrome_product",
					"浏览器类型",
					"error",
					f"当前连接的不是 Google Chrome 或 Microsoft Edge（{detected_browser}）",
					"请关掉现在这个浏览器窗口，再点任意一个平台的“去登录”。",
					"browser",
				)
			)
		else:
			if browser_name:
				message = f"{browser_name}已连接"
			elif product:
				message = f"浏览器已连接（{product}）"
			else:
				message = "浏览器已连接"
			checks.append(
				_check(
					"chrome_connection",
					"浏览器远程调试",
					"pass",
					message,
					"远程调试连接正常。",
				)
			)

	if result.get("chrome"):
		# v37②a：查用户打算用的平台（本次任务选的 → 配置启用的 →
		# 实际已登录的），不再没给采集参数就默认只查 BOSS。
		selected_platforms = set(resolve_check_platforms(config, collection_options, login_states))
		# v37②a（补）：缺页只对“这次明确选了的平台”报错。默认顺序/
		# 配置启用带进来的平台缺页只警告——全新/默认配置下四个平台
		# 默认全开，要求四个标签页全开才能过预检会把所有人都拦住；
		# 用户“任意可用平台已就绪即可跑”，没准备好的平台采集时跳过。
		# 没带 platform_order_explicit 标记的手写 options 视为明确选择
		# （旧行为：缺页报错），只有 normalize 明确标了非显式才降级。
		if isinstance(collection_options, dict) and "platform_order_explicit" in collection_options:
			explicit_selection = bool(collection_options["platform_order_explicit"])
		elif isinstance(collection_options, dict) and collection_options.get("platform_order"):
			explicit_selection = True
		else:
			explicit_selection = False
		platform_status = "error" if explicit_selection else "warning"
		if "boss" in selected_platforms:
			boss_tab = result.get("boss_tab")
			if boss_tab:
				url = str(boss_tab.get("url") or "")
				if is_boss_login_url(url):
					checks.append(
						_check(
							"boss_login",
							"BOSS 直聘登录",
							"warning",
							"BOSS 直聘可能尚未登录",
							"请在已连接的 Google Chrome 中完成登录，再开始任务。",
							"browser",
						)
					)
				else:
					checks.append(
						_check(
							"boss_tab",
							"BOSS 直聘页面",
							"pass",
							"已发现 BOSS 直聘页面",
							"请确认该页面已登录招聘平台账号。",
						)
					)
			elif _login_state(login_states, "boss") == "logged_in":
				# 已登录却没打开页面：开始任务时会自动打开，不拦。
				checks.append(
					_check(
						"boss_tab",
						"BOSS 直聘页面",
						"warning",
						"未发现已打开的 BOSS 直聘页面，开始任务时会自动打开",
						"BOSS 直聘已登录，开始任务时会自动打开对应的页面。",
						"browser",
					)
				)
			else:
				checks.append(
					_check(
						"boss_tab",
						"BOSS 直聘页面",
						"warning",
						"未发现已打开的 BOSS 直聘页面",
						"请在已连接的 Google Chrome 中打开 www.zhipin.com 并确认已经登录。",
						"browser",
					)
				)
		if "zhilian" in selected_platforms:
			zhilian_tab = result.get("zhilian_tab")
			page_state = result.get("zhilian_page") or {}
			state = str(page_state.get("status") or "unknown") if zhilian_tab else "missing"
			if state == "ready":
				checks.append(_check("zhilian_tab", "智联招聘页面", "pass", "智联搜索页可用，未发现登录墙", "采集不会点击投递或申请按钮。"))
			elif state == "login_required":
				checks.append(_check("zhilian_login", "智联招聘登录", platform_status, "智联页面要求登录", "请在已连接的浏览器中完成智联登录后重新检查。", "browser"))
			elif state == "blocked":
				checks.append(_check("zhilian_blocked", "智联页面状态", platform_status, "智联页面受到拦截", str(page_state.get("message") or "请人工处理验证码、频率限制或账号异常。"), "browser"))
			elif state == "missing":
				if _login_state(login_states, "zhilian") == "logged_in":
					# 已登录却没打开页面：开始任务时会自动打开，降级为 warning，不拦。
					checks.append(_check("zhilian_tab", "智联招聘页面", "warning", "未发现已打开的智联招聘页面，开始任务时会自动打开", "智联已登录，开始任务时会自动打开对应的页面。", "browser"))
				else:
					checks.append(_check("zhilian_tab", "智联招聘页面", platform_status, "未发现已打开的智联招聘页面", "请在已连接的浏览器中打开已登录的智联职位页后重新检查。", "browser"))
			else:
				checks.append(_check("zhilian_page", "智联页面结构", platform_status, "未识别到智联搜索页", str(page_state.get("message") or "请打开智联职位搜索页后重新检查。"), "browser"))
		if "51job" in selected_platforms:
			checks.extend(_platform_page_checks(result, "51job", "前程无忧", login_states, platform_status))
		if "liepin" in selected_platforms:
			checks.extend(_platform_page_checks(result, "liepin", "猎聘", login_states, platform_status))
	return checks


def _platform_page_checks(result: dict, platform: str, display: str, login_states: dict | None = None, missing_status: str = "error") -> list[dict[str, str]]:
	"""Login checks for 51job / Liepin, mirroring the Zhilian DOM check.

	只读已打开标签页的 DOM 状态（diagnostics 已查好放在 result 里），
	不新开页面：ready=已登录可用，login_required=要求登录，
	block=拦截，missing=没打开该平台页面，其他=结构未识别。
	missing_status：缺页/要求登录/被拦截时的严重程度——明确选了该
	平台时是 error，没明确选时调用方传 warning，不拦其他平台。
	"""
	tab_key = "job51_tab" if platform == "51job" else "liepin_tab"
	page_key = "job51_page" if platform == "51job" else "liepin_page"
	tab = result.get(tab_key)
	page_state = result.get(page_key) or {}
	state = str(page_state.get("status") or "unknown") if tab else "missing"
	message = str(page_state.get("message") or "")
	if state == "ready":
		return [_check(f"{platform}_tab", f"{display}页面", "pass", f"{display}搜索页可用，未发现登录墙", "采集不会点击投递或申请按钮。")]
	if state == "login_required":
		return [_check(f"{platform}_login", f"{display}登录", missing_status, f"{display}页面要求登录", f"请在已连接的浏览器中完成{display}登录后重新检查。", "browser")]
	if state == "blocked":
		return [_check(f"{platform}_blocked", f"{display}页面状态", missing_status, f"{display}页面受到拦截", message or "请人工处理验证码、频率限制或账号异常。", "browser")]
	if state == "missing":
		if _login_state(login_states, platform) == "logged_in":
			# 已登录却没打开页面：开始任务时会自动打开，降级为 warning，不拦。
			return [_check(f"{platform}_tab", f"{display}页面", "warning", f"未发现已打开的{display}页面，开始任务时会自动打开", f"{display}已登录，开始任务时会自动打开对应的页面。", "browser")]
		return [_check(f"{platform}_tab", f"{display}页面", missing_status, f"未发现已打开的{display}页面", f"请在已连接的浏览器中打开已登录的{display}职位页后重新检查。", "browser")]
	return [_check(f"{platform}_page", f"{display}页面结构", missing_status, f"未识别到{display}搜索页", message or f"请打开{display}职位搜索页后重新检查。", "browser")]


# ─── 平台登录态：从诊断结果推导并持久化 ─────────────────────────

_PLATFORM_DISPLAY_NAMES = {"boss": "BOSS 直聘", "zhilian": "智联", "51job": "前程无忧", "liepin": "猎聘"}


def derive_platform_login_states(result: dict) -> dict[str, dict[str, str | None]]:
	"""从一次 run_browser_diagnostics 结果推导四平台登录态。

	判定口径：BOSS 看标签页地址是否停在登录页；其余三平台看 DOM
	检测结果（ready=已登录，login_required=已掉线，blocked/结构未识别/
	没打开页面=无法判断 unknown）。浏览器未连接时全部 unknown，
	绝不把“连不上”误标成掉线。
	"""
	if not isinstance(result, dict) or not result.get("chrome"):
		return {
			platform: {"status": "unknown", "detail": "浏览器未连接，登录态无法判断"}
			for platform in ("boss", "zhilian", "51job", "liepin")
		}
	states: dict[str, dict[str, str | None]] = {}
	boss_tab = result.get("boss_tab")
	if isinstance(boss_tab, dict):
		url = str(boss_tab.get("url") or "")
		if is_boss_login_url(url):
			states["boss"] = {"status": "logged_out", "detail": "BOSS 直聘页面停在登录页"}
		else:
			states["boss"] = {"status": "logged_in", "detail": "已发现 BOSS 直聘页面，未见登录页"}
	else:
		states["boss"] = {"status": "unknown", "detail": "未发现已打开的 BOSS 直聘页面"}
	for platform, tab_key, page_key in (
		("zhilian", "zhilian_tab", "zhilian_page"),
		("51job", "job51_tab", "job51_page"),
		("liepin", "liepin_tab", "liepin_page"),
	):
		display = _PLATFORM_DISPLAY_NAMES[platform]
		tab = result.get(tab_key)
		page_state = result.get(page_key) or {}
		page_status = str(page_state.get("status") or "unknown") if isinstance(page_state, dict) else "unknown"
		page_message = str(page_state.get("message") or "") if isinstance(page_state, dict) else ""
		if not isinstance(tab, dict):
			states[platform] = {"status": "unknown", "detail": f"未发现已打开的{display}页面"}
		elif page_status == "ready":
			states[platform] = {"status": "logged_in", "detail": page_message or f"已发现{display}搜索页，未发现登录墙"}
		elif page_status == "login_required":
			states[platform] = {"status": "logged_out", "detail": page_message or f"{display}页面要求登录"}
		else:
			states[platform] = {"status": "unknown", "detail": page_message or f"{display}页面状态无法判断"}
	return states


def refresh_platform_login_states(db, config: dict | None = None, result: dict | None = None) -> dict[str, dict[str, str | None]]:
	"""跑一次浏览器诊断并把四平台登录态写入 platform_login_state。

	供 preflight 接口与后台巡检共用。浏览器连不上时全部写 unknown
	（不误标 logged_out、不触发熔断）；掉线写入时按 db.record_
	platform_login_state 的联动暂停该平台当天任务。
	"""
	from bosshunter.db import record_platform_login_state

	if result is None:
		# 没带结果时自己跑一次诊断：同样只做检测，不拉起浏览器。
		result = run_browser_diagnostics(config, allow_launch=False)
	states = derive_platform_login_states(result)
	for platform, state in states.items():
		record_platform_login_state(db, platform, str(state["status"]), state.get("detail"))
	return states


def error_messages(checks: list[dict[str, str]]) -> list[str]:
	"""Return backward-compatible blocker messages from structured checks."""
	return [
		f"{check['message']}：{check['detail']}"
		for check in checks
		if check.get("status") == "error"
	]


def _configuration_checks(mode: str, config: dict, options: dict | None = None) -> list[dict[str, str]]:
	checks: list[dict[str, str]] = []
	if mode not in VALID_MODES:
		checks.append(
			_check(
				"task_mode",
				"任务模式",
				"error",
				f"不支持的任务模式：{mode}",
				"请刷新页面后重试。",
			)
		)
		return checks

	ai_required = mode in {"full", "rescore"} or (mode == "collect" and bool(options and options.get("auto_score")))
	if ai_required:
		resume_path = config.get("profile", {}).get("resume_path", "")
		# Upload writes an absolute path under data/resumes/; check that file directly.
		if not resume_path or not Path(str(resume_path)).exists():
			checks.append(
				_check(
					"resume",
					"简历文件",
					"error",
					"尚未配置有效简历",
					"打开“配置 → 个人信息”，上传 .md、.docx 或 .pdf 简历。",
					"config",
				)
			)
		else:
			checks.append(_check("resume", "简历文件", "pass", "简历文件已就绪", Path(str(resume_path)).name))

	if mode in {"full", "collect"}:
		if options:
			platforms = options.get("platforms", {})
			keywords = [
				f"{platform}：{', '.join(value.get('keywords', []))}"
				for platform, value in platforms.items()
				if isinstance(value, dict) and value.get("keywords")
			]
		else:
			keywords = config.get("search", {}).get("keywords") or []
		if not keywords:
			checks.append(
				_check(
					"search_keywords",
					"搜索关键词",
					"error",
					"尚未填写搜索关键词",
					"打开“配置 → 搜索设置”，至少添加一个岗位关键词。",
					"config",
				)
			)
		else:
			checks.append(
				_check(
					"search_keywords",
					"搜索关键词",
					"pass",
					"搜索关键词已配置",
					"、".join(str(keyword) for keyword in keywords[:3]),
				)
			)
	return checks


def _check(
	check_id: str,
	title: str,
	status: str,
	message: str,
	detail: str,
	action: str = "",
) -> dict[str, str]:
	return {
		"id": check_id,
		"title": title,
		"status": status,
		"message": message,
		"detail": detail,
		"action": action,
	}
