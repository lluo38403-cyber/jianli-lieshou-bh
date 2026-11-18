"""Ollama 本地模型回归测试：用本地桩 Ollama HTTP 服务验证三处接入与回落。

覆盖：Ollama 评分 / 招呼语 / 改简历、连不上回落、模型缺失处理、预检只提示
不拦启动、本地规则提供方、配置校验。不依赖真实 Ollama 与模型。
"""

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from bosshunter.ai.credentials import (
    AIRequestError,
    call_ai_text,
    call_ollama_text,
    get_ollama_base_url,
    get_ollama_model,
    list_ai_models,
)
from bosshunter.ai.scorer import _score_job_with_ai
from bosshunter.db import get_db, insert_job, update_job_status
from bosshunter.web.preflight import check_ai_connection, collect_preflight_checks, error_messages

SCORE_JSON = json.dumps(
    {
        "role_summary": "内容运营岗位，负责内容策划与增长",
        "hard_requirements": {"evidence": "本科及以上学历，简历中有本科学历", "score": 15},
        "core_duties": {"evidence": "做过内容运营，直接相关", "score": 35},
        "transferable_evidence": {"evidence": "有用户增长方法与成果", "score": 20},
        "tools_industry": {"evidence": "熟悉内容平台", "score": 8},
        "practical_fit": {"evidence": "城市与薪资匹配", "score": 10},
        "caps": [],
        "hard_gaps": [],
        "reason": "核心职责与经历高度匹配",
        "missing": "",
    },
    ensure_ascii=False,
)

GREETING_TEXT = "您好，我做过 3 年内容运营，公众号阅读量从 1 万做到 5 万，看到贵公司在招内容运营，希望能进一步聊聊。"

BASE_RESUME = """# 张三
电话：13800000000 | 邮箱：zhangsan@example.com

## 基本信息
- 姓名：张三
- 电话：13800000000
- 邮箱：zhangsan@example.com

## 个人优势
- 3 年内容运营经验，熟悉公众号运营。

## 工作经历
### 示例公司 | 内容运营 | 2022 年 1 月 - 2024 年 1 月
- 负责公众号内容策划，阅读量从 1 万提升到 5 万。

## 项目经历
### 公众号增长项目
- 通过选题优化，3 个月粉丝增长 5000 人。

## 教育经历
### 示例大学 | 本科 | 2018 年 9 月 - 2022 年 6 月
- 新闻传播专业。

## 相关技能
- 内容策划、数据分析
"""

TAILORED_RESUME = """# 张三
电话：13800000000 | 邮箱：zhangsan@example.com

## 基本信息
- 姓名：张三
- 电话：13800000000
- 邮箱：zhangsan@example.com

## 个人优势
- 3 年内容运营经验，熟悉公众号运营；公众号阅读量从 1 万提升到 5 万。

## 项目经历
### 公众号增长项目
- 通过选题优化，3 个月粉丝增长 5000 人。

## 工作经历
### 示例公司 | 内容运营 | 2022 年 1 月 - 2024 年 1 月
- 负责公众号内容策划，阅读量从 1 万提升到 5 万。

## 教育经历
### 示例大学 | 本科 | 2018 年 9 月 - 2022 年 6 月
- 新闻传播专业。

## 相关技能
- 内容策划、数据分析、用户增长，熟悉公众号后台操作。
"""

SCORE_JOB = {
    "title": "内容运营",
    "company": "示例公司",
    "salary": "16-25K",
    "experience": "1-3年",
    "city": "北京",
    "education": "",
    "jd": "负责公众号内容运营，要求有内容策划经验。",
    "recruitment_type": "experienced",
}

CLOSED_PORT_URL = "http://127.0.0.1:9"


def _ollama_config(base_url, model="qwen2.5:7b"):
    return {
        "profile": {
            "target_roles": ["内容运营"],
            "target_cities": ["上海"],
            "education": "本科",
            "salary_min": 15,
            "salary_max": 25,
            "job_types": ["全职"],
            "posted_days": 7,
        },
        "search": {"keywords": ["内容运营"]},
        "ai": {
            "service": "ollama",
            "provider": "ollama",
            "ollama_base_url": base_url,
            "ollama_model": model,
        },
    }


