#!/usr/bin/env python3
"""minima ↔ pysince bridge. Reads JSON from stdin, writes JSON to stdout."""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DB_PATH = Path.home() / ".minima" / "since.db"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _local_tz_name() -> str:
    """Detect system timezone as IANA name or UTC±H:MM offset."""
    try:
        local = datetime.datetime.now().astimezone()
        tz = local.tzinfo
        if tz and hasattr(tz, "key"):
            return tz.key
        if tz:
            offset = tz.utcoffset(local)
            if offset is not None:
                total_min = int(offset.total_seconds() // 60)
                sign = "+" if total_min >= 0 else "-"
                total_min = abs(total_min)
                h, m = divmod(total_min, 60)
                return f"UTC{sign}{h}:{m:02d}"
    except Exception:
        pass
    return "UTC"


def _resolve_tz(data: dict) -> str:
    tz = data.get("timezone")
    if isinstance(tz, str) and tz.strip():
        return tz.strip()
    return _local_tz_name()


def _store():
    from since import Store
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return Store(DB_PATH)


def _ts_ms(ms: int | None) -> datetime.datetime:
    if ms:
        return datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc).replace(tzinfo=None)
    return _now()


def cmd_status(_: dict) -> dict:
    try:
        from since import Store  # noqa: F401
        return {"ok": True, "db": str(DB_PATH)}
    except ImportError:
        return {"ok": False, "error": "pysince not installed"}


def cmd_session(data: dict) -> dict:
    sid = data.get("session_id")
    if not sid:
        return {"ok": False, "error": "session_id required"}
    store = _store()
    info = store.session_info(sid)
    now = _now()
    if not info:
        return {"ok": True, "active": False, "label": ""}
    duration = now - info["first"]
    gap = now - info["last"]
    from since.format import _format_timedelta_short, _format_timedelta_compact
    label = _format_timedelta_compact(duration)
    if gap.total_seconds() > 120:
        gap_label = _format_timedelta_short(gap)
    else:
        gap_label = None
    return {
        "ok": True,
        "active": True,
        "count": info["count"],
        "label": label,
        "gap": gap_label,
        "started": info["first"].isoformat(),
        "last": info["last"].isoformat(),
    }


def cmd_record(data: dict) -> dict:
    from since.models import Message

    import memory as mem

    sid = data.get("session_id")
    role = data.get("role")
    content = data.get("content", "")
    if not sid or not role:
        return {"ok": False, "error": "session_id and role required"}
    ttl = "ephemeral" if len(content.strip()) < 12 and role == "user" else "slow"
    store = _store()
    tz = _resolve_tz(data)
    ts = _ts_ms(data.get("ts"))
    msg = Message(sid, 0, role, content, ts, timezone=tz, ttl_class=ttl)
    turn = store.insert_next(msg)
    extracted = []
    if role == "user" and content.strip():
        extracted = mem.store_facts(
            store,
            mem.extract_facts(content, ts),
            ts,
            tz,
            chat_id=sid,
            source_msg_id=data.get("source_msg_id"),
            source_excerpt=data.get("source_excerpt") or content,
        )
    return {"ok": True, "turn": turn, "facts": extracted}


def _calendar_anchor(now: datetime.datetime, tz_name: str) -> str:
    from since.freshness import _utc_to_local

    local = _utc_to_local(now, tz_name) if tz_name != "UTC" else now
    tomorrow = local + datetime.timedelta(days=1)
    year = local.year
    return (
        f"Calendar anchor: today is {local.strftime('%A, %B %d, %Y')}; "
        f"tomorrow is {tomorrow.strftime('%A, %B %d, %Y')}. "
        f"Relative dates and memory facts without a year refer to {year}. "
        f"When searching for teams, rosters, matches, or news, include {year}."
    )


def _inject_year_into_now_line(block: str, year: int) -> str:
    import re

    if str(year) in block:
        return block
    return re.sub(r"(Now: \w{3} \w{3} \d{1,2},)", rf"\1 {year},", block, count=1)


