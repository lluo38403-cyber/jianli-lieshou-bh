import unittest
from unittest.mock import Mock, patch

import httpx

from bosshunter.web.preflight import (
	check_ai_connection,
	check_browser_connection,
	collect_preflight_checks,
	is_boss_login_url,
	resolve_check_platforms,
)


class AiPreflightTests(unittest.TestCase):
	def test_missing_api_key_returns_actionable_error(self):
		checks = check_ai_connection({"ai": {"model": "claude-sonnet-4-6"}}, required=True)

		self.assertEqual(checks[0]["id"], "ai_credentials")
		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("AI 服务未配置", checks[0]["message"])
		self.assertIn("AI 服务未配置", checks[0]["detail"])

	@patch("bosshunter.web.preflight.httpx.get")
	def test_rejected_api_key_is_reported_without_exposing_key(self, http_get):
		http_get.return_value = Mock(status_code=401)
		config = {"ai": {"api_key": "secret-key", "model": "claude-sonnet-4-6"}}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("AI 服务鉴权失败", checks[0]["message"])
		self.assertNotIn("secret-key", str(checks))

	@patch("bosshunter.web.preflight.httpx.get")
	def test_valid_api_connection_returns_pass(self, http_get):
		http_get.return_value = Mock(status_code=200)
		config = {"ai": {"api_key": "secret-key", "model": "claude-sonnet-4-6"}}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "pass")
		self.assertIn("连接正常", checks[0]["message"])
		self.assertIn("仅检测模型列表接口", checks[0]["detail"])
		self.assertIn("未发送生成请求", checks[0]["detail"])
		http_get.assert_called_once()
		self.assertFalse(http_get.call_args.kwargs["trust_env"])

	def test_openai_compatible_provider_requires_base_url(self):
		config = {
			"ai": {
				"provider": "openai_compatible",
				"api_key": "secret-key",
				"model": "deepseek-chat",
			}
		}

		with patch.dict("os.environ", {}, clear=True):
			checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["id"], "ai_base_url")
		self.assertEqual(checks[0]["status"], "error")

	@patch("bosshunter.web.preflight.httpx.get")
	def test_ai_client_initialization_error_is_reported_as_runtime_failure(self, http_get):
		"""客户端初始化失败发生在请求前，应提示本地环境且不得泄露配置。"""
		api_key = "secret-key-that-must-not-leak"
		base_url = "https://private-api.example/v1"
		http_get.side_effect = TypeError(f"failed with {api_key} at {base_url}")
		config = {
			"ai": {
				"provider": "openai_compatible",
				"service": "custom",
				"base_url": base_url,
				"api_key": api_key,
				"model": "private-model",
			}
		}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["id"], "ai_runtime")
		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("运行环境", checks[0]["message"])
		self.assertIn("Python", checks[0]["detail"])
		self.assertIn("HTTPX", checks[0]["detail"])
		self.assertIn("简历猎手", checks[0]["detail"])
		self.assertNotIn(api_key, str(checks))
		self.assertNotIn(base_url, str(checks))

	@patch("bosshunter.web.preflight.sys.version", "3.13.0a5 (test build)")
	@patch("bosshunter.web.preflight.sys.version_info", Mock(releaselevel="alpha"))
	@patch("bosshunter.web.preflight.httpx.get")
	def test_prerelease_python_adds_runtime_warning(self, http_get):
		"""预发布 Python 可能存在依赖兼容风险，连接成功时也应明确警告。"""
		http_get.return_value = Mock(status_code=200)
		config = {"ai": {"api_key": "secret-key", "model": "claude-sonnet-4-6"}}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "pass")
		warning = next(check for check in checks if check["id"] == "python_runtime")
		self.assertEqual(warning["status"], "warning")
		self.assertIn("预发布", warning["message"])
		self.assertIn("3.13.0a5", warning["detail"])
		self.assertNotIn("secret-key", str(checks))

	@patch("bosshunter.web.preflight.httpx.get")
	def test_ai_timeout_has_specific_feedback(self, http_get):
		http_get.side_effect = httpx.ReadTimeout("timed out")
		config = {"ai": {"api_key": "secret-key", "model": "claude-sonnet-4-6"}}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("连接超时", checks[0]["message"])

	@patch("bosshunter.web.preflight.httpx.get")
	def test_invalid_ai_base_url_has_specific_feedback(self, http_get):
		http_get.side_effect = httpx.InvalidURL("Invalid port: ':1'")
		config = {
			"ai": {
				"provider": "openai_compatible",
				"service": "custom",
				"base_url": "http://::1:8000",
				"api_key": "secret-key",
				"model": "local-model",
			}
		}

		checks = check_ai_connection(config, required=True)

		self.assertEqual(checks[0]["status"], "error")
		self.assertIn("地址无效", checks[0]["message"])
		self.assertIn("[::1]", checks[0]["detail"])