class _StubOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            if self.server.stub_mode == "tags_missing_model":
                self._send_json(200, {"models": [{"name": "llama3.1:8b"}]})
            else:
                self._send_json(200, {"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.1:8b"}]})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {}
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": "not found"})
            return
        if self.server.stub_mode == "missing_model":
            self._send_json(404, {"error": "model 'qwen2.5:7b' not found"})
            return
        if self.server.stub_mode == "empty":
            self._send_json(200, {
                "model": payload.get("model", ""),
                "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
            })
            return
        self._send_json(200, {
            "model": payload.get("model", ""),
            "choices": [{"message": {"role": "assistant", "content": self.server.stub_text}, "finish_reason": "stop"}],
        })


class StubOllamaServer:
    """本地桩 Ollama：只回写死的响应，不调真实模型。"""

    def __init__(self, mode="ok", text=""):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _StubOllamaHandler)
        self._server.stub_mode = mode
        self._server.stub_text = text
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}"


class OllamaClientTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(get_ollama_base_url({}), "http://127.0.0.1:11434")
        self.assertEqual(get_ollama_model({}), "qwen2.5:7b")

    def test_call_ollama_text_returns_text(self):
        with StubOllamaServer(mode="ok", text=GREETING_TEXT) as stub:
            text = call_ollama_text(
                "写招呼语",
                {"ai": {"service": "ollama", "ollama_base_url": stub.url, "ollama_model": "qwen2.5:7b"}},
                100,
            )
        self.assertEqual(text, GREETING_TEXT)

    def test_call_ollama_text_missing_model(self):
        with StubOllamaServer(mode="missing_model") as stub:
            with self.assertRaises(AIRequestError) as ctx:
                call_ollama_text(
                    "hi",
                    {"ai": {"service": "ollama", "ollama_base_url": stub.url, "ollama_model": "qwen2.5:7b"}},
                    100,
                )
        self.assertEqual(ctx.exception.kind, "model_not_found")
        self.assertIn("ollama pull qwen2.5:7b", str(ctx.exception))

    def test_call_ollama_text_unreachable(self):
        with self.assertRaises(AIRequestError) as ctx:
            call_ollama_text(
                "hi",
                {"ai": {"service": "ollama", "ollama_base_url": CLOSED_PORT_URL}},
                100,
            )
        self.assertEqual(ctx.exception.kind, "network")

    def test_call_ai_text_local_returns_none_without_network(self):
        self.assertIsNone(call_ai_text("hi", {"ai": {"service": "local"}}, 100))

    def test_list_ollama_models(self):
        with StubOllamaServer(mode="ok") as stub:
            models = list_ai_models({"ai": {"service": "ollama", "ollama_base_url": stub.url}})
        self.assertEqual(models, ["llama3.1:8b", "qwen2.5:7b"])


class OllamaScoringTests(unittest.TestCase):
    def test_scorer_uses_ollama(self):
        with StubOllamaServer(mode="ok", text=SCORE_JSON) as stub:
            outcome = _score_job_with_ai(dict(SCORE_JOB), "简历摘要", _ollama_config(stub.url), max_attempts=1)
        self.assertIsNotNone(outcome.result)
        self.assertTrue(outcome.result.structured)
        self.assertEqual(outcome.result.score, 88)
        self.assertEqual(outcome.pause_reason, "")

    def test_scorer_falls_back_to_rule_score_when_ollama_down(self):
        # 标题命中 +10、薪资符合 +10、基准 40 = 60，正好过线。
        outcome = _score_job_with_ai(dict(SCORE_JOB), "简历摘要", _ollama_config(CLOSED_PORT_URL), max_attempts=1)
        self.assertIsNotNone(outcome.result)
        self.assertFalse(outcome.result.structured)
        self.assertEqual(outcome.result.score, 60)
        self.assertIn("Ollama 不可用", outcome.result.reason)
        self.assertEqual(outcome.pause_reason, "")

    def test_scorer_falls_back_when_ollama_returns_empty(self):
        with StubOllamaServer(mode="empty") as stub:
            outcome = _score_job_with_ai(dict(SCORE_JOB), "简历摘要", _ollama_config(stub.url), max_attempts=1)
        self.assertIsNotNone(outcome.result)
        self.assertEqual(outcome.result.score, 60)
        self.assertEqual(outcome.pause_reason, "")


