"""minima memory — extract, store, and list user facts via pysince TTL."""

from __future__ import annotations

import datetime
import json
import re
from typing import TYPE_CHECKING

from since.models import EPHEMERAL_TTL, Message

if TYPE_CHECKING:
    from since.store import Store

MEMORY_SESSION = "minima:memory"
MEMORY_ROLE = "memory"

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_meta (
    turn_id         INTEGER PRIMARY KEY,
    superseded_by   INTEGER,
    supersedes      INTEGER,
    source_chat_id  TEXT,
    source_msg_id   TEXT,
    source_excerpt  TEXT
);
"""

SOURCE_EXCERPT_MAX = 320

_EXPLICIT = re.compile(
    r"(?:remember|don't forget|do not forget|note that|keep in mind)\s+(.{4,160})",
    re.I,
)
_PREFERENCE = re.compile(
    r"(?:allergic to|i prefer|i always|i never)\s+(.{4,100})",
    re.I,
)
_PATH = re.compile(r"\b([A-Z]:\\(?:[^\s\\]+\\)*[^\s\\]+)\b")

# Clock references inside facts: "9 pm", "9:30pm", "9 o clock", "21:00"
_CLOCK_SUFFIX = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|o\s*'?\s*clock)\b", re.I)
_CLOCK_BARE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_MIDNIGHT = re.compile(r"\bmidnight\b", re.I)

# A deadline this far in the past is noise — hide the fact entirely.
PASSED_RETENTION = datetime.timedelta(hours=3)
SLOW_PASSED_RETENTION = datetime.timedelta(days=30)
# Context budget — keep temporal block small for the LLM.
CONTEXT_MAX_FACTS = 8
CONTEXT_MAX_CHARS = 96
# If a same-day time is only this far behind fact creation, treat as passed (not tomorrow).
SAME_DAY_PASSED_WINDOW = datetime.timedelta(hours=6)
_FUTURE_HINT = re.compile(
    r"\b(tomorrow|tonight|later today|next week|next month|upcoming|coming up)\b",
    re.I,
)
_MONTH_DAY = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b",
    re.I,
)
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    return text[:160]


def _clean_excerpt(text: str | None) -> str | None:
    if not text:
        return None
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) > SOURCE_EXCERPT_MAX:
        return text[: SOURCE_EXCERPT_MAX - 1] + "…"
    return text


def _ensure_meta(store: Store) -> None:
    conn = store._conn()
    conn.executescript(_META_SCHEMA)
    cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(memory_meta)").fetchall()
    }
    if "source_excerpt" not in cols:
        conn.execute("ALTER TABLE memory_meta ADD COLUMN source_excerpt TEXT")
    conn.commit()


def _get_meta(store: Store, turn_id: int) -> dict:
    _ensure_meta(store)
    row = store._conn().execute(
        "SELECT turn_id, superseded_by, supersedes, source_chat_id, source_msg_id, source_excerpt "
        "FROM memory_meta WHERE turn_id = ?",
        (turn_id,),
    ).fetchone()
    if not row:
        return {}
    return {
        "superseded_by": row[1],
        "supersedes": row[2],
        "source_chat_id": row[3],
        "source_msg_id": row[4],
        "source_excerpt": row[5],
    }


def _set_meta(
    store: Store,
    turn_id: int,
    *,
    superseded_by: int | None = None,
    supersedes: int | None = None,
    source_chat_id: str | None = None,
    source_msg_id: str | None = None,
    source_excerpt: str | None = None,
) -> None:
    _ensure_meta(store)
    conn = store._conn()
    existing = _get_meta(store, turn_id)
    conn.execute(
        "INSERT INTO memory_meta (turn_id, superseded_by, supersedes, source_chat_id, source_msg_id, source_excerpt) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(turn_id) DO UPDATE SET "
        "superseded_by = COALESCE(excluded.superseded_by, memory_meta.superseded_by), "
        "supersedes = COALESCE(excluded.supersedes, memory_meta.supersedes), "
        "source_chat_id = COALESCE(excluded.source_chat_id, memory_meta.source_chat_id), "
        "source_msg_id = COALESCE(excluded.source_msg_id, memory_meta.source_msg_id), "
        "source_excerpt = COALESCE(excluded.source_excerpt, memory_meta.source_excerpt)",
        (
            turn_id,
            superseded_by if superseded_by is not None else existing.get("superseded_by"),
            supersedes if supersedes is not None else existing.get("supersedes"),
            source_chat_id if source_chat_id is not None else existing.get("source_chat_id"),
            source_msg_id if source_msg_id is not None else existing.get("source_msg_id"),
            source_excerpt if source_excerpt is not None else existing.get("source_excerpt"),
        ),
    )
    conn.commit()


def _supersede_fact(
    store: Store,
    old_turn_id: int,
    new_turn_id: int,
    now: datetime.datetime,
) -> None:
    conn = store._conn()
    conn.execute(
        "UPDATE messages SET invalidated_at = ? "
        "WHERE session_id = ? AND turn_id = ? AND role = ?",
        (now.isoformat(), MEMORY_SESSION, old_turn_id, MEMORY_ROLE),
    )
    _set_meta(store, old_turn_id, superseded_by=new_turn_id)
    _set_meta(store, new_turn_id, supersedes=old_turn_id)
    conn.commit()


def _fact_dict(msg: Message, now: datetime.datetime, tz: str, meta: dict) -> dict:
    timing = fact_timing(msg, now, tz)
    tz_name = msg.timezone if msg.timezone and msg.timezone != "UTC" else tz
    created_local = _utc_to_local(msg.created_at, tz_name)
    target = _resolve_target(msg, created_local)
    out = {
        "turn_id": msg.turn_id,
        "content": msg.content,
        "ttl_class": msg.ttl_class,
        "ttl_label": _ttl_label(msg, now),
        "when": timing["when"],
        "passed": timing["passed"],
        "expired": timing["expired"],
        "created_at": msg.created_at.isoformat(),
        "chat_id": msg.source_id,
        "source_chat_id": meta.get("source_chat_id") or msg.source_id,
        "source_msg_id": meta.get("source_msg_id"),
        "source_excerpt": meta.get("source_excerpt"),
        "supersedes": meta.get("supersedes"),
        "superseded_by": meta.get("superseded_by"),
        "superseded": msg.invalidated_at is not None,
    }
    if target is not None:
        out["target_at"] = target.isoformat()
        out["target_label"] = target.strftime("%I:%M %p").lstrip("0").replace(" 0", " ")
        out["target_hour"] = target.hour + target.minute / 60.0
    return out


def _utc_to_local(dt: datetime.datetime, tz_name: str) -> datetime.datetime:
    from since.freshness import _utc_to_local as conv
    return conv(dt, tz_name)


def _resolve_clock_candidate(
    ch: int,
    minute: int,
    created_local: datetime.datetime,
    content: str,
) -> datetime.datetime | None:
    if not 0 <= ch <= 23 or not 0 <= minute <= 59:
        return None
    grace = created_local - datetime.timedelta(minutes=5)
    t = created_local.replace(hour=ch, minute=minute, second=0, microsecond=0)
    if t >= grace:
        return t
    gap = grace - t
    if gap <= SAME_DAY_PASSED_WINDOW:
        return t
    if _FUTURE_HINT.search(content):
        return t + datetime.timedelta(days=1)
    return t + datetime.timedelta(days=1)


def parse_fact_time(content: str, created_local: datetime.datetime) -> datetime.datetime | None:
    """Resolve a clock reference in a fact to a concrete local datetime.

    Ambiguous hours ("9 o clock", "7:30") try both am/pm; we pick the
    candidate closest to when the fact was recorded. Same-day times that
    already passed recently stay on today (marked passed) instead of rolling
    to tomorrow.
    """
    candidates_h: list[int] = []
    minute = 0

    m = _CLOCK_SUFFIX.search(content)
    if m:
        h = int(m.group(1)) % 12
        minute = int(m.group(2) or 0)
        suffix = m.group(3).lower()
        if suffix.startswith("am"):
            candidates_h = [h]
        elif suffix.startswith("pm"):
            candidates_h = [h + 12]
        else:  # "o clock" — ambiguous
            candidates_h = [h, h + 12]
    elif _MIDNIGHT.search(content):
        candidates_h = [0]
        minute = 0
    else:
        m = _CLOCK_BARE.search(content)
        if not m:
            return None
        h = int(m.group(1))
        minute = int(m.group(2))
        candidates_h = [h] if h > 12 else [h, h + 12]

    resolved: list[datetime.datetime] = []
    for ch in candidates_h:
        t = _resolve_clock_candidate(ch, minute, created_local, content)
        if t is not None:
            resolved.append(t)
    if not resolved:
        return None
    return min(resolved, key=lambda t: abs((t - created_local).total_seconds()))


def resolve_event_datetime(
    content: str, created_local: datetime.datetime
) -> datetime.datetime | None:
    """Resolve calendar refs (July 9, tomorrow) plus optional clock in a fact."""
    base_date: datetime.date | None = None

    if _FUTURE_HINT.search(content):
        base_date = (created_local + datetime.timedelta(days=1)).date()

    m = _MONTH_DAY.search(content)
    if m:
        month_key = m.group(1).lower().rstrip(".")
        month = _MONTHS.get(month_key)
        if month:
            day = int(m.group(2))
            year = int(m.group(3)) if m.group(3) else created_local.year
            try:
                d = datetime.date(year, month, day)
                if not m.group(3) and d < created_local.date() - datetime.timedelta(days=14):
                    d = datetime.date(year + 1, month, day)
                base_date = d
            except ValueError:
                pass

    if base_date is None:
        return None

    clock = parse_fact_time(content, created_local)
    if clock is not None:
        return datetime.datetime.combine(
            base_date, clock.time(), tzinfo=created_local.tzinfo
        )
    return datetime.datetime.combine(
        base_date, datetime.time(23, 59), tzinfo=created_local.tzinfo
    )


def _resolve_target(msg: Message, created_local: datetime.datetime) -> datetime.datetime | None:
    content = msg.content or ""
    event = resolve_event_datetime(content, created_local)
    if event is not None:
        return event
    return parse_fact_time(content, created_local)


def _fmt_delta(td: datetime.timedelta) -> str:
    mins = int(abs(td.total_seconds()) // 60)
    if mins < 1:
        return "now"
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def fact_timing(msg: Message, now: datetime.datetime, tz: str) -> dict:
    """Deterministic deadline verdict — the LLM never does this arithmetic."""
    tz_name = msg.timezone if msg.timezone and msg.timezone != "UTC" else tz
    created_local = _utc_to_local(msg.created_at, tz_name)
    now_local = _utc_to_local(now, tz_name)
    target = _resolve_target(msg, created_local)
    if target is None:
        return {"when": None, "passed": False, "expired": False}
    delta = target - now_local
    if delta.total_seconds() >= 0:
        return {"when": f"in {_fmt_delta(delta)}", "passed": False, "expired": False}
    retention = SLOW_PASSED_RETENTION if msg.ttl_class == "slow" else PASSED_RETENTION
    return {
        "when": f"passed {_fmt_delta(delta)} ago",
        "passed": True,
        "expired": -delta > retention,
    }


def extract_facts(text: str, now: datetime.datetime) -> list[tuple[str, str]]:
    """Return (content, ttl_class) pairs from user text.

    Only high-confidence patterns — deadlines/plans go through the remember tool.
    """
    if not text or len(text.strip()) < 8:
        return []

    facts: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(content: str, ttl: str) -> None:
        content = _clean(content)
        key = content.lower()
        if len(content) < 6 or key in seen:
            return
        seen.add(key)
        facts.append((content, ttl))

    for m in _EXPLICIT.finditer(text):
        add(m.group(1).rstrip(".,!?"), "permanent")

    for m in _PREFERENCE.finditer(text):
        add(m.group(0), "permanent")

    for m in _PATH.finditer(text):
        path = m.group(1)
        if path.count("\\") >= 1:
            add(f"Working with {path}", "slow")

    return facts[:6]


def _ttl_label(msg: Message, now: datetime.datetime) -> str:
    if msg.ttl_class == "permanent":
        return "always"
    if msg.ttl_class == "ephemeral":
        age = now - msg.created_at
        left = EPHEMERAL_TTL - age
        if left.total_seconds() <= 0:
            return "expired"
        mins = max(1, int(left.total_seconds() // 60))
        return f"{mins}m"
    if msg.ttl_class == "slow":
        return "session"
    return msg.ttl_class


def _is_active(msg: Message, now: datetime.datetime) -> bool:
    if msg.role != MEMORY_ROLE:
        return False
    if msg.invalidated_at is not None:
        return False
    if msg.ttl_class == "ephemeral":
        return (now - msg.created_at) <= EPHEMERAL_TTL
    return True


def _is_active_at(msg: Message, as_of: datetime.datetime) -> bool:
    """Whether a fact existed and was believed at a past instant."""
    if msg.role != MEMORY_ROLE:
        return False
    if msg.created_at > as_of:
        return False
    if msg.invalidated_at is not None and msg.invalidated_at <= as_of:
        return False
    if msg.ttl_class == "ephemeral":
        return (as_of - msg.created_at) <= EPHEMERAL_TTL
    return True


def list_facts_as_of(
    store: Store,
    as_of: datetime.datetime,
    tz: str = "UTC",
) -> list[dict]:
    """Facts believed at as_of, with deadline verdicts as of that moment."""
    _ensure_meta(store)
    msgs = store.load_session(MEMORY_SESSION)
    out: list[dict] = []
    for msg in reversed(msgs):
        if not _is_active_at(msg, as_of):
            continue
        timing = fact_timing(msg, as_of, tz)
        if timing["expired"] and msg.ttl_class != "permanent":
            continue
        meta = _get_meta(store, msg.turn_id)
        out.append(_fact_dict(msg, as_of, tz, meta))
    return out


def list_facts(store: Store, now: datetime.datetime, tz: str = "UTC") -> list[dict]:
    _ensure_meta(store)
    msgs = store.load_session(MEMORY_SESSION)
    out: list[dict] = []
    for msg in reversed(msgs):
        if not _is_active(msg, now):
            continue
        timing = fact_timing(msg, now, tz)
        if timing["expired"] and msg.ttl_class != "permanent":
            continue
        meta = _get_meta(store, msg.turn_id)
        out.append(_fact_dict(msg, now, tz, meta))
    return out


def fact_provenance(store: Store, turn_id: int, now: datetime.datetime, tz: str = "UTC") -> dict:
    """Return a fact and its supersession chain (receipts)."""
    _ensure_meta(store)
    msgs = {m.turn_id: m for m in store.load_session(MEMORY_SESSION) if m.role == MEMORY_ROLE}
    if turn_id not in msgs:
        return {"ok": False, "error": "not found"}
    chain: list[dict] = []
    current_id: int | None = turn_id
    seen: set[int] = set()
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        msg = msgs.get(current_id)
        if not msg:
            break
        meta = _get_meta(store, current_id)
        entry = _fact_dict(msg, now, tz, meta)
        entry["active"] = msg.invalidated_at is None and _is_active(msg, now)
        chain.append(entry)
        current_id = meta.get("supersedes")
    chain.reverse()
    return {"ok": True, "turn_id": turn_id, "chain": chain}


def _dedupe_exists(store: Store, content: str) -> bool:
    key = content.lower()
    for msg in store.last_n(MEMORY_SESSION, 40):
        if (
            msg.role == MEMORY_ROLE
            and msg.invalidated_at is None
            and msg.content.lower() == key
        ):
            return True
    return False


def store_facts(
    store: Store,
    facts: list[tuple[str, str]],
    now: datetime.datetime,
    tz: str = "UTC",
    chat_id: str | None = None,
    source_msg_id: str | None = None,
    source_excerpt: str | None = None,
) -> list[dict]:
    added: list[dict] = []
    excerpt = _clean_excerpt(source_excerpt)
    for content, ttl in facts:
        if _dedupe_exists(store, content):
            continue
        msg = Message(
            MEMORY_SESSION,
            0,
            MEMORY_ROLE,
            content,
            now,
            timezone=tz,
            ttl_class=ttl,
            source_id=chat_id,
        )
        turn = store.insert_next(msg)
        _set_meta(
            store,
            turn,
            source_chat_id=chat_id,
            source_msg_id=source_msg_id,
            source_excerpt=excerpt,
        )
        added.append({
            "turn_id": turn,
            "content": content,
            "ttl_class": ttl,
            "ttl_label": _ttl_label(msg, now),
        })
    return added


def pin_fact(
    store: Store,
    content: str,
    now: datetime.datetime,
    tz: str = "UTC",
    ttl_class: str = "permanent",
    chat_id: str | None = None,
    source_msg_id: str | None = None,
    source_excerpt: str | None = None,
    revises_turn_id: int | None = None,
) -> dict:
    content = _clean(content)
    if not content:
        raise ValueError("content required")
    if ttl_class not in ("permanent", "slow", "ephemeral"):
        ttl_class = "permanent"
    _ensure_meta(store)

    if revises_turn_id is not None:
        old_meta = _get_meta(store, revises_turn_id)
        if source_msg_id is None:
            source_msg_id = old_meta.get("source_msg_id")
        if source_excerpt is None:
            source_excerpt = old_meta.get("source_excerpt")
        if chat_id is None:
            chat_id = old_meta.get("source_chat_id")

    for msg in reversed(store.load_session(MEMORY_SESSION)):
        if msg.role == MEMORY_ROLE and msg.content.lower() == content.lower():
            if msg.invalidated_at is None:
                meta = _get_meta(store, msg.turn_id)
                out = _fact_dict(msg, now, tz, meta)
                out["duplicate"] = True
                return out

    msg = Message(
        MEMORY_SESSION,
        0,
        MEMORY_ROLE,
        content,
        now,
        timezone=tz,
        ttl_class=ttl_class,
        source_id=chat_id,
    )
    turn = store.insert_next(msg)
    msg.turn_id = turn
    _set_meta(
        store,
        turn,
        source_chat_id=chat_id,
        source_msg_id=source_msg_id,
        source_excerpt=_clean_excerpt(source_excerpt),
    )
    if revises_turn_id is not None and revises_turn_id != turn:
        _supersede_fact(store, revises_turn_id, turn, now)

    meta = _get_meta(store, turn)
    out = _fact_dict(msg, now, tz, meta)
    if revises_turn_id is not None:
        out["revised"] = revises_turn_id
    return out


def forget_fact(store: Store, turn_id: int) -> bool:
    conn = store._conn()
    cur = conn.execute(
        "DELETE FROM messages WHERE session_id = ? AND turn_id = ? AND role = ?",
        (MEMORY_SESSION, turn_id, MEMORY_ROLE),
    )
    conn.commit()
    return cur.rowcount > 0


def _memory_context_priority(f: dict) -> tuple[int, int]:
    """Upcoming deadlines first, then permanent prefs, then passed, then rest."""
    has_target = f.get("target_at") is not None
    if has_target and not f.get("passed"):
        return (0, f.get("turn_id") or 0)
    if f.get("ttl_class") == "permanent":
        return (1, f.get("turn_id") or 0)
    if has_target and f.get("passed"):
        return (2, f.get("turn_id") or 0)
    return (3, f.get("turn_id") or 0)


def _compact_fact_for_context(f: dict) -> dict:
    content = f.get("content") or ""
    if len(content) > CONTEXT_MAX_CHARS:
        content = content[: CONTEXT_MAX_CHARS - 1] + "…"
    out: dict = {"id": f["turn_id"], "c": content}
    if f.get("target_at"):
        out["s"] = "passed" if f.get("passed") else "upcoming"
        if f.get("when"):
            out["w"] = f["when"]
        if f.get("target_label"):
            out["at"] = f["target_label"]
    elif f.get("ttl_class") == "permanent":
        out["ttl"] = "p"
    return out


def memory_block(store: Store, now: datetime.datetime, tz: str = "UTC") -> str:
    """Compact JSON memory for LLM context — capped and priority-sorted."""
    facts = list_facts(store, now, tz)
    if not facts:
        return ""
    ranked = sorted(facts, key=_memory_context_priority)[:CONTEXT_MAX_FACTS]
    payload = [_compact_fact_for_context(f) for f in ranked]
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        "Active memory (JSON; trust s/w - never recalc deadlines): "
        + blob
    )


def sync_from_messages(
    store: Store,
    messages: list[str],
    now: datetime.datetime,
    tz: str = "UTC",
    chat_id: str | None = None,
) -> int:
    count = 0
    for text in messages:
        if not text or not text.strip():
            continue
        added = store_facts(store, extract_facts(text, now), now, tz, chat_id)
        count += len(added)
    return count