class BrowserPreflightTests(unittest.TestCase):
	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_unselected_platform_tabs_are_not_reported(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": {"targetId": "1", "url": "https://www.zhipin.com/web/geek/job"},
			"zhilian_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["boss"]})

		self.assertFalse(any(check["id"].startswith("zhilian") for check in checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_zhilian_only_collection_does_not_report_missing_boss_tab(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"zhilian_tab": {"targetId": "2"},
			"zhilian_page": {"status": "ready"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["zhilian"]})

		self.assertFalse(any(check["id"].startswith("boss") for check in checks))
		self.assertEqual(next(check for check in checks if check["id"] == "zhilian_tab")["status"], "pass")

	def test_resolve_check_platforms_prefers_enabled_platforms_over_boss(self):
		# v37②a：没给采集参数时不再默认只查 BOSS——查配置里启用的平台。
		config = {"platforms": {"boss": {"enabled": False}, "zhilian": {"enabled": True}}}
		self.assertEqual(resolve_check_platforms(config, None), ["zhilian"])
		# 本次任务明确选的平台优先于配置。
		self.assertEqual(
			resolve_check_platforms(config, {"platform_order": ["51job"]}),
			["51job"],
		)
		# 配置没写任何启用信息时，退到实际已登录的平台。
		self.assertEqual(
			resolve_check_platforms({}, None, {"liepin": {"status": "logged_in"}}),
			["liepin"],
		)
		# 全都没有才回落到 BOSS 兜底。
		self.assertEqual(resolve_check_platforms({}, None), ["boss"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_no_options_browser_check_uses_enabled_platforms_not_boss(self, diagnostics):
		# v37②a：“检查一下能不能跑”/只看回复这类不带采集参数的预检，
		# 以前只查 BOSS（智联已登录也看不见），现在查启用的平台。
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"zhilian_tab": {"targetId": "2"},
			"zhilian_page": {"status": "ready"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}
		config = {"platforms": {"boss": {"enabled": False}, "zhilian": {"enabled": True}}}

		checks = check_browser_connection(config, None)

		self.assertFalse(any(check["id"].startswith("boss") for check in checks))
		self.assertEqual(next(check for check in checks if check["id"] == "zhilian_tab")["status"], "pass")

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_full_preflight_without_options_checks_configured_platforms(self, diagnostics):
		# v37②a：全流程预检不带参数时，浏览器检查也要跟 full_options
		# 的平台顺序走，不能只查 BOSS。
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"zhilian_tab": {"targetId": "2"},
			"zhilian_page": {"status": "ready"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}
		config = {
			"search": {"keywords": ["人力"]},
			"collection": {"default_order": ["boss", "zhilian", "51job", "liepin"]},
			"platforms": {
				"boss": {"enabled": False, "search": {"keywords": ["人力"], "cities": ["北京"]}},
				"zhilian": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["北京"]}},
				"51job": {"enabled": False},
				"liepin": {"enabled": False},
			},
			"profile": {"target_cities": ["北京"]},
		}

		checks = collect_preflight_checks("full", config)

		self.assertFalse(any(check["id"].startswith("boss") for check in checks))
		platform_check = next(check for check in checks if check["id"] == "full_flow_platform")
		self.assertNotIn("boss", platform_check["detail"])
		self.assertIn("zhilian", platform_check["detail"])
		self.assertEqual(next(check for check in checks if check["id"] == "zhilian_tab")["status"], "pass")

	@patch("bosshunter.web.preflight.check_ai_connection")
	@patch("bosshunter.web.preflight.check_browser_connection")
	def test_full_flow_uses_only_explicit_full_flow_platform_order(self, browser_check, ai_check):
		ai_check.return_value = []
		browser_check.return_value = []
		config = {
			"search": {"keywords": ["人力"]},
			"platforms": {
				"boss": {"enabled": True},
				"zhilian": {"enabled": True},
			},
		}

		checks = collect_preflight_checks("full", config)

		platform_check = next(check for check in checks if check["id"] == "full_flow_platform")
		self.assertEqual(platform_check["status"], "pass")
		self.assertIn("执行顺序：boss", platform_check["detail"])
		self.assertIn("只有支持投递的平台", platform_check["detail"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_implicit_platforms_missing_tabs_are_warnings_not_errors(self, diagnostics):
		# v37②a（补）：默认/启用带进来的平台缺页只警告，不拦预检。
		# 全新/默认配置下四个平台默认全开，浏览器连上但一个标签页
		# 都没开时，full 预检必须通过（旧契约：只有 BOSS 警告）。
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}
		import tempfile
		from pathlib import Path

		with tempfile.TemporaryDirectory() as tmp:
			resume_path = Path(tmp) / "resume.md"
			resume_path.write_text("# Resume", encoding="utf-8")
			config = {
				"search": {"keywords": ["人力"]},
				"collection": {"default_order": ["boss", "zhilian", "51job", "liepin"]},
				"platforms": {
					"boss": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["北京"]}},
					"zhilian": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["北京"]}},
					"51job": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["北京"]}},
					"liepin": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["北京"]}},
				},
				"profile": {"resume_path": str(resume_path), "target_cities": ["北京"]},
			}

			checks = collect_preflight_checks("full", config)

		self.assertFalse(any(check["status"] == "error" for check in checks))
		for platform in ("zhilian", "51job", "liepin"):
			tab = next(check for check in checks if check["id"] == f"{platform}_tab")
			self.assertEqual(tab["status"], "warning")

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_explicitly_selected_platform_missing_tab_still_errors(self, diagnostics):
		# v37②a（补）：反向钉住——用户这次明确选了智联，缺页仍报错。
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}
		config = {
			"search": {"keywords": ["人力"]},
			"platforms": {
				"boss": {"enabled": False},
				"zhilian": {"enabled": True, "search": {"keywords": ["人力"], "cities": ["北京"]}},
			},
			"profile": {"target_cities": ["北京"]},
		}

		checks = collect_preflight_checks(
			"collect", config, {"platform_order": ["zhilian"]}
		)

		tab = next(check for check in checks if check["id"] == "zhilian_tab")
		self.assertEqual(tab["status"], "error")
		self.assertFalse(any(check["id"].startswith("boss") for check in checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_running_runtime_is_reused_when_node_is_not_on_path(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": False, "version": None},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": {"targetId": "1"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		runtime_check = next(check for check in checks if check["id"] == "browser_runtime")
		self.assertEqual(runtime_check["status"], "pass")
		self.assertNotIn("Node.js", str(checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_missing_remote_debugging_is_reported(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": False,
			"errors": ["Chrome is not connected to Browser Runtime."],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		chrome_check = next(check for check in checks if check["id"] == "chrome_connection")
		self.assertEqual(chrome_check["status"], "error")
		self.assertIn("去登录", chrome_check["detail"])
		self.assertNotIn("一键启动浏览器", chrome_check["detail"])
		self.assertNotIn("chrome://inspect", chrome_check["detail"])
		self.assertNotIn("bosshunter connect", chrome_check["detail"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_edge_browser_passes_like_chrome(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Microsoft Edge",
			"browser_product": "Edg/138.0",
			"boss_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		chrome_check = next(check for check in checks if check["id"] == "chrome_connection")
		self.assertEqual(chrome_check["status"], "pass")
		self.assertIn("Microsoft Edge", chrome_check["message"])
		self.assertFalse(any(check["id"] == "chrome_product" for check in checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_edge_product_without_browser_name_passes(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_product": "Edg/138.0",
			"boss_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		chrome_check = next(check for check in checks if check["id"] == "chrome_connection")
		self.assertEqual(chrome_check["status"], "pass")

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_51job_login_required_is_reported(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"job51_tab": {"targetId": "3", "url": "https://we.51job.com/pc/search"},
			"job51_page": {"status": "login_required", "message": "前程无忧页面要求登录"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["51job"]})

		login_check = next(check for check in checks if check["id"] == "51job_login")
		self.assertEqual(login_check["status"], "error")
		self.assertIn("要求登录", login_check["message"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_51job_ready_page_passes(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"job51_tab": {"targetId": "3", "url": "https://we.51job.com/pc/search"},
			"job51_page": {"status": "ready", "message": "已发现前程无忧搜索框，未发现登录墙"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["51job"]})

		tab_check = next(check for check in checks if check["id"] == "51job_tab")
		self.assertEqual(tab_check["status"], "pass")

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_liepin_missing_tab_is_reported(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"liepin_tab": None,
			"liepin_page": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["liepin"]})

		tab_check = next(check for check in checks if check["id"] == "liepin_tab")
		self.assertEqual(tab_check["status"], "error")
		self.assertIn("未发现已打开的猎聘页面", tab_check["message"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_liepin_login_required_is_reported(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"liepin_tab": {"targetId": "4", "url": "https://www.liepin.com/zhaopin/"},
			"liepin_page": {"status": "login_required", "message": "猎聘页面要求登录"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["liepin"]})

		login_check = next(check for check in checks if check["id"] == "liepin_login")
		self.assertEqual(login_check["status"], "error")
		self.assertIn("要求登录", login_check["message"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_chromium_name_is_rejected_even_when_product_looks_like_chrome(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Chromium",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		product_check = next(check for check in checks if check["id"] == "chrome_product")
		self.assertEqual(product_check["status"], "error")
		self.assertIn("Chromium", product_check["message"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_boss_personal_center_url_is_not_a_login_warning(self, diagnostics):
		# A1：个人中心页 https://www.zhipin.com/web/user/ 是已登录，旧的
		# "/user/" 宽松子串会误判掉线。
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": {"targetId": "1", "url": "https://www.zhipin.com/web/user/"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		tab_check = next(check for check in checks if check["id"] == "boss_tab")
		self.assertEqual(tab_check["status"], "pass")
		self.assertFalse(any(check["id"] == "boss_login" for check in checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_boss_header_login_url_is_a_login_warning(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": {"targetId": "1", "url": "https://www.zhipin.com/web/user/?ka=header-login"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({})

		login_check = next(check for check in checks if check["id"] == "boss_login")
		self.assertEqual(login_check["status"], "warning")

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_logged_in_zhilian_missing_tab_is_warning_not_error(self, diagnostics):
		# A2：已登录却没打开页面，开始任务时会自动打开，不拦。
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"zhilian_tab": None,
			"zhilian_page": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["zhilian"]}, login_states={"zhilian": "logged_in"})

		tab_check = next(check for check in checks if check["id"] == "zhilian_tab")
		self.assertEqual(tab_check["status"], "warning")
		self.assertIn("会自动打开", tab_check["message"])
		self.assertFalse(any(check["status"] == "error" for check in checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_logged_out_zhilian_missing_tab_still_errors(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"zhilian_tab": None,
			"zhilian_page": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["zhilian"]}, login_states={"zhilian": "logged_out"})

		tab_check = next(check for check in checks if check["id"] == "zhilian_tab")
		self.assertEqual(tab_check["status"], "error")

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_logged_in_51job_missing_tab_is_warning(self, diagnostics):
		# login_states 也接受 DB 行那样的 dict（{"status": ...}）。
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"job51_tab": None,
			"job51_page": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection(
			{}, {"platform_order": ["51job"]}, login_states={"51job": {"status": "logged_in"}}
		)

		tab_check = next(check for check in checks if check["id"] == "51job_tab")
		self.assertEqual(tab_check["status"], "warning")
		self.assertIn("会自动打开", tab_check["message"])
		self.assertFalse(any(check["status"] == "error" for check in checks))

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_logged_in_liepin_missing_tab_is_warning(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"liepin_tab": None,
			"liepin_page": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["liepin"]}, login_states={"liepin": "logged_in"})

		tab_check = next(check for check in checks if check["id"] == "liepin_tab")
		self.assertEqual(tab_check["status"], "warning")
		self.assertIn("会自动打开", tab_check["message"])

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_logged_in_boss_missing_tab_mentions_auto_open(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, login_states={"boss": "logged_in"})

		tab_check = next(check for check in checks if check["id"] == "boss_tab")
		self.assertEqual(tab_check["status"], "warning")
		self.assertIn("会自动打开", tab_check["message"])

	@patch("bosshunter.web.preflight.check_browser_connection")
	def test_collect_preflight_checks_passes_login_states_through(self, browser_check):
		# A2：collect_preflight_checks 的 login_states 要透传给
		# check_browser_connection，server.py 接线后才起作用。
		browser_check.return_value = []

		collect_preflight_checks("monitor", {}, login_states={"boss": "logged_in"})

		browser_check.assert_called_once()
		self.assertEqual(browser_check.call_args.kwargs["login_states"], {"boss": "logged_in"})

	@patch("bosshunter.web.preflight.run_browser_diagnostics")
	def test_selected_zhilian_requires_real_search_page_state(self, diagnostics):
		diagnostics.return_value = {
			"node": {"available": True, "version": "v22"},
			"runtime": True,
			"chrome": True,
			"browser_name": "Google Chrome",
			"browser_product": "Chrome/138.0",
			"boss_tab": None,
			"zhilian_tab": {"targetId": "2"},
			"zhilian_page": {"status": "login_required", "message": "智联页面要求登录"},
			"errors": [],
			"runtime_url": "http://127.0.0.1:3456",
		}

		checks = check_browser_connection({}, {"platform_order": ["zhilian"]})

		login_check = next(check for check in checks if check["id"] == "zhilian_login")
		self.assertEqual(login_check["status"], "error")
		self.assertIn("要求登录", login_check["message"])


class IsBossLoginUrlTests(unittest.TestCase):
	"""A1：BOSS 登录页精确判定，与 boss.py 的正则同一口径。"""

	def test_login_page_urls_are_login(self):
		self.assertTrue(is_boss_login_url("https://www.zhipin.com/web/user/login"))
		self.assertTrue(is_boss_login_url("https://www.zhipin.com/web/user/login?ka=header-login"))
		self.assertTrue(is_boss_login_url("https://www.zhipin.com/web/user/?ka=header-login"))

	def test_personal_center_url_is_not_login(self):
		self.assertFalse(is_boss_login_url("https://www.zhipin.com/web/user/"))
		self.assertFalse(is_boss_login_url("https://www.zhipin.com/web/user"))
		self.assertFalse(is_boss_login_url("https://www.zhipin.com/web/geek/job"))

	def test_other_platform_urls_are_not_affected(self):
		self.assertFalse(is_boss_login_url("https://www.zhaopin.com/login"))
		self.assertFalse(is_boss_login_url("https://www.zhipin.com/web/user/verify"))
		self.assertFalse(is_boss_login_url(None))
		self.assertFalse(is_boss_login_url(""))


if __name__ == "__main__":
	unittest.main()