class OllamaGreetingTests(unittest.TestCase):
    def _seed(self, db, job_id):
        insert_job(db, {
            "id": job_id, "title": "内容运营", "company": "示例公司",
            "salary": "16-25K", "jd": "负责公众号内容运营",
        })
        update_job_status(db, job_id, "approved")

    def _greeting_config(self, base_url, service="ollama", resume_path=""):
        from bosshunter.web.greeting_activity import GreetingActivityRegistry

        return {
            "profile": {
                "resume_path": resume_path,
                "ai_greeting_enabled": True,
                "fixed_greeting": "您好，我对这个岗位很感兴趣。",
            },
            "ai": {
                "service": service,
                "provider": service,
                "ollama_base_url": base_url,
                "ollama_model": "qwen2.5:7b",
            },
            "_workbench_greeting_activity": GreetingActivityRegistry().claim,
        }

    def test_greeter_uses_ollama(self):
        from bosshunter.ai import greeter

        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text(BASE_RESUME, encoding="utf-8")
            db_path = Path(tmp) / "jobs.db"
            db = get_db(db_path)
            try:
                self._seed(db, "job-1")
            finally:
                db.close()
            with StubOllamaServer(mode="ok", text=GREETING_TEXT) as stub:
                count = greeter.generate_greetings(self._greeting_config(stub.url, resume_path=str(resume)), ["job-1"], db_path)
            self.assertEqual(count, 1)
            verify = get_db(db_path)
            try:
                greeting = verify.execute("SELECT greeting FROM jobs WHERE id = 'job-1'").fetchone()[0]
            finally:
                verify.close()
            self.assertEqual(greeting, GREETING_TEXT)

    def test_greeter_falls_back_to_fixed_greeting_when_ollama_down(self):
        from bosshunter.ai import greeter

        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text(BASE_RESUME, encoding="utf-8")
            db_path = Path(tmp) / "jobs.db"
            db = get_db(db_path)
            try:
                self._seed(db, "job-1")
            finally:
                db.close()
            count = greeter.generate_greetings(
                self._greeting_config(CLOSED_PORT_URL, resume_path=str(resume)), ["job-1"], db_path
            )
            self.assertEqual(count, 1)
            verify = get_db(db_path)
            try:
                greeting = verify.execute("SELECT greeting FROM jobs WHERE id = 'job-1'").fetchone()[0]
                report = verify.execute("SELECT COUNT(*) FROM history WHERE job_id = 'job-1' AND action = 'greeting_failed'").fetchone()[0]
            finally:
                verify.close()
            self.assertEqual(greeting, "您好，我对这个岗位很感兴趣。")
            self.assertEqual(report, 0)

    def test_local_service_uses_fixed_greeting_without_ai(self):
        from bosshunter.ai import greeter

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jobs.db"
            db = get_db(db_path)
            try:
                self._seed(db, "job-1")
            finally:
                db.close()
            config = self._greeting_config(CLOSED_PORT_URL, service="local")
            with patch.object(greeter, "_call_claude", side_effect=AssertionError("本地规则不应调用 AI")):
                count = greeter.generate_greetings(config, ["job-1"], db_path)
            self.assertEqual(count, 1)
            verify = get_db(db_path)
            try:
                greeting = verify.execute("SELECT greeting FROM jobs WHERE id = 'job-1'").fetchone()[0]
            finally:
                verify.close()
            self.assertEqual(greeting, "您好，我对这个岗位很感兴趣。")


