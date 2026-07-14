# Contributing

Thanks for taking an interest in minima. This project is meant to be local, hackable, and yours to shape — contributions and forks are welcome.

## Before you start

1. Read **[ARCHITECTURE.md](ARCHITECTURE.md)** for how the browser UI, harness, server, and Python bridges fit together.
2. Follow the **[README](README.md)** quick start to run the app locally.
3. Run the harness tests:

   ```powershell
   node harness-tests.js
   ```

   You should see `11 passed, 0 failed`.

## Environment

| Requirement | Notes |
|-------------|--------|
| Windows 10+ | Server is PowerShell today |
| Python 3.10+ | `pip install -r requirements.txt` |
| Node.js | Optional; only for `harness-tests.js` |
| LLM endpoint | Any OpenAI-compatible API, configured in the UI under **Keys** |

> **Platform note:** minima is Windows-only until the server is ported. A **Python server** that mirrors `server.ps1` routes is the highest-impact contribution and would unlock macOS and Linux.

## How to contribute

### Report a bug or idea

Open a [GitHub issue](https://github.com/LNSHRIVAS/minimachat/issues). Include:

- What you expected vs what happened
- Steps to reproduce (if applicable)
- Windows version, Python version, and browser

Rough notes and “this confused me” reports are useful too.

### Pick up work

- Look for issues labeled **`good-first-issue`** for small, scoped tasks.
- Larger features (cross-platform server, new tool families, book UX) — comment on an issue first so effort aligns with maintainers.

### Pull requests

1. Fork and branch from `master`.
2. Keep changes focused; match existing style in the files you touch.
3. Run `node harness-tests.js` before opening the PR.
4. Describe **what** changed and **why** in the PR body.
5. If you add a tool or API route, update **ARCHITECTURE.md** when behavior is non-obvious.

There is no formal CLA. By contributing, you agree your work is licensed under the same terms as the project (MIT).

## Code conventions

- **JavaScript:** IIFE modules attached to `global.Minima`; no bundler. Prefer small, explicit functions over heavy abstraction.
- **Python:** Standard library + existing bridge patterns in `since_bridge.py`.
- **Server:** New filesystem behavior belongs in `server.ps1` with caps documented in ARCHITECTURE.md.
- **UI:** `index.html` is large by design; isolate new UI in separate CSS/JS files when practical.
- **Commits:** Short, neutral subject lines (e.g. “Add workspace hint to list_files”, “Fix activity dedupe on tool finish”). One logical change per commit when possible.

## High-value areas

| Area | Why it matters |
|------|----------------|
| **Cross-platform server** | Removes the Windows-only barrier |
| **Harness & tools** | Agent reliability and token efficiency |
| **Memory / timeline UX** | Core differentiator — honest temporal memory |
| **Books** | Long-lived documents from chat |
| **Tests** | `harness-tests.js` and future server tests |
| **Docs** | README, ARCHITECTURE, and inline comments for strangers |

## Personal forks and themes

You do not need permission to fork, theme, or run a private variant. If you build something interesting, consider opening an issue or discussion to share it — but there is no obligation.

## Questions

Open an issue with the **question** label or start a discussion on the repository. Responses may not be instant, but thoughtful reports and PRs are appreciated.
