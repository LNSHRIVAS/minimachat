# minimachat (minima)

**A local chat with a memory that stays honest.** Deadlines strike themselves through when they pass. You can scrub the page back to see what it believed an hour ago. The good parts of a conversation get saved as living documents you own - not a scroll you'll never find again.

Most chats forget. Worse, they keep showing you things that stopped being true. minima remembers *honestly*, on your machine, with your keys.

<img width="1920" alt="minima interface" src="https://github.com/user-attachments/assets/f551df0f-9030-4594-9dc6-b3730e5825d9" />


---

## What makes it different

Every other local chat leads with "ChatGPT, but private." minima is built around one idea nobody else has: **your past stays true.**

- **Temporal memory that ages.** Facts carry time. A deadline that passes gets struck through - visibly, honestly - instead of sitting there as a stale claim. Powered by [pysince](https://github.com/LNSHRIVAS/since).
- **Scrub through what it believed.** Drag the timeline to see the memory and the page as they were an hour, a day, or a week ago. Your assistant's mind, at any point in time.
- **Books, not lost scrolls.** Tell minima to save an answer and it distills it into a continuous document you own - with a receipt back to the exact moment it was said. Reference a book from any chat.
- **Agentic, on your files.** It reads, writes, edits, and searches your local filesystem, runs commands, and searches the web - all locally, with a dumb-and-reliable agent loop that never runs away.
- **Yours to shape.** Local, BYOK, hackable, MIT-licensed. No cloud, no accounts, no telemetry. A personal chat you can actually make your own.

---

## Quick start

**Requirements:** Windows 10+, [Python 3.10+](https://www.python.org/downloads/), PowerShell

> **Note:** minima is currently **Windows-only** - the server is PowerShell. Cross-platform support (a Python server port) is the most-wanted contribution - see [Contributing](#contributing).

```powershell
# 1. Clone
git clone https://github.com/LNSHRIVAS/minimachat.git
cd minimachat

# 2. Python deps (memory + web search)
pip install -r requirements.txt

# 3. Start server
.\start.bat
# or: powershell -ExecutionPolicy Bypass -File .\server.ps1
```

Open **http://localhost:8081** (use `localhost`, not `127.0.0.1`).

Click **Keys** to configure your API endpoint and model, then save. minima works with any OpenAI-compatible API - bring whatever model you like.

---

## How it works

minima is a single-page UI that talks directly to your LLM API. A local PowerShell server handles filesystem access, web search, and the temporal store; everything runs on your machine. Connection settings live in your browser - no build step, no accounts.

- **Memory** (left panel) - facts the agent remembers, with time-aware status and receipts back to where each came from.
- **When** (timeline) - scrub to see what was believed at any past moment.
- **Books** - distilled, continuous documents that capture the good parts of your chats and stay referenceable across sessions.
- **Files / Workspace** - point it at a project folder and it becomes the agent's workspace.

### Workspace

Open **Files** and navigate to your project folder - that becomes the workspace. New files and PNG exports go under `{workspace}/minima-exports/`.

### Token budget

In **Keys**, the token budget slider sets how many tokens each agent run may consume before pausing:

| Preset | Limit |
|--------|-------|
| 25k – 1M | Fixed cap per run |
| ∞ | No token limit |

Turn safety (64 model calls) and a 10-minute wall clock still apply when unlimited.

---

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for setup, pull request expectations, and where help is most useful.

minima is meant to be **yours to shape**, and contributions are genuinely welcome - this is an open project I want people to build on and make their own.

**Best places to start:**
- **Cross-platform support** - the server is PowerShell today; a Python port would open minima to macOS and Linux. This is the highest-impact contribution.
- Check issues tagged **`good-first-issue`** for small, well-scoped tasks.
- Read **[ARCHITECTURE.md](ARCHITECTURE.md)** to understand how the pieces fit together before diving in.

If you open an issue or PR, I'll try to respond quickly and warmly - even a bug report or a "here's what confused me" is valuable. If you build a personal fork or a theme, I'd love to see it.

---

## Project layout

| File | Purpose |
|------|---------|
| `index.html` | UI shell, chat, memory, books |
| `server.ps1` | Local HTTP server (port 8081) + `/api/*` routes |
| `start.bat` | One-click server launcher |
| `minima-harness.js` | Agent loop (model → tools → ledger) |
| `minima-tools.js` | Tool schemas + API adapters |
| `minima-events.js` | Run activity UI + optional code panel |
| `since_bridge.py` | pysince / temporal context bridge |
| `memory.py`, `book.py` | Memory facts + book passages |
| `search_bridge.py` | DuckDuckGo web search (no key) |
| `svg_to_img.py` | Optional PNG export for diagrams |

## Verify

```powershell
node harness-tests.js
```

Expect: `harness-tests: 11 passed, 0 failed`

## License

MIT - see upstream components for their licenses ([pysince](https://github.com/LNSHRIVAS/since), CDN libraries in `index.html`).
