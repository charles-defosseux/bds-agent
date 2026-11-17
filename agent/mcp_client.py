"""Synchronous MCP client for the LangGraph nodes.

An asyncio loop runs in a background thread and owns the two stdio server processes (started once per Python process).
The harness side of the contract lives here, never in the model's hands:
  * `idempotency_key` = sha256 of (thread_id, tool, canonical args), injected for keyed tools;
  * `actor_id` = the treasurer (user_id of the run), injected where the tool asks for it;
  * both are removed from the schema the model sees, and any value the model sends for them is overwritten.
"""
import asyncio
import hashlib
import json
import os
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from servers.common import HIDDEN_ARGS

ROOT = Path(__file__).resolve().parent.parent
SERVER_MODULES = {"bds-registry": "servers.registry", "bds-treasury": "servers.treasury"}
CALL_TIMEOUT_S = 60


def idempotency_key(thread_id: str, tool: str, args: dict) -> str:
    """Same logical action in the same run => same key. No timestamp, no randomness."""
    canonical = json.dumps([thread_id, tool, args], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _parse_error(text: str) -> dict:
    """The typed error raised by our servers, or a best-effort one for errors raised by the SDK itself."""
    try:
        err = json.loads(text[text.index("{"):])
        if isinstance(err, dict) and "code" in err:
            return {"code": err["code"], "message": err.get("message", ""), "hint": err.get("hint", ""),
                    "retryable": bool(err.get("retryable", False))}
    except ValueError:
        pass
    code = "invalid_argument" if "validation error" in text.lower() else "internal"
    hint = "Fix the arguments to match the tool schema." if code == "invalid_argument" else "Stop and report this error."
    return {"code": code, "message": text[:500], "hint": hint, "retryable": False}


class McpClient:
    def __init__(self, db_path: str | Path | None = None):
        self._env = {"BDS_DB_PATH": str(db_path)} if db_path else {}
        if not db_path and os.environ.get("BDS_DB_PATH"):
            self._env["BDS_DB_PATH"] = os.environ["BDS_DB_PATH"]
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="mcp-loop")
        self._sessions: dict[str, ClientSession] = {}  # server name -> session
        self._tool_server: dict[str, str] = {}  # tool name -> server name
        self._tools: list[dict] = []
        self._hidden: dict[str, set[str]] = {}  # tool name -> hidden args it accepts
        self._params: dict[str, set[str]] = {}  # tool name -> argument names the model may send
        self._stop: asyncio.Event | None = None
        self._main = None
        self._failure: dict | None = None  # bonus: injected fault (see set_failure)
        self._failures_done = 0

    # -- lifecycle ---------------------------------------------------------------------------------------------------
    def start(self) -> "McpClient":
        self._thread.start()
        ready = threading.Event()
        errors: list[BaseException] = []
        self._main = asyncio.run_coroutine_threadsafe(self._run(ready, errors), self._loop)
        ready.wait(timeout=60)
        if errors or not ready.is_set():
            self.close()
            raise RuntimeError(f"MCP servers failed to start: {errors[0] if errors else 'timeout'}")
        return self

    def close(self) -> None:
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._main is not None:
            try:
                self._main.result(timeout=15)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def __enter__(self) -> "McpClient":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    async def _run(self, ready: threading.Event, errors: list) -> None:
        # Sessions must be opened and closed by the same task: this coroutine owns them for the whole lifetime.
        self._stop = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                for name, module in SERVER_MODULES.items():
                    params = StdioServerParameters(command=sys.executable, args=["-m", module], cwd=ROOT, env=self._env)
                    read, write = await stack.enter_async_context(stdio_client(params))
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    self._sessions[name] = session
                    for tool in (await session.list_tools()).tools:
                        props = tool.input_schema.get("properties", {})
                        self._tool_server[tool.name] = name
                        self._hidden[tool.name] = {a for a in HIDDEN_ARGS if a in props}
                        self._params[tool.name] = set(props) - set(HIDDEN_ARGS)
                        self._tools.append(self._model_view(tool, name))
                ready.set()
                await self._stop.wait()
        except BaseException as e:  # noqa: BLE001 - reported to start()
            errors.append(e)
            ready.set()

    @staticmethod
    def _model_view(tool, server: str) -> dict:
        """What the model sees: no hidden arguments in the schema."""
        schema = json.loads(json.dumps(tool.input_schema))
        for arg in HIDDEN_ARGS:
            schema.get("properties", {}).pop(arg, None)
        schema["required"] = [r for r in schema.get("required", []) if r not in HIDDEN_ARGS]
        ann = tool.annotations
        return {"name": tool.name, "description": tool.description or "", "input_schema": schema, "server": server,
                "read_only": bool(ann and ann.read_only_hint), "keyed": "idempotency_key" in tool.input_schema.get("properties", {})}

    # -- tools -------------------------------------------------------------------------------------------------------
    def list_tools(self) -> list[dict]:
        """Tool definitions for the model (hidden arguments removed)."""
        return json.loads(json.dumps(self._tools))

    def set_failure(self, spec: dict | None) -> None:
        """Bonus (fault injection), reset at each run. spec = {"tool": "pay_expense", "mode": "timeout_after_write",
        "times": 1}: the first `times` calls of that tool really reach the server and commit, but the client then reports a
        retryable `timeout` as if the response had been lost. None switches the injection off."""
        if spec and spec.get("mode") != "timeout_after_write":
            raise ValueError(f"unknown inject_failure mode: {spec.get('mode')!r}")
        self._failure, self._failures_done = spec, 0

    def _lost_response(self, tool: str) -> bool:
        f = self._failure
        if not f or f.get("tool") != tool or self._failures_done >= f.get("times", 1):
            return False
        self._failures_done += 1
        return True

    def call(self, tool: str, args: dict[str, Any], ctx: dict[str, str]) -> dict:
        """Call a tool. ctx = {"thread_id": ..., "user_id": ...}. Returns
        {"ok": True, "data": {...}} or {"ok": False, "error": {"code", "message", "hint", "retryable"}}."""
        if tool not in self._tool_server:
            return {"ok": False, "error": {"code": "not_found", "message": f"Unknown tool {tool}.",
                                           "hint": "Use one of the listed tools.", "retryable": False}}
        # The model never chooses these: drop whatever it sent, then inject ours.
        clean = {k: v for k, v in args.items() if k not in HIDDEN_ARGS}
        if unknown := sorted(set(clean) - self._params[tool]):  # the SDK would silently ignore them: we refuse (extra=forbid)
            return {"ok": False, "error": {"code": "invalid_argument", "message": f"Unknown argument(s) for {tool}: {unknown}.",
                                           "hint": f"Allowed arguments: {sorted(self._params[tool])}.", "retryable": False}}
        sent = dict(clean)
        if "idempotency_key" in self._hidden[tool]:
            sent["idempotency_key"] = idempotency_key(ctx["thread_id"], tool, clean)
        if "actor_id" in self._hidden[tool]:
            sent["actor_id"] = ctx["user_id"]
        session = self._sessions[self._tool_server[tool]]
        fut = asyncio.run_coroutine_threadsafe(session.call_tool(tool, sent), self._loop)
        try:
            result = fut.result(timeout=CALL_TIMEOUT_S)
        except TimeoutError:
            fut.cancel()
            return {"ok": False, "error": {"code": "timeout", "message": f"{tool} did not answer in {CALL_TIMEOUT_S}s.",
                                           "hint": "Do not assume it worked.", "retryable": True}}
        if self._lost_response(tool):  # the write is committed, the answer never arrives
            return {"ok": False, "error": {"code": "timeout", "message": f"{tool} did not answer in {CALL_TIMEOUT_S}s.",
                                           "hint": "Do not assume it failed: the call may have been applied.",
                                           "retryable": True}}
        text = result.content[0].text if result.content else ""
        if result.is_error:
            return {"ok": False, "error": _parse_error(text)}
        data = result.structured_content if result.structured_content is not None else json.loads(text)
        return {"ok": True, "data": data}
