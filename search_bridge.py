#!/usr/bin/env python3
"""minima web search bridge — DuckDuckGo via ddgs (free, no API key)."""

from __future__ import annotations

import json
import sys


def _format_results(items: list[dict]) -> str:
    lines: list[str] = []
    for i, item in enumerate(items, 1):
        title = (item.get("title") or "Untitled").strip()
        href = (item.get("href") or item.get("url") or "").strip()
        body = (item.get("body") or item.get("snippet") or "").strip()
        block = f"{i}. {title}"
        if href:
            block += f"\n   {href}"
        if body:
            block += f"\n   {body}"
        lines.append(block)
    return "\n\n".join(lines)


def cmd_search(data: dict) -> dict:
    query = (data.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "query required"}

    max_results = int(data.get("max_results") or 8)
    max_results = max(1, min(max_results, 12))

    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # legacy package name
        except ImportError:
            return {
                "ok": False,
                "error": "ddgs not installed — run: pip install ddgs",
            }

    items: list[dict] = []
    last_err = ""
    ddgs = DDGS()
    for attempt in ("text", "news"):
        try:
            if attempt == "text":
                items = list(ddgs.text(query, max_results=max_results))
            else:
                items = list(ddgs.news(query, max_results=max_results))
            if items:
                break
        except Exception as e:
            last_err = str(e)
            items = []

    if not items:
        return {
            "ok": False,
            "query": query,
            "error": last_err or "no results found",
            "results": "",
            "source": "duckduckgo",
        }

    text = _format_results(items)
    return {
        "ok": True,
        "query": query,
        "results": text,
        "source": "duckduckgo",
        "count": len(items),
    }


def main() -> None:
    try:
        data = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"invalid json: {e}"}))
        sys.exit(1)
    try:
        out = cmd_search(data)
    except Exception as e:
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