def _collapse_stale_beliefs(stale: list) -> list:
    """Drop missing files, collapse repeated source_ids, cap list size."""
    if not stale:
        return stale
    filtered = []
    for item in stale:
        sid = item.source_id
        if sid and sid.startswith("read:"):
            fp = sid[len("read:"):]
            try:
                if not Path(fp).exists():
                    continue
            except (OSError, ValueError):
                pass
        filtered.append(item)
    seen: dict[str, object] = {}
    collapsed = []
    for item in reversed(filtered):
        key = item.source_id or (item.content_preview or "")[:48]
        if key in seen:
            seen[key] = int(seen[key]) + 1
            continue
        seen[key] = 1
        collapsed.append(item)
    collapsed.reverse()
    return collapsed[:8]


def cmd_context(data: dict) -> dict:
    from since.format import PROMPTING_NUDGE
    from since.freshness import _utc_to_local, build_world_freshness_block
    from since.stale_files import drifted_files

    import memory as mem

    sid = data.get("session_id")
    if not sid:
        return {"ok": False, "error": "session_id required"}
    store = _store()
    now = _ts_ms(data.get("now_ms")) if data.get("now_ms") else _now()
    tz_name = _resolve_tz(data)
    history = store.load_session(sid)
    stale = _collapse_stale_beliefs(store.stale_messages(sid, now))
    block = build_world_freshness_block(
        now,
        history=history,
        tz_name=tz_name,
        stale_info=stale,
        include_header=True,
        include_session=True,
    )
    block = PROMPTING_NUDGE + "\n\n" + block
    mem_block = mem.memory_block(store, now, tz_name)
    if mem_block:
        block += "\n\n" + mem_block
    import book as bk
    book_block = bk.book_context_block(store)
    if book_block:
        block += "\n\n" + book_block
    drift = drifted_files(store, sid)
    if drift:
        lines = ["", "Tracked files that changed since last read:"]
        for d in drift[:8]:
            reasons = ", ".join(d.get("reasons", []))
            lines.append(f"- {Path(d['filepath']).name}: {reasons}")
        block += "\n".join(lines)
    block = _inject_year_into_now_line(block, (
        _utc_to_local(now, tz_name) if tz_name != "UTC" else now
    ).year)
    block += "\n" + _calendar_anchor(now, tz_name)
    return {"ok": True, "block": block, "timezone": tz_name}


