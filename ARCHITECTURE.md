# Architecture

minimachat (minima) is a local, browser-based agent chat. The UI runs in your browser; a small PowerShell server on `localhost:8081` exposes filesystem, search, and temporal-memory APIs. The LLM is called directly from the browser using connection settings you configure under **Keys**.

There is no build step. Static assets are served as-is.

---

## System overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (index.html + JS modules)                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Chat + panels│  │ minima-      │  │ minima-tools.js      │ │
│  │ memory, books│  │ harness.js   │  │ (tool → /api/*)      │ │
│  └──────────────┘  └──────┬───────┘  └──────────┬───────────┘ │
│                           │                      │             │
│                           │  HTTPS/fetch to      │             │
│                           │  your LLM endpoint   │             │
└───────────────────────────┼──────────────────────┼─────────────┘
                            │                      │
                            ▼                      ▼
                   OpenAI-compatible API    localhost:8081
                   (BYOK, from browser)       server.ps1
                                                      │
                                    ┌─────────────────┼─────────────────┐
                                    ▼                 ▼                 ▼
                              /api/fs/*        /api/since/*      /api/web/search
                              filesystem       Python bridges     search_bridge.py
                                                 since_bridge.py
                                                 memory.py, book.py
                                                      │
                                                      ▼
                                              ~/.minima/since.db
                                              (SQLite via pysince)
```

**Request path for one user message**

1. User sends text → `index.html` records the turn and fetches temporal context once (`/api/since/context`).
2. `minima-harness.js` runs a bounded loop: build messages → stream model → if tool calls, execute via `minima-tools.js` → repeat until the model replies without tools or a limit is hit.
3. Tool results and compact run metadata are persisted on the assistant message in `localStorage` (chat history), not full tool payloads.
4. Memory panel and timeline read from `/api/since/memory` and related routes; books use `/api/since/book/*`.

---

## Frontend modules

| Module | Role |
|--------|------|
| `index.html` | Shell: chat UI, memory/when/books/files panels, settings, workspace, bootstrap wiring |
| `minima-harness.js` | Generic model→tool loop, message assembly, run state, pause/stop/continue |
| `minima-tools.js` | OpenAI-style tool schemas + `execTool()` adapters to `/api/*` |
| `minima-events.js` | In-message activity UI, optional code panel, paused-run affordances |
| `minima-events.css`, `minima-workspace.css`, `botanical.css` | Layout and skin styles |
| `harness-tests.js` | Deterministic harness tests (`node harness-tests.js`) |

`index.html` still owns some book-specific `execTool` branches and UI glue that depend on panel state. New tools should prefer `minima-tools.js` plus a matching `server.ps1` route.

---

## Agent harness

The harness in `minima-harness.js` is intentionally **linear and bounded** — no hidden sub-agents, no recursive planner.

### Loop

1. Freeze **temporal context** for the run (max ~2 KB injected into system prompt).
2. Build a protocol-valid message list: system contract, last four **final** chat turns, compact **run ledger** (max ~3 KB), and at most one open assistant+tool cycle.
3. One model call. If the response has no tool calls → status `final`.
4. Otherwise execute tools **sequentially**, append a one-line summary per tool to the ledger, compact the completed cycle, and loop.
5. Stop when any limit triggers → status `paused` (user can **Continue**), or user abort → `stopped`.

### Limits

| Limit | Default | Notes |
|-------|---------|--------|
| Token budget | 100k (slider: 25k–1M or unlimited) | Per run; configurable in **Keys** |
| Model calls | 64 | Safety cap (`MAX_TURN_SAFETY`) |
| Wall clock | 10 minutes | Per run |
| Tool result size | 8 KB | Truncated in harness |
| Ledger | 3 KB | Summaries only, not raw tool bodies |

### Run object

Each assistant message may carry a compact `run`:

`id`, `status`, `terminalReason`, `turn`, `ledger[]`, `events[]`, `metrics`

Bulky tool transcripts are **not** stored in chat history. Refresh mid-run shows a paused/interrupted state with **Continue** (`Minima.harness.run` with `resumeRun`).

---

## Server (`server.ps1`)

PowerShell `HttpListener` on **`http://localhost:8081/`** only (not `127.0.0.1`). Serves static files from the repo root and implements JSON/text API routes.

### Filesystem (`/api/fs/*`)

Enforced caps live in the server, not only in the client:

| Route | Purpose |
|-------|---------|
| `list`, `read`, `head`, `mtime`, `grep` | Bounded reads and search |
| `find` | Workspace-scoped filename search |
| `write`, `append`, `edit` | Writes; create-only unless `overwrite:true` |
| `mkdir`, `move`, `copy`, `delete` | Path operations |
| `image` | Serve local images for viewport |
| `search` | Drive-wide filename search (discouraged for agents) |

`read_file` requires `offset` + `limit` (max 160 lines / 8 KB). Responses include `X-Total-Lines`, `X-Next-Offset`, and `X-Content-Hash` where applicable.

### Other routes

| Prefix | Handler |
|--------|---------|
| `/api/workspace` | Current workspace root and export path |
| `/api/run` | Shell command execution (stdout/stderr capped) |
| `/api/web/search` | `search_bridge.py` → DuckDuckGo via `ddgs` |
| `/api/since/*` | `since_bridge.py` → pysince store, memory, books, diagrams |
| `/api/diagram/export` | SVG → PNG via `svg_to_img.py` |

Python bridges are subprocesses: JSON on stdin, JSON on stdout.

---

## Python layer

| File | Role |
|------|------|
| `since_bridge.py` | Command router for pysince: context, record, stamp, staleness, memory, books, diagram export |
| `memory.py` | Active memory facts, TTL classes, provenance, scrub rendering |
| `book.py` | Named books, passages, embed hooks for chat assets |
| `search_bridge.py` | Web search (no API key) |
| `svg_to_img.py` | Optional PNG rasterization for saved diagrams |

**Datastore:** `~/.minima/since.db` (SQLite, managed by [pysince](https://github.com/LNSHRIVAS/since)).

Temporal context collapses stale file beliefs before injection so the prompt stays small. File reads can be **stamped** so later edits invalidate outdated assumptions.

---

## Temporal memory & books

**Memory** — Facts the agent stores via `remember` with TTL classes (`permanent`, `slow`, `ephemeral`). The left panel shows live status; deadlines that pass are struck through. **When** scrubs the timeline so you can see what was believed at an earlier time.

**Books** — Long-form distillations via `store_in_book` and related tools. Passages keep receipt links back to the chat turn they came from. Books are session-scoped in the store and readable across chats.

**Workspace** — Set by navigating the **Files** panel. The harness receives `workspace` + `exports` paths in context. Agent saves should go under the workspace; PNG exports default to `{workspace}/minima-exports/`.

---

## UI panels

| Panel | Data source |
|-------|-------------|
| Memory | `/api/since/memory` |
| When (scrubber) | Memory facts at scrub timestamp |
| Books | `/api/since/book` |
| Files | `/api/fs/list`, `/api/drives` |
| History | `localStorage` (`minima-chats`) |
| Code panel | In-memory file registry from run events (opens on user click) |

Skins: **botanical** (default) and **classic**; **night** theme via `data-theme`.

---

## Persistence model

| What | Where |
|------|--------|
| Connection settings | Browser `localStorage` (`m`) |
| Chat transcripts + compact runs | Browser `localStorage` (`minima-chats`) |
| Drafts, theme, workspace | Browser `localStorage` |
| Temporal turns, memory, books | `~/.minima/since.db` |

Chats are per-browser, not synced. The temporal store is per-machine.

---

## Verification

```powershell
node harness-tests.js
```

Covers OpenAI tool-protocol validation, a one-write agent scenario (≤2 model calls), byte caps, and token-budget helpers. Extend this file when changing harness contracts.

---

## Extension points

| Goal | Likely touch points |
|------|---------------------|
| New agent tool | `minima-tools.js` schema + `execTool`, `server.ps1` route, harness prompt line if needed |
| Cross-platform server | Port `server.ps1` routes to Python (top community ask) |
| Memory behavior | `memory.py`, `since_bridge.py` |
| Book format | `book.py`, book UI in `index.html` |
| Activity / code UI | `minima-events.js`, activity helpers in `index.html` |

The `pulse/` directory holds an experimental proactive layer; it is not wired into the live UI.

---

## Dependencies

- **Runtime:** Windows PowerShell, Python 3.10+
- **Python packages:** `pysince`, `ddgs` (see `requirements.txt`)
- **Browser CDN:** marked, DOMPurify, KaTeX (loaded from `index.html`)

No Node.js required except for running `harness-tests.js`.
