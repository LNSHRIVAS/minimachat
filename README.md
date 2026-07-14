# minimachat (minima)

Local **bring-your-own-key** agentic chat for Windows. A single-page UI talks to your LLM API, runs tools against your filesystem, and keeps temporal memory via [pysince](https://github.com/LNSHRIVAS/since).

No build step. Connection settings are saved in your browser.

<img width="1920" height="384" alt="White Travel Reddit Banner (2)" src="https://github.com/user-attachments/assets/f551df0f-9030-4594-9dc6-b3730e5825d9" />

## Quick start

**Requirements:** Windows 10+, [Python 3.10+](https://www.python.org/downloads/), PowerShell

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

Click **Keys** to configure your API endpoint and model, then save.

## Token budget

In **Keys**, the token budget slider sets how many tokens each agent run may consume before pausing:

| Preset | Limit |
|--------|-------|
| 25k – 1M | Fixed cap per run |
| ∞ | No token limit |
<img width="1920" height="384" alt="White Travel Reddit Banner (2)" src="https://github.com/user-attachments/assets/3cf4cd4e-0f1a-4419-885c-8954e05ffdae" />

Turn safety (64 model calls) and a 10-minute wall clock still apply when unlimited.

## Verify

```powershell
node harness-tests.js
```

Expect: `harness-tests: 11 passed, 0 failed`

## What’s included

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

## Workspace

Open **Files** and navigate to your project folder — that becomes the workspace. New files and PNG exports go under `{workspace}/minima-exports/`.

## License

MIT — see upstream components for their licenses (`pysince`, CDN libraries in `index.html`).
