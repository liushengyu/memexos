"""Backend: the Claude API through the official `anthropic` Python SDK (optional: pip install "memexos[anthropic]").

For people without the Claude Code or Codex command lines. MemexOS runs the agent loop itself with the file
tools in memexos.backends.tools, so the Policy is enforced by this process: reads under cwd and the allowed
directories, writes only to the allowed paths, commands only with an allowed prefix. Web access uses the API's
server-side web search and fetch tools.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from pathlib import Path

from memexos import credentials, i18n
from memexos.backends.base import QUOTA_RE, READ_ONLY_NOTE, Backend, Policy, Result, parse_json_text
from memexos.backends.tools import Sandbox

DEFAULT_MODEL = "claude-opus-5"
# USD per million tokens (input, output). Cache reads cost 0.1x input, cache writes 1.25x input.
# Unknown models report tokens without a cost.
PRICES = {"claude-opus-5": (5.0, 25.0), "claude-opus-5-5": (4.0, 20.0), "claude-fable-5-1": (10.0, 50.0),
          "claude-fable-5": (10.0, 50.0), "claude-sonnet-5": (2.0, 10.0), "claude-opus-4-8": (5.0, 25.0),
          "claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0)}
MAX_TURNS = 200


def _sdk():
    try:
        import anthropic  # noqa: PLC0415 — optional dependency
        return anthropic
    except ImportError:
        return None


class AnthropicAPI(Backend):
    name = "anthropic"

    def model(self) -> str:
        return self.section.get("model") or DEFAULT_MODEL

    def available(self) -> tuple[bool, str]:
        if not _sdk():
            return False, i18n.t("backend.no_sdk")
        if not credentials.get(self.section.get("api_key_env") or "ANTHROPIC_API_KEY"):
            return False, i18n.t("backend.no_key", env=self.section.get("api_key_env") or "ANTHROPIC_API_KEY")
        return True, ""

    def _client(self, timeout: float):
        anthropic = _sdk()
        kw = {"api_key": credentials.get(self.section.get("api_key_env") or "ANTHROPIC_API_KEY"), "max_retries": 4,
              "timeout": min(timeout, 900)}
        if self.section.get("base_url"):
            kw["base_url"] = self.section["base_url"]
        proxy = (self.section.get("proxy") or "").strip()
        if proxy and proxy != "direct":
            kw["http_client"] = anthropic.DefaultHttpxClient(proxy=proxy)
        return anthropic.Anthropic(**kw)

    def _params(self, effort: str, schema: dict | None) -> dict:
        model = self.model()
        p: dict = {"model": model, "max_tokens": 16000, "cache_control": {"type": "ephemeral"}}
        oc: dict = {}
        if "haiku" not in model:
            p["thinking"] = {"type": "adaptive"}
            oc["effort"] = effort if effort in ("low", "medium", "high", "xhigh", "max") else "high"
        if schema is not None:
            oc["format"] = {"type": "json_schema", "schema": schema}
        if oc:
            p["output_config"] = oc
        return p

    def _cost(self, usage: dict) -> float | None:
        price = PRICES.get(self.model())
        if not price:
            return None
        pin, pout = price
        return round((usage["input_tokens"] * pin + usage["cache_read_tokens"] * pin * 0.1 + usage["cache_write_tokens"] * pin * 1.25
                      + usage["output_tokens"] * pout) / 1e6, 4)

    def _loop(self, content: list, sandbox: Sandbox, server_tools: list, effort: str, schema: dict | None,
              workdir: Path, timeout: float) -> Result:
        anthropic = _sdk()
        res = Result(ok=False, backend=self.name, model=self.model())
        usage = {"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0, "turns": 0}
        t0 = time.time()
        client = self._client(timeout)
        messages: list = [{"role": "user", "content": content}]
        params = self._params(effort, schema)
        tools = sandbox.definitions() + server_tools
        transcript = []
        try:
            while usage["turns"] < MAX_TURNS and time.time() - t0 < timeout:
                resp = client.messages.create(messages=messages, tools=tools, **params)
                usage["turns"] += 1
                u = resp.usage
                usage["input_tokens"] += u.input_tokens or 0
                usage["output_tokens"] += u.output_tokens or 0
                usage["cache_read_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0
                usage["cache_write_tokens"] += getattr(u, "cache_creation_input_tokens", 0) or 0
                if resp.stop_reason == "refusal":
                    det = getattr(resp, "stop_details", None)
                    res.tail = f"refused ({getattr(det, 'category', None) or 'no category'})"
                    break
                messages.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason == "pause_turn":
                    continue
                calls = [b for b in resp.content if b.type == "tool_use"]
                if resp.stop_reason == "tool_use" and calls:
                    results = []
                    for b in calls:
                        out, err = sandbox.call(b.name, dict(b.input or {}))
                        transcript.append({"tool": b.name, "input": b.input, "error": err})
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": out, "is_error": err})
                    messages.append({"role": "user", "content": results})   # all results in one message
                    continue
                text = "".join(b.text for b in resp.content if b.type == "text")
                res.text = text
                if schema is not None:
                    res.result = parse_json_text(text)
                    res.ok = res.result is not None
                    if not res.ok:
                        res.tail = f"no JSON in the final answer (stop: {resp.stop_reason}): {text[-300:]}"
                else:
                    res.result, res.ok = text, True
                break
            else:
                res.tail = "stopped: too many turns or out of time"
        except anthropic.AuthenticationError as e:
            res.tail = f"authentication failed: {e.message}"
        except anthropic.RateLimitError as e:
            res.transient, res.tail = True, f"rate limited: {e.message}"
        except anthropic.BadRequestError as e:
            res.tail = f"bad request: {e.message}"
            res.limited = bool(QUOTA_RE.search(e.message or ""))
        except anthropic.APIStatusError as e:
            res.tail = f"API error {e.status_code}: {e.message}"
            res.transient = e.status_code >= 500
            res.limited = e.status_code in (402, 403) and bool(QUOTA_RE.search(e.message or ""))
        except anthropic.APIConnectionError as e:
            res.transient, res.tail = True, f"connection error: {e}"
        res.usage = usage
        res.cost_usd = self._cost(usage)
        res.elapsed_s = round(time.time() - t0, 1)
        res.denials = sandbox.denied
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "api-transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, default=str)[:2_000_000],
                                                     encoding="utf-8")
        if res.ok and res.result is not None:
            (workdir / "out.json").write_text(json.dumps(res.result, ensure_ascii=False, indent=1), encoding="utf-8")
        return res

    @staticmethod
    def _content(text: str, images) -> list:
        blocks = []
        for im in images:
            mt = mimetypes.guess_type(str(im))[0] or "image/jpeg"
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                       "data": base64.standard_b64encode(Path(im).read_bytes()).decode("ascii")}})
        blocks.append({"type": "text", "text": text})
        return blocks

    def structured(self, prompt, schema, *, cwd, workdir, effort="high", timeout=3600, images=(), extra=""):
        text = prompt + READ_ONLY_NOTE.get(i18n.language(), READ_ONLY_NOTE["en"]) + (("\n" + extra) if extra else "")
        sandbox = Sandbox(Path(cwd), Policy(), read_only=True)
        return self._loop(self._content(text, images), sandbox, [], effort, schema, Path(workdir), timeout)

    def agent(self, prompt, *, cwd, workdir, policy: Policy, effort="high", timeout=3600, schema=None, images=()):
        sandbox = Sandbox(Path(cwd), policy)
        server = [{"type": "web_search_20260209", "name": "web_search"},
                  {"type": "web_fetch_20260209", "name": "web_fetch"}] if policy.web else []
        return self._loop(self._content(prompt, images), sandbox, server, effort, schema, Path(workdir), timeout)
