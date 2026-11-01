"""The OpenAI-compatible backend against a scripted local server: tool loop, policy enforcement, JSON, errors."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from memexos import backends, config
from memexos.backends.base import Policy

SCRIPT: list = []
SEEN: list = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"auth": self.headers.get("Authorization"), "body": body})
        status, reply = SCRIPT.pop(0)
        data = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def call(name, args, cid="c1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def msg(content="", calls=None, usage=(10, 5)):
    m = {"role": "assistant", "content": content}
    if calls:
        m["tool_calls"] = calls
    return {"choices": [{"message": m, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}


@pytest.fixture
def cfg(home, monkeypatch, tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("LOCAL_KEY", "sk-test")
    c = config.load(tmp_path / "v")
    c.data["backends"]["local"] = {"type": "openai", "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "model": "m",
                                   "api_key_env": "LOCAL_KEY", "proxy": "direct", "price_in": 1.0, "price_out": 2.0}
    SCRIPT.clear()
    SEEN.clear()
    yield c
    srv.shutdown()


def test_named_endpoint_is_a_backend(cfg):
    assert "local" in backends.names(cfg)
    b = backends.get(cfg, "local")
    assert b.available() == (True, "")
    assert backends.status(cfg)["local"]["available"]


def test_structured_reads_files_then_answers_json(cfg, tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (work / "note.md").write_text("the answer is 42")
    SCRIPT[:] = [(200, msg(calls=[call("read_file", {"path": "note.md", "offset": 1, "limit": 10})])), (200, msg('{"n": 42}'))]
    r = backends.get(cfg, "local").structured("what is it?", {"type": "object", "properties": {"n": {"type": "integer"}},
                                                                 "required": ["n"], "additionalProperties": False},
                                              cwd=work, workdir=tmp_path / "run")
    assert r.ok and r.result == {"n": 42} and r.usage["turns"] == 2
    assert SEEN[0]["auth"] == "Bearer sk-test" and "response_format" not in SEEN[0]["body"]      # free to call tools
    assert "the answer is 42" in SEEN[1]["body"]["messages"][-1]["content"]
    assert r.cost_usd == pytest.approx((20 * 1.0 + 10 * 2.0) / 1e6)


def test_agent_writes_only_where_the_policy_allows(cfg, tmp_path):
    work = tmp_path / "w"
    (work / "wiki").mkdir(parents=True)
    SCRIPT[:] = [(200, msg(calls=[call("write_file", {"path": "raw/x.md", "content": "no"}, "a"),
                                  call("write_file", {"path": "wiki/ok.md", "content": "yes"}, "b")])), (200, msg("done"))]
    r = backends.get(cfg, "local").agent("write", cwd=work, workdir=tmp_path / "run", policy=Policy(write=[work / "wiki"]))
    assert r.ok and (work / "wiki" / "ok.md").read_text() == "yes" and not (work / "raw" / "x.md").exists()
    assert r.denials


def test_quota_and_transient_errors_are_told_apart(cfg, tmp_path):
    SCRIPT[:] = [(429, {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}})]
    r = backends.get(cfg, "local").structured("x", {"type": "object"}, cwd=tmp_path, workdir=tmp_path / "r1")
    assert not r.ok and r.limited and not r.transient
    SCRIPT[:] = [(503, {"error": {"message": "overloaded"}})]
    r = backends.get(cfg, "local").structured("x", {"type": "object"}, cwd=tmp_path, workdir=tmp_path / "r2")
    assert not r.ok and r.transient and not r.limited


def test_an_answer_that_misses_the_schema_gets_one_formatting_turn(cfg, tmp_path):
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"], "additionalProperties": False}
    SCRIPT[:] = [(200, msg("It is forty-two.")), (200, msg('{"n": 42}'))]
    r = backends.get(cfg, "local").structured("x", schema, cwd=tmp_path, workdir=tmp_path / "r")
    assert r.ok and r.result == {"n": 42}
    last = SEEN[-1]["body"]
    assert last["response_format"]["type"] == "json_schema" and "tools" not in last
    SCRIPT[:] = [(200, msg('{"wrong": 1}')), (200, msg('{"still": "wrong"}'))]
    r = backends.get(cfg, "local").structured("x", schema, cwd=tmp_path, workdir=tmp_path / "r2")
    assert not r.ok and "missing n" in r.tail


def test_schema_errors():
    from memexos.backends.base import schema_errors
    sch = {"type": "object", "required": ["a"], "additionalProperties": False,
           "properties": {"a": {"type": "array", "items": {"type": "string", "enum": ["x", "y"]}}}}
    assert schema_errors(sch, {"a": ["x"]}) == []
    assert schema_errors(sch, {"a": ["z"], "b": 1}) == ["$: unexpected b", "$.a[0]: 'z' is not one of ['x', 'y']"]
    assert schema_errors({"type": "integer"}, True)