class OllamaResumeTests(unittest.TestCase):
    def _setup(self, tmp):
        from bosshunter.ai.resume import RESUME_COMPLETION_MARKER

        resume_path = Path(tmp) / "resume.md"
        resume_path.write_text(BASE_RESUME, encoding="utf-8")
        db_path = Path(tmp) / "jobs.db"
        db = get_db(db_path)
        try:
            insert_job(db, {
                "id": "job-1", "title": "内容运营", "company": "示例公司",
                "salary": "16-25K", "education": "本科",
                "recruitment_type": "experienced",
                "jd": "负责公众号内容运营，要求有内容策划经验。",
            })
        finally:
            db.close()
        return resume_path, db_path, RESUME_COMPLETION_MARKER

    def test_tailor_resume_via_ollama(self):
        from bosshunter.ai.resume import generate_tailored_resume

        with tempfile.TemporaryDirectory() as tmp:
            resume_path, db_path, marker = self._setup(tmp)
            tailored = TAILORED_RESUME + "\n" + marker + "\n"
            config = {
                "profile": {"resume_path": str(resume_path), "resume_output_dir": str(Path(tmp) / "resumes")},
                "ai": {"service": "ollama", "provider": "ollama", "ollama_base_url": "http://127.0.0.1:9", "ollama_model": "qwen2.5:7b"},
            }
            with StubOllamaServer(mode="ok", text=tailored) as stub:
                config["ai"]["ollama_base_url"] = stub.url
                with patch("bosshunter.ai.resume.get_db", side_effect=lambda: get_db(db_path)), \
                     patch("bosshunter.ai.resume.new_tab", return_value=None):
                    result = generate_tailored_resume("job-1", config)
            self.assertIsNotNone(result)
            self.assertTrue(str(result).endswith(".md"))
            self.assertTrue(Path(result).exists())
            self.assertIn("公众号增长项目", Path(result).read_text(encoding="utf-8"))

    def test_tailor_resume_returns_none_when_ollama_down(self):
        from bosshunter.ai.resume import generate_tailored_resume

        with tempfile.TemporaryDirectory() as tmp:
            resume_path, db_path, _ = self._setup(tmp)
            config = {
                "profile": {"resume_path": str(resume_path), "resume_output_dir": str(Path(tmp) / "resumes")},
                "ai": {"service": "ollama", "provider": "ollama", "ollama_base_url": CLOSED_PORT_URL, "ollama_model": "qwen2.5:7b"},
            }
            with patch("bosshunter.ai.resume.get_db", side_effect=lambda: get_db(db_path)):
                result = generate_tailored_resume("job-1", config)
            self.assertIsNone(result)


class OllamaPreflightTests(unittest.TestCase):
    def _ollama_config(self, base_url, model="qwen2.5:7b"):
        return {"ai": {"service": "ollama", "provider": "ollama", "ollama_base_url": base_url, "ollama_model": model}}

    def test_ollama_ready_passes(self):
        with StubOllamaServer(mode="ok") as stub:
            checks = check_ai_connection(self._ollama_config(stub.url), required=True)
        self.assertEqual(checks[0]["status"], "pass")
        self.assertIn("就绪", checks[0]["message"])
        self.assertEqual(error_messages(checks), [])

    def test_ollama_model_missing_is_warning(self):
        with StubOllamaServer(mode="tags_missing_model") as stub:
            checks = check_ai_connection(self._ollama_config(stub.url), required=True)
        self.assertEqual(checks[0]["status"], "warning")
        self.assertIn("还没拉取", checks[0]["message"])
        self.assertEqual(error_messages(checks), [])

    def test_ollama_down_is_warning_not_error(self):
        checks = check_ai_connection(self._ollama_config(CLOSED_PORT_URL), required=True)
        self.assertEqual(checks[0]["status"], "warning")
        self.assertIn("未连接", checks[0]["message"])
        self.assertEqual(error_messages(checks), [])

    def test_local_service_passes(self):
        checks = check_ai_connection({"ai": {"service": "local", "provider": "local"}}, required=True)
        self.assertEqual(checks[0]["status"], "pass")
        self.assertEqual(error_messages(checks), [])

    def test_collect_preflight_ollama_down_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text("# 简历\nPython 后端。", encoding="utf-8")
            config = {
                "ai": {"service": "ollama", "provider": "ollama", "ollama_base_url": CLOSED_PORT_URL, "ollama_model": "qwen2.5:7b"},
                "profile": {"resume_path": str(resume)},
                "search": {},
                "collection": {},
            }
            passing_browser = [{"id": "browser_runtime", "title": "浏览器连接", "status": "pass",
                                "message": "已连接", "detail": "", "action": ""}]
            with patch("bosshunter.web.preflight.check_browser_connection", return_value=passing_browser):
                checks = collect_preflight_checks("rescore", config)
            by_id = {check["id"]: check for check in checks}
            self.assertEqual(by_id["ai_connection"]["status"], "warning")
            self.assertFalse([c for c in checks if c["status"] == "error"])


