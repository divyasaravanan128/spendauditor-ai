"""Web agent: outside-world facts (provider price changes, model releases) via Claude's server-side
web search tool. Off by default (SPENDAUDITOR_WEB_SEARCH=true to enable) because each search costs
$0.01, more than the tokens of a whole investigation."""
from __future__ import annotations

from pydantic import BaseModel

from . import config, llm

WEB_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 2}


class WebFinding(BaseModel):
    answer: str
    sources: list[dict]
    searched: bool


def search(question: str) -> WebFinding:
    if not config.WEB_SEARCH_ENABLED:
        return WebFinding(answer="Web search is turned off in this deployment (it costs $0.01 per search).",
                          sources=[], searched=False)
    msg = llm.complete("web_search", [{"role": "user", "content":
                       f"{question}\n\nSearch the web, then answer in 2-4 sentences with dates. Say clearly if "
                       "you found nothing relevant."}], tools=[WEB_TOOL], max_tokens=700)
    sources = []
    for block in msg.content:
        if getattr(block, "type", "") == "web_search_tool_result" and isinstance(block.content, list):
            sources += [{"title": r.title, "url": r.url} for r in block.content if hasattr(r, "url")][:4]
    return WebFinding(answer=llm.text_of(msg), sources=sources[:6], searched=True)
