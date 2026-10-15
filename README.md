# Zebra BI for AI assistants

Zebra BI draws business data as interactive IBCS-style charts and tables, inline in the conversation. This repository packages the Zebra BI MCP server with two skills that teach the assistant how to use it, as a plugin for Claude and for GitHub Copilot in VS Code, in the Agent Plugins format that ChatGPT and Codex also read.

The server has one tool, `render_visual`. The assistant sends it aggregated figures, such as actuals with a previous year, plan or forecast, and Zebra BI draws the variances. Hosts that support MCP Apps show the interactive visual; the others show a text summary of what was drawn.

## Skills

1. `chart-from-data`: when to show findings as a visual, and how to shape the data for `render_visual`.
2. `variance-analysis`: how to explain actuals against previous year, previous month, plan or forecast, and show the variances.

## Install

The server URL is `https://test.zebra-ai.com/mcp`, the staging server, until the production server is live.

1. **Claude Code**: `/plugin marketplace add zebrabi/mcp`, then `/plugin install zebra-bi@zebrabi`. Installs the server and the skills.
2. **VS Code**: run "Chat: Install Plugin From Source" and enter `https://github.com/zebrabi/mcp`. Installs the server and the skills.
3. **claude.ai, Claude Desktop and Cowork**: add the server URL as a custom connector under Settings > Connectors. This adds the server only.
4. **ChatGPT**: in developer mode, add the server URL as a custom MCP server. This adds the server only.

## Repository layout

| Path | Read by |
|---|---|
| `.claude-plugin/plugin.json`, `.mcp.json` | Claude Code, claude.ai, Claude Desktop, Cowork, the Anthropic directory |
| `.claude-plugin/marketplace.json` | Claude Code and Copilot marketplaces |
| `plugin.json`, `mcp.json` | Agent Plugins 1.0: VS Code, Copilot CLI, ChatGPT and Codex |
| `server.json` | The MCP Registry |
| `skills/` | Hosts that install the plugin |

## Versions

Every release bumps `version` in `.claude-plugin/plugin.json`, `plugin.json` and `server.json`, because third-party marketplaces do not update plugins automatically. The version matches the one the server reports. CI checks the three manifests carry the same version and the same server URL.

## License

[Apache-2.0](LICENSE)