class OllamaConfigTests(unittest.TestCase):
    def test_defaults_include_ollama(self):
        from bosshunter.config import DEFAULTS, load_config

        self.assertEqual(DEFAULTS["ai"]["ollama_base_url"], "http://127.0.0.1:11434")
        self.assertEqual(DEFAULTS["ai"]["ollama_model"], "qwen2.5:7b")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(Path(tmp) / "config.yaml")
        # 默认行为不变：仍是 Claude。
        self.assertEqual(cfg["ai"]["service"], "anthropic")

    def test_validate_accepts_ollama_and_local(self):
        from bosshunter.config import _validate_ai_provider, load_config

        with tempfile.TemporaryDirectory() as tmp:
            for service in ("ollama", "local"):
                cfg = load_config(Path(tmp) / "config.yaml")
                cfg["ai"]["service"] = service
                cfg["ai"]["provider"] = service
                _validate_ai_provider(cfg)


class OllamaMonitorFallbackTests(unittest.TestCase):
    def test_fallback_resume_path(self):
        from bosshunter.executor.monitor import _fallback_resume_path

        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text(BASE_RESUME, encoding="utf-8")
            ollama_config = {"profile": {"resume_path": str(resume)}, "ai": {"service": "ollama"}}
            self.assertEqual(_fallback_resume_path(ollama_config), resume)
            deepseek_config = {"profile": {"resume_path": str(resume)}, "ai": {"service": "deepseek"}}
            self.assertIsNone(_fallback_resume_path(deepseek_config))
            missing_config = {"profile": {"resume_path": str(Path(tmp) / "nope.md")}, "ai": {"service": "ollama"}}
            self.assertIsNone(_fallback_resume_path(missing_config))

    def test_ollama_tailor_failure_falls_back_to_original_resume(self):
        import json as _json

        from bosshunter.db import get_jobs_needing_resume
        from bosshunter.executor import monitor

        job = {
            "id": "job-ollama-resume", "title": "AI运营", "company": "Example",
            "salary": "20-30K", "city": "北京", "experience": "1-3年",
            "jd": "负责AI内容运营", "hr_name": "HR", "hr_title": "招聘者",
            "hr_active": "", "company_size": "", "company_industry": "",
            "url": "https://example.com/job",
        }
        messages = [
            {"sender": "me", "text": "您好，我对岗位很感兴趣。"},
            {"sender": "hr", "text": "方便发一份你的简历过来吗？"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            resume = Path(tmp) / "resume.md"
            resume.write_text(BASE_RESUME, encoding="utf-8")
            db_path = Path(tmp) / "data" / "bosshunter.db"
            db = get_db(db_path)
            try:
                insert_job(db, job)
                update_job_status(db, "job-ollama-resume", "sent")
            finally:
                db.close()

            def open_db():
                return get_db(db_path)

            with patch.object(monitor, "get_db", side_effect=open_db), \
                 patch.object(monitor, "_open_conversation", return_value="target-1"), \
                 patch.object(monitor, "evaluate", return_value=_json.dumps(messages, ensure_ascii=False)), \
                 patch.object(monitor, "close_tab"), \
                 patch.object(monitor, "_send_message_in_chat", return_value=True), \
                 patch.object(monitor.time, "sleep"), \
                 patch("bosshunter.ai.resume.generate_tailored_resume", return_value=None), \
                 patch("bosshunter.ai.resume.get_last_resume_failure_reason", return_value="Ollama 未连接"):
                action = monitor._handle_conversation(
                    dict(job, status="sent"),
                    {
                        "profile": {"resume_path": str(resume), "portfolio_url": "https://example.com/resume"},
                        "ai": {"service": "ollama", "provider": "ollama", "resume_tailor_enabled": True},
                    },
                )

            verify_db = get_db(db_path)
            try:
                row = verify_db.execute(
                    "SELECT status, resume_path FROM jobs WHERE id = ?", ("job-ollama-resume",)
                ).fetchone()
                actions = [
                    item["action"]
                    for item in verify_db.execute(
                        "SELECT action FROM history WHERE job_id = ? ORDER BY id", ("job-ollama-resume",)
                    ).fetchall()
                ]
                detail = verify_db.execute(
                    "SELECT detail FROM history WHERE job_id = ? AND action = 'needs_resume'",
                    ("job-ollama-resume",),
                ).fetchone()["detail"]
                needing_resume = get_jobs_needing_resume(verify_db)
            finally:
                verify_db.close()

        self.assertEqual(action, "needs_resume")
        self.assertEqual(row["status"], "needs_resume")
        self.assertEqual(actions, ["needs_resume"])
        self.assertIn("已回落", detail)
        self.assertIn(str(resume), detail)
        self.assertEqual(len(needing_resume), 1)


if __name__ == "__main__":
    unittest.main()
