"""LLM 客户端、FakeLLM、调用日志与脱敏、配置读取。全部不联网。"""

import email.message
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

import helpers
from firmforge.config import LLMSettings, ModelTier, Settings, SettingsError, load_dotenv, load_settings
from firmforge.llm import CallLogger, FakeLLM, LLMError, LoggedLLM, OpenAICompatClient, redact_headers

KEY = "sk-test-0123456789abcdef"
OK_BODY = {"model": "deepseek-x", "choices": [{"message": {"content": "你好"}}],
           "usage": {"prompt_tokens": 12, "completion_tokens": 3}}


def http_error(code, retry_after=None):
    hdrs = email.message.Message()
    if retry_after:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://x/chat/completions", code, "err", hdrs, io.BytesIO(b'{"error":"x"}'))


class ScriptedOpener:
    """按顺序返回预设结果；元素是 dict（200 响应体）或异常。"""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append(req)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return 200, json.dumps(step).encode("utf-8"), {}


def client(opener, **kw):
    sleeps = []
    c = OpenAICompatClient(KEY, "https://api.example.com/", opener=opener, sleep=sleeps.append, **kw)
    return c, sleeps


class ClientTest(unittest.TestCase):
    def test_complete_parses_response(self):
        op = ScriptedOpener(OK_BODY)
        c, _ = client(op)
        r = c.complete([{"role": "user", "content": "hi"}], model="m")
        self.assertEqual((r.text, r.prompt_tokens, r.completion_tokens, r.model), ("你好", 12, 3, "deepseek-x"))
        req = op.requests[0]
        self.assertEqual(req.full_url, "https://api.example.com/chat/completions")
        self.assertEqual(req.get_header("Authorization"), f"Bearer {KEY}")
        body = json.loads(req.data)
        self.assertEqual(body["model"], "m")

    def test_retries_on_429_then_succeeds(self):
        op = ScriptedOpener(http_error(429, retry_after="3"), http_error(503), OK_BODY)
        c, sleeps = client(op, max_retries=3)
        self.assertEqual(c.complete([], model="m").text, "你好")
        self.assertEqual(len(op.requests), 3)
        self.assertEqual(sleeps[0], 3.0)     # 尊重 Retry-After
        self.assertEqual(sleeps[1], 2.0)     # 指数退避

    def test_retries_on_network_error(self):
        op = ScriptedOpener(urllib.error.URLError("timed out"), TimeoutError(), OK_BODY)
        c, sleeps = client(op, max_retries=3)
        self.assertEqual(c.complete([], model="m").text, "你好")
        self.assertEqual(len(sleeps), 2)

    def test_gives_up_after_max_retries(self):
        op = ScriptedOpener(*[http_error(500)] * 3)
        c, _ = client(op, max_retries=2)
        with self.assertRaises(LLMError) as cm:
            c.complete([], model="m")
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual(len(op.requests), 3)

    def test_no_retry_on_400(self):
        op = ScriptedOpener(http_error(401), OK_BODY)
        c, sleeps = client(op)
        with self.assertRaises(LLMError):
            c.complete([], model="m")
        self.assertEqual(len(op.requests), 1)
        self.assertEqual(sleeps, [])

    def test_list_models(self):
        op = ScriptedOpener({"data": [{"id": "b-model"}, {"id": "a-model"}]})
        c, _ = client(op)
        self.assertEqual(c.list_models(), ["a-model", "b-model"])
        self.assertEqual(op.requests[0].get_method(), "GET")

    def test_missing_key_message(self):
        s = Settings(llm=LLMSettings())
        with self.assertRaises(SettingsError) as cm:
            OpenAICompatClient.from_settings(s, env={})
        self.assertIn("DEEPSEEK_API_KEY", str(cm.exception))

    def test_base_url_from_env(self):
        s = Settings(llm=LLMSettings())
        c = OpenAICompatClient.from_settings(s, env={"DEEPSEEK_API_KEY": KEY, "DEEPSEEK_BASE_URL": "https://b/v1/"})
        self.assertEqual(c.base_url, "https://b/v1")

    def test_request_info_is_redacted(self):
        c, _ = client(ScriptedOpener())
        info = c.request_info()
        self.assertEqual(info["headers"]["Authorization"], "***")
        self.assertNotIn(KEY, json.dumps(info))
        self.assertEqual(redact_headers({"X-Api-Key": "k", "Accept": "a"}), {"X-Api-Key": "***", "Accept": "a"})


class LoggingTest(unittest.TestCase):
    def test_logs_are_written_and_redacted(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, _ = client(ScriptedOpener(OK_BODY))
            llm = LoggedLLM(c, CallLogger(Path(tmp), secrets=[KEY]), "coder-strong")
            # 就算消息里意外带了 key，日志里也要被替换掉
            llm.complete([{"role": "user", "content": f"key 是 {KEY}"}], model="m")
            summary = (Path(tmp) / "llm_calls.jsonl").read_text(encoding="utf-8")
            full = (Path(tmp) / "llm_calls" / "0001.json").read_text(encoding="utf-8")
            self.assertNotIn(KEY, summary + full)
            data = json.loads(full)
            self.assertEqual(data["request"]["headers"]["Authorization"], "***")
            self.assertEqual(data["response"]["text"], "你好")
            self.assertEqual(json.loads(summary.splitlines()[0])["prompt_tokens"], 12)

    def test_failed_call_is_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            c, _ = client(ScriptedOpener(http_error(400)))
            llm = LoggedLLM(c, CallLogger(Path(tmp)), "a")
            with self.assertRaises(LLMError):
                llm.complete([], model="m")
            row = json.loads((Path(tmp) / "llm_calls.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(row["ok"])


class FakeLLMTest(unittest.TestCase):
    def test_list_and_function_scripts(self):
        f = FakeLLM(["a", lambda m: m[-1]["content"].upper()])
        self.assertEqual(f.complete([{"role": "user", "content": "x"}], model="m").text, "a")
        self.assertEqual(f.complete([{"role": "user", "content": "yz"}], model="m").text, "YZ")
        with self.assertRaises(LLMError):
            f.complete([], model="m")
        self.assertIn("yz", f.all_text_sent())


class SettingsTest(unittest.TestCase):
    def test_load_repo_settings(self):
        s = load_settings(helpers.ROOT / "firmforge.toml")
        self.assertIn("strong", s.models)
        self.assertEqual(s.llm.api_key_env, "DEEPSEEK_API_KEY")

    def test_tier_requires_name(self):
        s = Settings(models={"strong": ModelTier("")})
        with self.assertRaises(SettingsError):
            s.tier("strong")
        with self.assertRaises(SettingsError):
            s.tier("nope")

    def test_cost(self):
        self.assertAlmostEqual(ModelTier("m", 2.0, 8.0).cost(1_000_000, 500_000), 6.0)

    def test_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text('# 注释\nA=1\nexport B="two"\nC=\nEXISTING=new\n', encoding="utf-8")
            env = {"EXISTING": "old"}
            loaded = load_dotenv(p, env)
            self.assertEqual(env, {"EXISTING": "old", "A": "1", "B": "two"})
            self.assertEqual(sorted(loaded), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
