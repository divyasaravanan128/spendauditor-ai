"""Metrics agent: answers cost-number questions by calling the MCP server's tools in a loop.

This is the classic tool-use loop, with the tools discovered over MCP rather than hard-coded:
model picks a tool -> client calls the MCP server -> result goes back -> repeat until the model answers.
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from . import llm, mcp_client

MAX_STEPS = 6


class MetricsAnswer(BaseModel):
    answer: str
    calls: list[dict]  # [{tool, args, result}] for the trace and the chart


async def _run(session, question: str) -> MetricsAnswer:
    tools = await mcp_client.tool_specs(session)
    cat = await mcp_client.read_catalogue(session)
    system = ("You answer cloud and AI cost questions using the metrics tools only. Never guess numbers. "
              f"Data range: {cat['data_range']['first_day']} to {cat['data_range']['last_day']}. "
              f"Teams: {cat['known_values']['team']}. Models: {cat['known_values']['model']}. "
              "Costs are USD effective cost unless asked otherwise. Answer in 2-5 sentences with exact figures; "
              "use a short markdown table if comparing more than 3 items.")
    messages = [{"role": "user", "content": question}]
    calls = []
    for _ in range(MAX_STEPS):
        msg = llm.complete("metrics_agent", messages, system, tools=tools, max_tokens=1200)
        uses = [b for b in msg.content if getattr(b, "type", "") == "tool_use"]
        if not uses:
            return MetricsAnswer(answer=llm.text_of(msg), calls=calls)
        messages.append({"role": "assistant", "content": msg.content})
        results = []
        for u in uses:
            data = await mcp_client.call(session, u.name, dict(u.input))
            calls.append({"tool": u.name, "args": dict(u.input), "result": data})
            results.append({"type": "tool_result", "tool_use_id": u.id,
                            "content": json.dumps(data)[:12000], "is_error": "error" in data})
        messages.append({"role": "user", "content": results})
    return MetricsAnswer(answer="I needed too many steps for that question. Try narrowing it (one team or month).",
                         calls=calls)


def answer(question: str) -> MetricsAnswer:
    return mcp_client.run(_run, question)
