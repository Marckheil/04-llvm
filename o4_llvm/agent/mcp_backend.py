"""The agent's view of the tools: the MCP server, spawned as a subprocess over stdio.

One server per episode, so each episode starts with a fresh compile budget. Builds
are still cached on disk across episodes (D11).
"""

import json
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from o4_llvm.config import REPO_ROOT


class McpBackend:
    def __init__(self, budget: int, config: str | None = None, log: str | None = None):
        args = ["-m", "o4_llvm.tools.server", "--budget", str(budget)]
        if config:
            args += ["--config", config]
        if log:
            args += ["--log", log]
        self.params = StdioServerParameters(command=sys.executable, args=args,
                                            cwd=str(REPO_ROOT))
        self._stack = AsyncExitStack()
        self._tools: list[dict] = []

    async def __aenter__(self) -> "McpBackend":
        streams = await self._stack.enter_async_context(stdio_client(self.params))
        self.session = await self._stack.enter_async_context(ClientSession(*streams))
        await self.session.initialize()
        self._tools = [{"name": t.name, "description": t.description or "",
                        "input_schema": t.input_schema}
                       for t in (await self.session.list_tools()).tools]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def schemas(self) -> list[dict]:
        return self._tools

    async def call(self, name: str, args: dict) -> dict:
        if name not in {t["name"] for t in self._tools}:
            return {"ok": False, "error": {"code": "INVALID_ARGUMENT",
                                           "message": f"unknown tool {name!r}"}}
        res = await self.session.call_tool(name, args if isinstance(args, dict) else {})
        text = res.content[0].text if res.content else ""
        if res.is_error:  # rejected by the transport before reaching ToolService
            return {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": text[:500]}}
        sc = res.structured_content
        if sc is not None:
            return sc.get("result", sc) if set(sc) == {"result"} else sc
        return json.loads(text)
