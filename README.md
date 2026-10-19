# Zebra BI MCP and plugin

The Zebra BI MCP draws business data as interactive IBCS-style charts and tables, inline in the conversation. This repository packages it with skills as one plugin, for AI assistants such as Claude, ChatGPT and GitHub Copilot Chat, and for coding agents such as Claude Code, Codex and Copilot CLI. The plugin comes in Claude's format and in Agent Plugins, the format VS Code, Copilot CLI, ChatGPT and Codex read. Any other MCP client can connect to the Zebra BI MCP on its own.

The MCP has one tool, `render_visual`. Your assistant or agent sends it aggregated figures, such as actuals with a previous year, plan or forecast, and Zebra BI draws the variances. Hosts that support MCP Apps show the interactive visual; the others show a text summary of what was drawn.

The skills add three things on top of the tool:

1. **Charts and variances.** When and how to show business figures as a Zebra BI visual, and how to explain actuals against previous year, previous month, plan or forecast.
2. **Data stories.** Hand your assistant or agent a dataset and get back the story a controller would tell: one key message, the three to five findings that prove it, and a Zebra BI chart for each one. Every number comes from a computation on your data, and the story never adds a cause the data does not show.
3. **Files to share.** One A4 PDF of a story, or a PowerPoint deck and an Excel workbook with live Zebra BI for Office visuals, in your company's brand. Made only when someone asks for a file.

## Skills

| Skill | What it does |
|---|---|
| `chart-from-data` | When to show findings as a visual, and how to shape the data for `render_visual`. |
| `variance-analysis` | How to explain actuals against previous year, previous month, plan or forecast, and show the variances. |
| `data-source` | Finds a dataset (local CSV or Excel, Excel in SharePoint or OneDrive, a Power BI semantic model) and remembers it in `.zebra/sources/`, so the file, sheet, date format or query is never asked twice. |
| `data-story` | Analyses the data and tells an IBCS story in the conversation, with a Zebra BI chart for every supporting message. In Claude Code it also saves the story in `.zebra/stories/`. |
| `story-pdf` | Turns a saved story into one A4 PDF: the key message and KPIs, each supporting message with its chart, page numbers. Chart labels and values stay text. Claude Code only. |
| `story-office` | Writes a saved story as a PowerPoint deck and an Excel workbook. Each chart is a live Zebra BI for Office visual where the Charts or Tables add-in is installed, and a picture of the chart for everyone else. Claude Code only. |
| `story-brand` | Sets the look and voice of story files: colours, fonts, logo, credit line, your PowerPoint template and its layouts, and the tone of the writing. The charts keep their IBCS colours whatever the brand. |

In Claude Code the skills run as `/zebra-bi:<skill>`, for example:

```
/zebra-bi:data-story ~/Downloads/sales.csv      # tell the story in the conversation
/zebra-bi:story-pdf sales-2026-q3               # one shareable PDF
/zebra-bi:story-office sales-2026-q3            # PowerPoint deck and Excel workbook
/zebra-bi:story-brand                           # dress story files in your company's brand
```

## Install

The MCP URL is `https://app.zebra-ai.com/mcp`.

1. **Claude Code**: `/plugin marketplace add zebrabi/zebra-bi`, then `/plugin install zebra-bi@zebrabi`. Installs the MCP and the skills.
2. **VS Code**: run "Chat: Install Plugin From Source" and enter `https://github.com/zebrabi/zebra-bi`. Installs the MCP and the skills.
3. **claude.ai, Claude Desktop and Cowork**: add the MCP URL as a custom connector under Settings > Connectors. This adds the MCP only.
4. **ChatGPT**: in developer mode, add the MCP URL with "Add custom MCP server". This adds the MCP only.

**Where the charts draw.** Claude Desktop chat and claude.ai show the visual inline. The Claude Code desktop tab draws it only through the MCP added as a connector in the Claude Desktop app (step 3). The plugin's own copy, which Claude Code loads too, returns the chart's data, so the skills use the connector when both are there. The Claude Code terminal cannot show MCP Apps and prints the chart data instead.

**Data stories and files.** `data-story` works wherever the MCP is connected. The `.zebra/` folder, the PDF and the Office files need Claude Code, `python3` (standard library only) and Google Chrome, Microsoft Edge or Chromium. In Claude chat or Cowork, `story-pdf` and `story-office` hand over the story as JSON instead of building a file. Readers need the Zebra BI Charts or Tables for Office add-in, and its licence, to see the Office visuals live.

## What this plugin sends and runs

- **The MCP.** The skills call `render_visual` once per chart. Each call sends the chart's arguments: the category and period labels, the aggregated values per scenario and the titles. The labels are your data's own values, so a chart by customer or employee sends those names; leave out anything that should not leave your machine. The source file is never sent. A chart that fails to render is reported to Zebra's error monitoring together with the arguments that were sent.
- **`story-pdf` and `story-office`.** Their Python scripts send the same chart arguments to the same MCP over HTTPS and print `Sending <n> chart(s) to <endpoint>` first. A chart whose arguments did not change is reused from `.zebra/stories/<story>/visuals/`. Once a day they download the chart renderer (the `ui://` page `render_visual` names in `tools/list`) and cache it in `$CLAUDE_PLUGIN_DATA`. The plugin sends no telemetry.
- **A local browser.** `story-pdf`, `story-office` and the `story-brand` preview start Chrome, Edge or Chromium headless, with a throwaway profile and a remote-debugging port bound to 127.0.0.1. The browser closes when the file is done.
- **A web font, if a brand sets one.** With `fonts.web_css` set, the headless browser loads that stylesheet while it builds the PDF. The default brand sets none.
- **What is written.** Files under `.zebra/` in your working folder (sources, stories, brand, and the PDF, PowerPoint and Excel files) and the renderer cache.
- **Sign-in.** The scripts store no credentials and do not sign in. If the MCP asks for sign-in, a build stops with `Zebra BI needs sign-in` and writes no file.

Privacy policy: https://zebra-ai.com/privacy-policy/

## Repository layout

| Path | Read by |
|---|---|
| `.claude-plugin/plugin.json`, `.mcp.json` | Claude Code, claude.ai, Claude Desktop, Cowork, the Anthropic directory |
| `.claude-plugin/marketplace.json` | Claude Code and Copilot marketplaces |
| `plugin.json`, `mcp.json` | Agent Plugins 1.0: VS Code, Copilot CLI, ChatGPT and Codex |
| `server.json` | The MCP Registry |
| `skills/` | Hosts that install the plugin |
| `scripts/`, `assets/` | The story skills in Claude Code: profiling, PDF and Office builds, the brand default and the page template |

`CONTRIBUTING.md` covers the internals and how to run the checks.

## Versions

The plugin and the MCP carry separate versions. Every plugin release bumps `version` in `.claude-plugin/plugin.json` and `plugin.json`, because third-party marketplaces do not update plugins automatically; CI checks the two match and that every manifest carries the same MCP URL. `server.json` carries the version the MCP reports.

Pushing a `v<version>` tag runs `release.yml`. It checks that the tag matches the plugin version and that the live MCP reports the `server.json` version, publishes `server.json` to the MCP Registry when that version is new, and creates a GitHub release with the plugin ZIP. Publishing needs the `mcp-registry-publish` environment with an `MCP_PRIVATE_KEY` secret, the Ed25519 key whose public half is in the `zebrabi.com` DNS TXT record.

## License

[Apache-2.0](LICENSE)