def cmd_stamp(data: dict) -> dict:
    from since.stale_files import stamp_file_read

    sid = data.get("session_id")
    path = data.get("path")
    if not sid or not path:
        return {"ok": False, "error": "session_id and path required"}
    try:
        source_id = stamp_file_read(path, _store(), sid)
        return {"ok": True, "source_id": source_id}
    except FileNotFoundError:
        return {"ok": False, "error": "file not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cmd_staleness(data: dict) -> dict:
    from since.stale_files import check_and_invalidate_detail
    from since.format import _format_timedelta_short

    sid = data.get("session_id")
    path = data.get("path")
    if not sid or not path:
        return {"ok": False, "error": "session_id and path required"}
    detail = check_and_invalidate_detail(path, _store(), sid)
    result = {"ok": True, **detail}
    if detail.get("read_at"):
        try:
            read_at = datetime.datetime.fromisoformat(detail["read_at"])
            result["read_ago"] = _format_timedelta_short(_now() - read_at)
        except ValueError:
            pass
    return result


def cmd_memory_list(data: dict) -> dict:
    import memory as mem

    store = _store()
    now = _ts_ms(data.get("now_ms")) if data.get("now_ms") else _now()
    tz = _resolve_tz(data)
    as_of = _ts_ms(data.get("as_of_ms")) if data.get("as_of_ms") else None
    if as_of is not None:
        facts = mem.list_facts_as_of(store, as_of, tz)
        return {"ok": True, "facts": facts, "as_of": as_of.isoformat(), "live": False}
    return {"ok": True, "facts": mem.list_facts(store, now, tz), "live": True}


def cmd_memory_pin(data: dict) -> dict:
    import memory as mem

    content = (data.get("content") or "").strip()
    if not content:
        return {"ok": False, "error": "content required"}
    store = _store()
    now = _ts_ms(data.get("now_ms")) if data.get("now_ms") else _now()
    tz = _resolve_tz(data)
    fact = mem.pin_fact(
        store,
        content,
        now,
        tz,
        ttl_class=data.get("ttl_class") or "permanent",
        chat_id=data.get("session_id"),
        source_msg_id=data.get("source_msg_id"),
        source_excerpt=data.get("source_excerpt"),
        revises_turn_id=int(data["revises_turn_id"]) if data.get("revises_turn_id") is not None else None,
    )
    return {"ok": True, "fact": fact, "facts": mem.list_facts(store, now, tz)}


def cmd_memory_provenance(data: dict) -> dict:
    import memory as mem

    turn_id = data.get("turn_id")
    if turn_id is None:
        return {"ok": False, "error": "turn_id required"}
    store = _store()
    now = _ts_ms(data.get("now_ms")) if data.get("now_ms") else _now()
    tz = _resolve_tz(data)
    result = mem.fact_provenance(store, int(turn_id), now, tz)
    return result


def cmd_book_list(data: dict) -> dict:
    import book as bk

    store = _store()
    book_name = data.get("book_name") or data.get("name")
    query = data.get("query") or data.get("q")
    if book_name:
        doc = bk.get_book(
            store,
            str(book_name),
            query=query if isinstance(query, str) else None,
        )
        if doc is None:
            return {"ok": False, "error": "book not found"}
        return {"ok": True, "book": doc}
    books = bk.list_books(store)
    return {"ok": True, "books": books}


def cmd_book_store(data: dict) -> dict:
    import book as bk

    content = (data.get("content") or data.get("passage") or "").strip()
    body = data.get("body")
    if not content and not body:
        return {"ok": False, "error": "content required"}
    store = _store()
    now = _ts_ms(data.get("now_ms")) if data.get("now_ms") else _now()
    tz = _resolve_tz(data)
    entry = bk.store_entry(
        store,
        content or body,
        now,
        tz,
        book_name=data.get("book_name") or data.get("book"),
        section=data.get("section"),
        title=data.get("title"),
        body=body,
        body_md=body or content,
        chat_id=data.get("session_id"),
        source_msg_id=data.get("source_msg_id"),
        source_excerpt=data.get("source_excerpt") or content or body,
        replace_msg_id=bool(data.get("replace") or data.get("update_existing")),
    )
    book_doc = bk.get_book(store, entry["book_name"])
    return {
        "ok": True,
        "entry": entry,
        "books": bk.list_books(store),
        "book": book_doc,
    }


def cmd_book_forget(data: dict) -> dict:
    import book as bk

    store = _store()
    turn_id = data.get("turn_id")
    book_name = data.get("book_name") or data.get("book")
    position = data.get("position")
    title = data.get("title")
    if turn_id is None:
        entry = bk.resolve_passage(
            store,
            book_name=book_name,
            position=position,
            title=title,
        )
        if not entry:
            return {"ok": False, "error": "passage not found"}
        turn_id = entry["turn_id"]
    entry = bk.get_entry(store, int(turn_id))
    ok = bk.forget_entry(store, int(turn_id))
    out = {"ok": ok, "books": bk.list_books(store), "deleted_turn_id": int(turn_id)}
    if entry:
        doc = bk.get_book(store, entry["book_name"])
        if doc:
            out["book"] = doc
    return out


def cmd_book_create(data: dict) -> dict:
    import book as bk

    book_name = data.get("book_name") or data.get("book") or data.get("name")
    if not book_name:
        return {"ok": False, "error": "book_name required"}
    store = _store()
    now = _ts_ms(data.get("now_ms")) if data.get("now_ms") else _now()
    created = bk.create_book(
        store,
        str(book_name),
        now,
        display_title=data.get("display_title") or data.get("title"),
    )
    return {"ok": True, "book": created, "books": bk.list_books(store)}


def cmd_book_update(data: dict) -> dict:
    import book as bk

    store = _store()
    turn_id = data.get("turn_id")
    book_name = data.get("book_name") or data.get("book")
    position = data.get("position")
    title_query = data.get("passage_title") or data.get("title_query")
    if turn_id is None:
        entry = bk.resolve_passage(
            store,
            book_name=book_name,
            position=position,
            title=title_query,
        )
        if not entry:
            return {"ok": False, "error": "passage not found — call read_book for turn_id"}
        turn_id = entry["turn_id"]
    now = _ts_ms(data.get("now_ms")) if data.get("now_ms") else _now()
    tz = _resolve_tz(data)
    passage = data.get("passage") or data.get("content") or data.get("body")
    try:
        entry = bk.update_passage(
            store,
            int(turn_id),
            now,
            tz,
            title=data.get("title"),
            section=data.get("section"),
            passage=passage,
            body=data.get("body"),
            body_md=data.get("body_md") or passage,
            book_name=book_name,
            clear_section=bool(data.get("clear_section")),
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    book_doc = bk.get_book(store, entry["book_name"])
    return {
        "ok": True,
        "entry": entry,
        "books": bk.list_books(store),
        "book": book_doc,
    }


def cmd_memory_forget(data: dict) -> dict:
    import memory as mem

    turn_id = data.get("turn_id")
    if turn_id is None:
        return {"ok": False, "error": "turn_id required"}
    store = _store()
    ok = mem.forget_fact(store, int(turn_id))
    return {"ok": ok}


def cmd_diagram_export(data: dict) -> dict:
    import base64

    svg = (data.get("svg") or "").strip()
    if not svg:
        return {"ok": False, "error": "svg required"}
    try:
        import svg_to_img as s2i

        width = int(data.get("width") or 1200)
        background = str(data.get("background") or "#ffffff")
        png = s2i.render(svg, width=width, background=background)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    out: dict = {"ok": True, "bytes": len(png), "png_b64": base64.b64encode(png).decode("ascii")}
    path = data.get("path")
    workspace_root = data.get("workspace_root")
    if path:
        try:
            saved = s2i.save(png, str(path))
            out["path"] = saved
        except Exception as e:
            out["save_error"] = str(e)
            base = Path(str(workspace_root)) if workspace_root else Path(__file__).resolve().parent
            fallback_dir = base / "minima-exports"
            fallback_dir.mkdir(parents=True, exist_ok=True)
            fallback = fallback_dir / Path(str(path)).name
            try:
                saved = s2i.save(png, str(fallback))
                out["path"] = saved
                out["save_fallback"] = True
            except Exception:
                pass
    else:
        base = Path(str(workspace_root)) if workspace_root else Path(__file__).resolve().parent
        fallback_dir = base / "minima-exports"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback = fallback_dir / f"diagram-{int(__import__('time').time() * 1000)}.png"
        try:
            saved = s2i.save(png, str(fallback))
            out["path"] = saved
        except Exception as e:
            out["save_error"] = str(e)
    return out


def cmd_memory_sync(data: dict) -> dict:
    import memory as mem

    messages = data.get("messages") or []
    if not isinstance(messages, list):
        return {"ok": False, "error": "messages must be a list"}
    store = _store()
    now = _ts_ms(data.get("now_ms")) if data.get("now_ms") else _now()
    tz = _resolve_tz(data)
    count = mem.sync_from_messages(
        store,
        [str(m) for m in messages if m],
        now,
        tz,
        chat_id=data.get("session_id"),
    )
    return {"ok": True, "added": count, "facts": mem.list_facts(store, now, tz)}


HANDLERS = {
    "status": cmd_status,
    "session": cmd_session,
    "record": cmd_record,
    "context": cmd_context,
    "stamp": cmd_stamp,
    "staleness": cmd_staleness,
    "memory_list": cmd_memory_list,
    "memory_pin": cmd_memory_pin,
    "memory_forget": cmd_memory_forget,
    "memory_sync": cmd_memory_sync,
    "memory_provenance": cmd_memory_provenance,
    "book_list": cmd_book_list,
    "book_store": cmd_book_store,
    "book_forget": cmd_book_forget,
    "book_create": cmd_book_create,
    "book_update": cmd_book_update,
    "diagram_export": cmd_diagram_export,
}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "error": "usage: since_bridge.py <command>"}))
        sys.exit(1)
    cmd = sys.argv[1]
    handler = HANDLERS.get(cmd)
    if not handler:
        print(json.dumps({"ok": False, "error": f"unknown command: {cmd}"}))
        sys.exit(1)
    try:
        if sys.stdin.isatty():
            data = {}
        else:
            raw = sys.stdin.buffer.read().decode("utf-8-sig")
            data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"invalid json: {e}"}))
        sys.exit(1)
    try:
        out = handler(data)
    except ImportError:
        out = {"ok": False, "error": "pysince not installed — run: pip install pysince"}
    except Exception as e:
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
