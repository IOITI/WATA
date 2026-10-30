# WATA research project for Claude desktop

What to give a Claude desktop project so that its chats know WATA, its trading style, its turbos and data, how to judge a backtest, and what has already been tried, before they explore indicators and strategies.

| File | Where it goes |
|---|---|
| `project-instructions.md` | The project's **Instructions**: paste the whole file |
| `knowledge/01-wata-system.md` … `knowledge/07-glossary.md` | The project's **Files** (project knowledge) |

## Setting it up

1. Open the project's instructions in Claude desktop and paste `project-instructions.md`.
2. Add the seven files of `knowledge/` to the project.
3. In a chat of the project, check that the `wata` tools are on (the chat's tools menu). The desktop app already declares the server (`claude_desktop_config.json`, through `mcp-remote`), and reaches it while the Mac's WireGuard tunnel to the server is up. Ask it to run `status`: an answer with the server's version means it works.

Without the tools, Claude can still discuss and write Pine. With them, it runs studies and backtests on the live server's data. That is research only: nothing there trades, changes the configuration or takes a rule live.

## Keeping it current

- **The memory is `knowledge/06-research-memory.md`.** At the end of a research chat, say "update the research memory". Claude writes the entries to add, in that file's format. Add them, then upload the file again in place of the old one.
- What every Claude should see goes into WATA itself. The experiment journal is the server's: any client of the MCP server reads it with `exp_get`. An entry of the AI agent's memory (admin › AI agent › Memory) is read by this project, WATA's agent and Claude Code alike.
- The other files describe WATA at v0.62.0 (30 Oct 2026). After a release that changes the rules, the data or the tools, ask Claude Code in this repository to bring them up to date.

The files hold no host, token, key or account number. Keep it so.
