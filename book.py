"""minima book — named documents with passages, sections, and hidden receipts."""

from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING

from since.models import Message

if TYPE_CHECKING:
    from since.store import Store

BOOK_SESSION = "minima:book"
BOOK_ROLE = "book"
DEFAULT_BOOK = "default"

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_meta (
    turn_id         INTEGER PRIMARY KEY,
    book_name       TEXT NOT NULL DEFAULT 'default',
    section         TEXT,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    body_md         TEXT,
    source_chat_id  TEXT,
    source_msg_id   TEXT,
    source_excerpt  TEXT
);
CREATE TABLE IF NOT EXISTS book_registry (
    book_name       TEXT PRIMARY KEY,
    display_title   TEXT,
    created_at      TEXT NOT NULL
);
"""

SOURCE_EXCERPT_MAX = 320
BODY_MAX = 12000
BODY_MD_MAX = 120000
TITLE_MAX = 120
BOOK_NAME_MAX = 64
SECTION_MAX = 120


def _clean_excerpt(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) > SOURCE_EXCERPT_MAX:
        return text[: SOURCE_EXCERPT_MAX - 1] + "…"
    return text


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\s+", " ", text.strip())
    return text


_QUOTE_EDGE = re.compile(r'^[\s"\'`“”‘’«»]+|[\s"\'`“”‘’«»]+$')


def _strip_wrapping_quotes(text: str | None) -> str | None:
    if text is None:
        return None
    s = str(text).strip()
    for _ in range(4):
        n = _QUOTE_EDGE.sub("", s).strip()
        if n == s:
            break
        s = n
    return s or None


def normalize_book_name(name: str | None) -> str:
    raw = (name or DEFAULT_BOOK).strip().lower()
    raw = re.sub(r"[^\w\s\-]", "", raw)
    raw = re.sub(r"\s+", "-", raw).strip("-")
    if not raw:
        raw = DEFAULT_BOOK
    return raw[:BOOK_NAME_MAX]


def normalize_section(section: str | None) -> str | None:
    if not section:
        return None
    section = _strip_wrapping_quotes(section) or ""
    section = re.sub(r"\s+", " ", section.strip())
    if not section:
        return None
    return section[:SECTION_MAX]


def distill_entry(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("content required")

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    title_src = lines[0] if lines else raw
    title_src = re.sub(r"^#{1,6}\s+", "", title_src)
    title_src = re.sub(r"^\d+\.\s+", "", title_src)
    title_src = re.sub(r"^[-*+]\s+", "", title_src)
    title = _strip_markdown(title_src)
    title = _strip_wrapping_quotes(title) or title
    if not title:
        title = "Untitled passage"
    if len(title) > TITLE_MAX:
        title = title[: TITLE_MAX - 1].rstrip() + "…"

    plain = _strip_markdown(raw)
    if not plain:
        plain = title
    body = plain[:BODY_MAX]
    if len(plain) > BODY_MAX:
        body = body[: BODY_MAX - 1].rstrip() + "…"
    return title, body


def _ensure_meta(store: Store) -> None:
    conn = store._conn()
    conn.executescript(_META_SCHEMA)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS book_registry "
        "(book_name TEXT PRIMARY KEY, display_title TEXT, created_at TEXT NOT NULL)"
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(book_meta)").fetchall()}
    if "book_name" not in cols:
        conn.execute("ALTER TABLE book_meta ADD COLUMN book_name TEXT NOT NULL DEFAULT 'default'")
    if "section" not in cols:
        conn.execute("ALTER TABLE book_meta ADD COLUMN section TEXT")
    if "body_md" not in cols:
        conn.execute("ALTER TABLE book_meta ADD COLUMN body_md TEXT")
    conn.execute("UPDATE book_meta SET book_name = 'default' WHERE book_name IS NULL OR book_name = ''")
    conn.execute("UPDATE book_meta SET body_md = body WHERE body_md IS NULL OR body_md = ''")
    conn.commit()


def _get_meta(store: Store, turn_id: int) -> dict:
    _ensure_meta(store)
    row = store._conn().execute(
        "SELECT turn_id, book_name, section, title, body, body_md, "
        "source_chat_id, source_msg_id, source_excerpt "
        "FROM book_meta WHERE turn_id = ?",
        (turn_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        "book_name": row[1] or DEFAULT_BOOK,
        "section": row[2],
        "title": row[3],
        "body": row[4],
        "body_md": row[5] or row[4],
        "source_chat_id": row[6],
        "source_msg_id": row[7],
        "source_excerpt": row[8],
    }


def _set_meta(
    store: Store,
    turn_id: int,
    *,
    book_name: str,
    section: str | None,
    title: str,
    body: str,
    body_md: str | None = None,
    source_chat_id: str | None = None,
    source_msg_id: str | None = None,
    source_excerpt: str | None = None,
) -> None:
    _ensure_meta(store)
    conn = store._conn()
    conn.execute(
        "INSERT INTO book_meta "
        "(turn_id, book_name, section, title, body, body_md, source_chat_id, source_msg_id, source_excerpt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(turn_id) DO UPDATE SET "
        "book_name = excluded.book_name, "
        "section = COALESCE(excluded.section, book_meta.section), "
        "title = excluded.title, "
        "body = excluded.body, "
        "body_md = COALESCE(excluded.body_md, book_meta.body_md), "
        "source_chat_id = COALESCE(excluded.source_chat_id, book_meta.source_chat_id), "
        "source_msg_id = COALESCE(excluded.source_msg_id, book_meta.source_msg_id), "
        "source_excerpt = COALESCE(excluded.source_excerpt, book_meta.source_excerpt)",
        (
            turn_id,
            book_name,
            section,
            title,
            body,
            body_md or body,
            source_chat_id,
            source_msg_id,
            _clean_excerpt(source_excerpt),
        ),
    )
    conn.commit()


def _entry_dict(msg: Message, meta: dict, *, index: int | None = None, index_from_end: int | None = None) -> dict:
    out = {
        "turn_id": msg.turn_id,
        "book_name": meta.get("book_name") or DEFAULT_BOOK,
        "section": meta.get("section"),
        "title": meta.get("title") or msg.content,
        "body": meta.get("body") or msg.content,
        "body_md": meta.get("body_md") or meta.get("body") or msg.content,
        "created_at": msg.created_at.isoformat(),
        "source_chat_id": meta.get("source_chat_id") or msg.source_id,
        "source_msg_id": meta.get("source_msg_id"),
        "source_excerpt": meta.get("source_excerpt"),
    }
    if index is not None:
        out["index"] = index
    if index_from_end is not None:
        out["index_from_end"] = index_from_end
    return out


def _find_by_msg_id(store: Store, source_msg_id: str, book_name: str | None = None) -> int | None:
    if not source_msg_id:
        return None
    _ensure_meta(store)
    if book_name:
        row = store._conn().execute(
            "SELECT turn_id FROM book_meta WHERE source_msg_id = ? AND book_name = ? "
            "ORDER BY turn_id DESC LIMIT 1",
            (source_msg_id, book_name),
        ).fetchone()
    else:
        row = store._conn().execute(
            "SELECT turn_id FROM book_meta WHERE source_msg_id = ? ORDER BY turn_id DESC LIMIT 1",
            (source_msg_id,),
        ).fetchone()
    return int(row[0]) if row else None


def _iter_passages(store: Store, book_name: str | None = None) -> list[tuple[Message, dict]]:
    _ensure_meta(store)
    msgs = store.load_session(BOOK_SESSION)
    out: list[tuple[Message, dict]] = []
    for msg in msgs:
        if msg.role != BOOK_ROLE:
            continue
        meta = _get_meta(store, msg.turn_id)
        if book_name and meta.get("book_name") != book_name:
            continue
        out.append((msg, meta))
    return out


def store_entry(
    store: Store,
    content: str,
    now: datetime.datetime,
    tz: str = "UTC",
    *,
    book_name: str | None = None,
    section: str | None = None,
    title: str | None = None,
    body: str | None = None,
    body_md: str | None = None,
    chat_id: str | None = None,
    source_msg_id: str | None = None,
    source_excerpt: str | None = None,
    replace_msg_id: bool = True,
) -> dict:
    book = normalize_book_name(book_name)
    sec = normalize_section(section)
    raw_md = (body_md or body or content or "").strip()
    create_book(store, book, now)
    if title and (body or body_md):
        dist_title = _strip_wrapping_quotes(title.strip()) or title.strip()
        dist_body = (body or body_md or content).strip()
    else:
        dist_title, dist_body = distill_entry(raw_md or content)

    if not dist_title:
        raise ValueError("title required")
    if not dist_body:
        raise ValueError("passage required")

    plain_body = _strip_markdown(dist_body)
    if len(plain_body) > BODY_MAX:
        plain_body = plain_body[: BODY_MAX - 1].rstrip() + "…"
    if len(raw_md) > BODY_MD_MAX:
        # Prefer keeping complete svg fences when truncating
        cut = raw_md[:BODY_MD_MAX]
        open_fence = cut.rfind("```")
        close_ok = raw_md.find("```", open_fence + 3) if open_fence >= 0 else -1
        if open_fence >= 0 and (close_ok < 0 or close_ok >= BODY_MD_MAX):
            cut = raw_md[:open_fence].rstrip()
        raw_md = cut.rstrip() + ("…" if cut != raw_md else "")

    _ensure_meta(store)
    existing_id = (
        _find_by_msg_id(store, source_msg_id, book)
        if replace_msg_id and source_msg_id
        else None
    )
    if existing_id is not None:
        conn = store._conn()
        conn.execute(
            "UPDATE messages SET content = ?, created_at = ?, source_id = ? "
            "WHERE session_id = ? AND turn_id = ? AND role = ?",
            (dist_title, now.isoformat(), chat_id, BOOK_SESSION, existing_id, BOOK_ROLE),
        )
        conn.commit()
        _set_meta(
            store,
            existing_id,
            book_name=book,
            section=sec,
            title=dist_title,
            body=plain_body,
            body_md=raw_md,
            source_chat_id=chat_id,
            source_msg_id=source_msg_id,
            source_excerpt=source_excerpt or raw_md,
        )
        msgs = {m.turn_id: m for m in store.load_session(BOOK_SESSION)}
        msg = msgs.get(existing_id)
        if not msg:
            raise ValueError("entry missing after update")
        out = _entry_dict(msg, _get_meta(store, existing_id))
        out["updated"] = True
        return out

    msg = Message(
        BOOK_SESSION,
        0,
        BOOK_ROLE,
        dist_title,
        now,
        timezone=tz,
        ttl_class="permanent",
        source_id=chat_id,
    )
    turn = store.insert_next(msg)
    msg.turn_id = turn
    _set_meta(
        store,
        turn,
        book_name=book,
        section=sec,
        title=dist_title,
        body=plain_body,
        body_md=raw_md,
        source_chat_id=chat_id,
        source_msg_id=source_msg_id,
        source_excerpt=source_excerpt or raw_md,
    )
    return _entry_dict(msg, _get_meta(store, turn))


def _registry_row(store: Store, book_name: str) -> dict | None:
    _ensure_meta(store)
    row = store._conn().execute(
        "SELECT book_name, display_title, created_at FROM book_registry WHERE book_name = ?",
        (book_name,),
    ).fetchone()
    if not row:
        return None
    return {"name": row[0], "display_title": row[1], "created_at": row[2]}


def create_book(
    store: Store,
    book_name: str | None,
    now: datetime.datetime,
    *,
    display_title: str | None = None,
) -> dict:
    book = normalize_book_name(book_name)
    _ensure_meta(store)
    conn = store._conn()
    title = (display_title or book).strip()[:TITLE_MAX] or book
    row = conn.execute(
        "SELECT book_name FROM book_registry WHERE book_name = ?",
        (book,),
    ).fetchone()
    if row:
        reg = _registry_row(store, book)
        return {"name": book, "display_title": reg["display_title"] if reg else title, "created_at": reg["created_at"], "existed": True}
    created_at = now.isoformat()
    conn.execute(
        "INSERT INTO book_registry (book_name, display_title, created_at) VALUES (?, ?, ?)",
        (book, title, created_at),
    )
    conn.commit()
    return {"name": book, "display_title": title, "created_at": created_at, "existed": False}


def list_books(store: Store) -> list[dict]:
    _ensure_meta(store)
    books: dict[str, dict] = {}
    for row in store._conn().execute(
        "SELECT book_name, display_title, created_at FROM book_registry"
    ).fetchall():
        books[row[0]] = {
            "name": row[0],
            "display_title": row[1],
            "passage_count": 0,
            "sections": [],
            "created_at": row[2],
            "updated_at": row[2],
        }
    for msg, meta in _iter_passages(store):
        name = meta.get("book_name") or DEFAULT_BOOK
        entry = _entry_dict(msg, meta)
        if name not in books:
            books[name] = {
                "name": name,
                "display_title": None,
                "passage_count": 0,
                "sections": [],
                "created_at": entry["created_at"],
                "updated_at": entry["created_at"],
            }
        rec = books[name]
        rec["passage_count"] += 1
        if entry["created_at"] > rec["updated_at"]:
            rec["updated_at"] = entry["created_at"]
        if not rec.get("created_at") or entry["created_at"] < rec["created_at"]:
            rec["created_at"] = entry["created_at"]
        sec = entry.get("section")
        if sec and sec not in rec["sections"]:
            rec["sections"].append(sec)
    out = sorted(books.values(), key=lambda b: b["updated_at"], reverse=True)
    return out


def get_book(
    store: Store,
    book_name: str,
    *,
    query: str | None = None,
) -> dict | None:
    book = normalize_book_name(book_name)
    raw_passages = list(_iter_passages(store, book))
    if not raw_passages and not _registry_row(store, book):
        return None
    all_passages = [
        _entry_dict(msg, meta, index=i, index_from_end=len(raw_passages) - 1 - i)
        for i, (msg, meta) in enumerate(raw_passages)
    ]
    passages: list[dict] = []
    q = (query or "").strip().lower()
    for entry in all_passages:
        if q:
            hay = f"{entry.get('section') or ''} {entry['title']} {entry['body']}".lower()
            if q not in hay:
                continue
        passages.append(entry)
    sections: list[str] = []
    for p in passages:
        sec = p.get("section")
        if sec and sec not in sections:
            sections.append(sec)
    reg = _registry_row(store, book)
    out = {"name": book, "passages": passages, "sections": sections}
    if reg:
        out["display_title"] = reg.get("display_title")
        out["created_at"] = reg.get("created_at")
    return out


def list_entries(
    store: Store,
    *,
    book_name: str | None = None,
    query: str | None = None,
    limit: int = 200,
) -> list[dict]:
    if book_name:
        doc = get_book(store, book_name, query=query)
        if not doc:
            return []
        passages = doc["passages"]
        if limit:
            passages = passages[-limit:]
        return list(reversed(passages))
    _ensure_meta(store)
    out: list[dict] = []
    q = (query or "").strip().lower()
    for msg, meta in reversed(_iter_passages(store)):
        entry = _entry_dict(msg, meta)
        if q:
            hay = f"{entry.get('section') or ''} {entry['title']} {entry['body']}".lower()
            if q not in hay:
                continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def get_entry(store: Store, turn_id: int) -> dict | None:
    _ensure_meta(store)
    for msg in store.load_session(BOOK_SESSION):
        if msg.turn_id == turn_id and msg.role == BOOK_ROLE:
            return _entry_dict(msg, _get_meta(store, turn_id))
    return None


def resolve_passage(
    store: Store,
    *,
    turn_id: int | None = None,
    book_name: str | None = None,
    position: str | int | None = None,
    title: str | None = None,
) -> dict | None:
    if turn_id is not None:
        entry = get_entry(store, int(turn_id))
        if entry:
            return entry
    book = normalize_book_name(book_name) if book_name else None
    if not book:
        return None
    doc = get_book(store, book)
    if not doc:
        return None
    passages = doc["passages"]
    if not passages:
        return None
    if title:
        q = title.strip().lower()
        for p in reversed(passages):
            if q in (p.get("title") or "").lower():
                return p
    if position is not None:
        pos = str(position).strip().lower()
        if pos == "last":
            return passages[-1]
        if pos == "first":
            return passages[0]
        try:
            idx = int(position)
        except (TypeError, ValueError):
            return None
        if idx < 0:
            idx = len(passages) + idx
        if 0 <= idx < len(passages):
            return passages[idx]
    return None


def update_passage(
    store: Store,
    turn_id: int,
    now: datetime.datetime,
    tz: str = "UTC",
    *,
    title: str | None = None,
    section: str | None = None,
    passage: str | None = None,
    body: str | None = None,
    body_md: str | None = None,
    book_name: str | None = None,
    clear_section: bool = False,
) -> dict:
    _ensure_meta(store)
    entry = get_entry(store, int(turn_id))
    if not entry:
        raise ValueError("passage not found")

    book = normalize_book_name(book_name) if book_name else entry["book_name"]
    sec = normalize_section(section) if section is not None else entry.get("section")
    if clear_section:
        sec = None

    new_title = title.strip() if title else entry["title"]
    raw_md = (body_md or body or passage)
    if raw_md is not None:
        raw_md = raw_md.strip()
        if not raw_md:
            raise ValueError("passage body required")
        if title is None and not (body or body_md or passage):
            new_title = entry["title"]
        elif title is None:
            dist_title, _ = distill_entry(raw_md)
            if dist_title:
                new_title = dist_title
        plain_body = _strip_markdown(raw_md)
        if len(plain_body) > BODY_MAX:
            plain_body = plain_body[: BODY_MAX - 1].rstrip() + "…"
        if len(raw_md) > BODY_MD_MAX:
            cut = raw_md[:BODY_MD_MAX]
            open_fence = cut.rfind("```")
            close_ok = raw_md.find("```", open_fence + 3) if open_fence >= 0 else -1
            if open_fence >= 0 and (close_ok < 0 or close_ok >= BODY_MD_MAX):
                cut = raw_md[:open_fence].rstrip()
            raw_md = cut.rstrip() + ("…" if cut != raw_md else "")
    else:
        plain_body = entry["body"]
        raw_md = entry.get("body_md") or entry["body"]

    if not new_title:
        raise ValueError("title required")

    conn = store._conn()
    conn.execute(
        "UPDATE messages SET content = ?, created_at = ? "
        "WHERE session_id = ? AND turn_id = ? AND role = ?",
        (new_title, now.isoformat(), BOOK_SESSION, int(turn_id), BOOK_ROLE),
    )
    conn.commit()
    _set_meta(
        store,
        int(turn_id),
        book_name=book,
        section=sec,
        title=new_title,
        body=plain_body,
        body_md=raw_md,
        source_chat_id=entry.get("source_chat_id"),
        source_msg_id=entry.get("source_msg_id"),
        source_excerpt=entry.get("source_excerpt"),
    )
    out = get_entry(store, int(turn_id))
    if not out:
        raise ValueError("entry missing after update")
    out["updated"] = True
    return out


def book_context_block(store: Store, max_chars: int = 1800) -> str:
    """Compact book index for LLM context."""
    books = list_books(store)
    if not books:
        return ""
    lines = ["Books (local, cross-session):"]
    used = 0
    for b in books[:6]:
        secs = ", ".join(b["sections"][:4]) if b["sections"] else "—"
        line = f"- {b['name']}: {b['passage_count']} passages; sections: {secs}"
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    lines.append(
        "Use list_books/create_book/read_book/store_in_book/edit_book_passage/delete_book_passage. "
        "read_book returns turn_id and index per passage for precise edits."
    )
    return "\n".join(lines)


def forget_entry(store: Store, turn_id: int) -> bool:
    _ensure_meta(store)
    conn = store._conn()
    conn.execute("DELETE FROM book_meta WHERE turn_id = ?", (turn_id,))
    cur = conn.execute(
        "DELETE FROM messages WHERE session_id = ? AND turn_id = ? AND role = ?",
        (BOOK_SESSION, turn_id, BOOK_ROLE),
    )
    conn.commit()
    return cur.rowcount > 0
