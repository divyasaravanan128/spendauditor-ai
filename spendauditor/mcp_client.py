"""MCP client: how the agents reach the metrics. They never import metrics.py directly.

The server runs as a subprocess speaking MCP over stdio. On connect the client *discovers* the tools
(names, descriptions, JSON schemas) instead of hard-coding them, so a new metric added to the server is
immediately usable by the metrics agent.
"""
from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from . import config

SERVER = Path(__file__).resolve().parent.parent / "mcp_server.py"


@asynccontextmanager
async def connect():
    # The server is a separate process: tell it which bill to read (the sample, or an uploaded one).
    env = {**get_default_environment(), "SPENDAUDITOR_DB": str(config.DB_PATH)}
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _result_to_data(result) -> dict:
    """Prefer structured output; fall back to parsing the text content."""
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        text = " ".join(getattr(c, "text", "") for c in result.content)
        return {"error": text or "tool error"}
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured:
        # some servers wrap non-object results as {"result": ...}
        return structured.get("result", structured) if isinstance(structured, dict) and len(structured) == 1 \
            and "result" in structured else structured
    text = " ".join(getattr(c, "text", "") for c in result.content)
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


async def call(session: ClientSession, name: str, args: dict) -> dict:
    return _result_to_data(await session.call_tool(name, {k: v for k, v in args.items() if v is not None}))


async def tool_specs(session: ClientSession) -> list[dict]:
    """The server's tools as Anthropic tool definitions (name, description, input_schema)."""
    listed = await session.list_tools()
    specs = []
    for t in listed.tools:
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
        specs.append({"name": t.name, "description": t.description or "", "input_schema": schema})
    return specs


async def read_catalogue(session: ClientSession) -> dict:
    res = await session.read_resource("spend://catalogue")
    return json.loads(res.contents[0].text)


async def get_recipe(session: ClientSession, question: str) -> str:
    res = await session.get_prompt("investigate_cost_change", {"question": question})
    return res.messages[0].content.text


def run(coro_fn, *args):
    """Run an async function that takes a connected session, from synchronous code."""

    async def _main():
        async with connect() as session:
            return await coro_fn(session, *args)

    return asyncio.run(_main())


def call_many(calls: list[tuple[str, dict]]) -> list[dict]:
    """Several tool calls over one connection (one subprocess)."""

    async def _go(session):
        return [await call(session, n, a) for n, a in calls]

    return run(_go)
