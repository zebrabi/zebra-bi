# Zebra BI plugin

Ask your AI assistant about your business numbers and get the answer as a Zebra BI chart: actuals against plan, budget, forecast or last year, with the differences drawn the way IBCS reports show them. The plugin works with Claude, ChatGPT and GitHub Copilot, and with coding agents such as Claude Code and Codex.

What you can do with it:

1. **Charts, dashboards and variances.** Ask how a figure compares with last year, last month, plan or forecast, and see it as a chart or table in the conversation, or several of them side by side as one dashboard.
2. **Data stories.** Point your assistant at a spreadsheet or a Power BI model and get the story a controller would tell: one key message, the three to five findings that prove it, and one dashboard with a chart for each. Every number is computed from your data, and the story never adds a cause the data does not show.
3. **Files to share.** A story as a PDF, a web page, or a PowerPoint deck and an Excel workbook, in your company's brand. Made only when you ask for a file.
4. **Power BI reports.** For report developers: a coding agent writes a Zebra BI report into a Power BI project and checks it before Power BI Desktop opens it. This part needs Windows with Power BI Desktop and has its own guide, [`POWER-BI.md`](POWER-BI.md).

## What leaves your computer

| What | Where it goes | When |
|---|---|---|
| The figures and labels of a chart: category and period names, the totals per scenario, the titles | Zebra BI's chart service, `mcp.zebrabi.com` | Each time a chart is drawn |
| Your spreadsheet, your Power BI model, the rows behind the totals | Nowhere. They stay on your machine | Never |
| The figures and labels of a chart that failed to draw | Zebra BI's error monitoring | Only when a chart fails |
| A story page | claude.ai, as a private page that only you can open until you share it | Only when you ask for an artifact page |
| A Power BI project | Nowhere. The checker reads it on your machine and has no network access | Never |
| Usage tracking | The plugin has none | Never |

The table covers what the plugin adds. Your assistant itself reads the data you give it, as it does with any file you share with it, under its provider's terms. Labels are your data's own names, so a chart by customer sends customer names. Leave out anything that should not leave your machine; each skill reminds you before its first chart. The full list, with what is saved on your machine and how sign-in works, is under [What this plugin sends and runs](#what-this-plugin-sends-and-runs).

## How it works

The plugin bundles the Zebra BI MCP connector with the skills that put it to work. The connector has two tools: `render_visual` draws one chart or table, and `render_dashboard` draws up to six of them side by side. Your assistant sends them aggregated figures and Zebra BI draws the variances. Hosts that support MCP Apps show the interactive visual or dashboard; the others show a text summary of what was drawn. The skills tell your assistant when to use the tool and how. The plugin comes in Claude's format and in Agent Plugins, the format VS Code, Copilot CLI, ChatGPT and Codex read. Any other MCP client can connect to the Zebra BI MCP on its own.

## Skills

| Skill | What it does |
|---|---|
| `chart-from-data` | When to show findings as a visual, how to shape the data for `render_visual`, and when to draw several charts as one dashboard with `render_dashboard`. |
| `variance-analysis` | How to explain actuals against previous year, previous month, plan or forecast, and show the variances. |
| `data-source` | Finds a dataset (local CSV or Excel, Excel in SharePoint or OneDrive, a Power BI semantic model) and remembers it in `.zebra/sources/`, so the file, sheet, date format or query is never asked twice. |
| `data-story` | Analyses the data and tells an IBCS story in the conversation, with one Zebra BI dashboard holding a chart for every supporting message. In Claude Code it also saves the story in `.zebra/stories/`. |
| `story-pdf` | Turns a saved story into one A4 PDF: the key message and KPIs, each supporting message with its chart, page numbers. Chart labels and values stay text. Claude Code only. |
| `story-artifact` | Publishes a saved story as one shareable Claude artifact page: the key message and KPIs, each supporting message with its Zebra BI chart. Charts are pictures of the Zebra BI charts, or live charts with hover on request. A plain artifact page, since the Dashboard artifact type cannot carry Zebra BI charts. Claude Code only. |
| `story-office` | Writes a saved story as a PowerPoint deck and an Excel workbook. Each chart is a live Zebra BI for Office visual where the Charts or Tables add-in is installed, and a picture of the chart for everyone else. On request (`--canvas`) the workbook also gets one Excel Canvas sheet meant to draw the story's visuals together as one Zebra BI dashboard (or one visual) without the add-in. Claude Code only. |
| `story-brand` | Sets the look and voice of story files: colours, fonts, logo, credit line, your PowerPoint template and its layouts, and the tone of the writing. The charts keep their IBCS colours whatever the brand. |

The Power BI skills, for a coding agent on Windows with Power BI Desktop:

| Skill | What it does |
|---|---|
| `setup-doctor` | Checks that this machine can author a Power BI report: Power BI Desktop, the project, the Zebra BI visuals, the licence, the model, and what can be verified. It covers Power BI authoring only, not the story tools. |
| `report-authoring` | Plans a page, writes the report layer of a Power BI project, binds Zebra BI Charts, Tables and Cards, applies IBCS variance semantics and verifies the result against Power BI Desktop. |
| `report-publishing` | The Zebra BI checks around publishing a finished report to a workspace. Microsoft's own skill does the upload. |

In Claude Code the skills run as `/zebra-bi:<skill>`, for example:

```
/zebra-bi:data-story ~/Downloads/sales.csv      # tell the story in the conversation
/zebra-bi:story-pdf sales-2026-q3               # one shareable PDF
/zebra-bi:story-artifact sales-2026-q3          # one shareable artifact page
/zebra-bi:story-office sales-2026-q3            # PowerPoint deck and Excel workbook
/zebra-bi:story-brand                           # dress story files in your company's brand
/zebra-bi:setup-doctor                          # is this machine ready to author Power BI reports?
/zebra-bi:report-authoring                      # write a Zebra BI report into a Power BI project
```

## Install

[![Install the MCP in VS Code](https://img.shields.io/badge/VS_Code-Install_the_MCP-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=zebra-bi&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fmcp.zebrabi.com%2Fmcp%22%7D) [![Install the MCP in VS Code Insiders](https://img.shields.io/badge/VS_Code_Insiders-Install_the_MCP-24bfa5?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=zebra-bi&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fmcp.zebrabi.com%2Fmcp%22%7D&quality=insiders)

The MCP URL is `https://mcp.zebrabi.com/mcp`.

1. **Claude Code**: `/plugin marketplace add zebrabi/zebra-bi`, then `/plugin install zebra-bi@zebrabi`. Installs the MCP, the skills and the Power BI checker.
2. **VS Code**: run "Chat: Install Plugin From Source" and enter `https://github.com/zebrabi/zebra-bi`. Installs the MCP and the skills. The VS Code buttons above install the MCP only.
3. **claude.ai, Claude Desktop and Cowork**: add the MCP URL as a custom connector under Settings > Connectors. This adds the MCP only.
4. **ChatGPT**: in developer mode, add the MCP URL with "Add custom MCP server". This adds the MCP only.

**Where the charts draw.** Claude Desktop chat and claude.ai show the visual inline. The Claude Code desktop tab draws it only through the MCP added as a connector in the Claude Desktop app (step 3). The plugin's own copy, which Claude Code loads too, returns the chart's data, so the skills use the connector when both are there. The Claude Code terminal cannot show MCP Apps and prints the chart data instead.

**Data stories and files.** `data-story` works wherever the MCP is connected. The `.zebra/` folder, the PDF, the artifact page and the Office files need Claude Code, `python3` (standard library only) and Google Chrome, Microsoft Edge or Chromium. In Claude chat or Cowork, `story-pdf`, `story-artifact` and `story-office` hand over the story as JSON instead of building a file. Readers need the Zebra BI Charts or Tables for Office add-in, and its licence, to see the Office visuals live. The optional Excel Canvas sheet (`story-office --canvas`) is the exception: it draws without them.

**Two MCP servers in Claude Code.** The plugin lists `zebra-bi`, the hosted server that draws the charts, and `zebra-bi-reports`, the Power BI checker, which runs on your machine with Node.js. Nothing else is installed up front; each part says what it needs the first time it is used. Without Node.js the checker shows as failed under `/mcp` and everything else works: charts and stories do not use it, and Power BI authoring still builds the report and checks the rendered page. Other hosts get the hosted server from the plugin, and [`INSTALL.md`](INSTALL.md) covers adding the checker there.

**Power BI reports.** [`POWER-BI.md`](POWER-BI.md) covers what you need before you start and the first ten minutes; [`INSTALL.md`](INSTALL.md) covers other agents and machines without git.

## What this plugin sends and runs

- **The MCP.** The skills call `render_visual` once per chart, or `render_dashboard` once for several charts. Each call sends every chart's arguments: the category, group and period labels, the aggregated values per scenario, any additional measures and totals, and the titles. The labels are your data's own values, so a chart by customer or employee sends those names; leave out anything that should not leave your machine. Each skill says so in one line before its first chart. The source file is never sent. A chart that fails to render is reported to Zebra's error monitoring together with the arguments that were sent.
- **`story-pdf`, `story-artifact` and `story-office`.** Their Python scripts send the same chart arguments to the same MCP over HTTPS and print `Sending <n> chart(s) to <endpoint>` first. A chart whose arguments did not change is reused from `.zebra/stories/<story>/visuals/`. Once a day they download the chart renderer (the `ui://` page `render_visual` names in `tools/list`) and cache it in `$CLAUDE_PLUGIN_DATA`, or in `~/.cache/zebra-bi` when that is not set. A renderer or chart fetched from another MCP address is never reused. The plugin sends no telemetry.
- **The artifact page.** `story-artifact` publishes the page and a picture of each chart to claude.ai as a private artifact, which its owner shares from the page's Share menu. It holds the story's aggregated numbers, not the source data. With `--live` it also carries the Zebra BI renderer, which draws the charts in each reader's browser.
- **The Excel Canvas sheet.** With `story-office --canvas` the workbook carries the Zebra BI renderer (about 4 MB, the page the builds already cache, compressed into a canvas page of about 1.5 MB, because Excel's canvas host did not load a 4.4 MB page in a workbook of several sheets and did load a 1.49 MB one) and the drawn visuals, and the canvas sheet draws them as one dashboard in the reader's Excel (up to six visuals in a 12-column grid, a time chart across the row, tables and other charts side by side) without the add-in, a Zebra BI sign-in or a licence check, as `--live` does for the artifact page in a browser. A hand-built dashboard page of this kind drew in Excel on the web and was interactive (hover, click to filter, a row highlight, a chart-type menu). The canvas is live on one visual only: the first, or the one named, redraws from edited cells, values and labels, when its totals are sums; the others draw as built. The renderer can be read out of the file. The sheet makes no call of its own. It is off unless asked for, and the build prints the page size and a line saying what the workbook carries. A dashboard canvas written by `office.py` was seen drawing in Excel on the web (26 Nov 2026: a story's chart and table side by side under its title); Excel desktop is untried. Excel desktop is untried.
- **A local browser.** `story-pdf`, `story-artifact`, `story-office` and the `story-brand` preview start Chrome, Edge or Chromium headless, with a throwaway profile and its background traffic (updates, sync, metrics, safe-browsing lists) turned off. The scripts drive it over a private pipe that no other program can reach; on Windows over a remote-debugging port bound to 127.0.0.1. The browser closes when the file is done.
- **A web font, if a brand sets one.** With `fonts.web_css` set, the headless browser loads that stylesheet while it builds the PDF, and an artifact page loads it in every reader's browser, so the font host (for an artifact, always Google Fonts) sees each reader's address. The default brand sets none.
- **What is written.** Files under `.zebra/` in your working folder (sources, stories, brand, and the PDF, PowerPoint and Excel files) and the renderer cache. A source read from SharePoint or a Power BI model keeps a full copy of the sheet or query result in `.zebra/sources/`. Saved sources stay out of git: `data-source` writes `.zebra/sources/.gitignore` with the first source, so the manifests and data copies are not committed. The rest of `.zebra/` (stories, brand and the files built from them) is not ignored, so add `.zebra/` to your `.gitignore` when the working folder is a repository and the story figures are confidential.
- **Sign-in.** The MCP asks for sign-in, and the scripts sign in on their own, apart from the connector: `python3 -I scripts/story.py sign-in` opens the Zebra BI sign-in page in your browser (OAuth with PKCE, answered on a 127.0.0.1 port) and keeps the token in `auth.json` next to the renderer cache, readable only by you. Sign-in stops when the MCP's sign-in metadata names another server. The token is sent only to the MCP it was issued for, and to its sign-in server to refresh or revoke it. Without a valid token a build stops with `Zebra BI needs sign-in` and writes no file. `story.py sign-out` revokes and forgets every kept sign-in, an older MCP address included. The report builder's server runs the same sign-in without blocking when the user clicks "Sign in to Zebra BI" or asks for it in the chat: it opens the page in the browser, takes the answer on a 127.0.0.1 port in the background, and keeps `sign-in.json` (whether a sign-in is waiting) and `refusal.json` (Zebra BI's last licence refusal) next to `auth.json`, readable only by you.

- **The Power BI checker.** `zebra-bi-reports` is a local Node.js process with no dependencies. It reads the Power BI project you point it at and returns findings and file content as text; it writes nothing and makes no network calls. The Power BI skills have your agent run PowerShell, start Power BI Desktop and write the report files. [`SECURITY.md`](SECURITY.md) covers that part.

## Help

- Documentation and support: https://help.zebrabi.com/
- Email: support@zebrabi.com
- Privacy policy: https://zebrabi.com/legal/?doc=privacy-policy
- Terms: https://zebrabi.com/legal/

## Repository layout

| Path | Read by |
|---|---|
| `.claude-plugin/plugin.json`, `.mcp.json` | Claude Code, claude.ai, Claude Desktop, Cowork, the Anthropic directory |
| `.claude-plugin/marketplace.json` | Claude Code and GitHub Copilot CLI marketplaces |
| `plugin.json`, `mcp.json` | Agent Plugins 1.0: VS Code, Copilot CLI, ChatGPT and Codex |
| `skills/` | Hosts that install the plugin |
| `scripts/`, `assets/` | The story skills in Claude Code: profiling, PDF and Office builds, the brand default and the page template |
| `mcp/` | The Power BI checker, started by `.mcp.json` as `zebra-bi-reports`: plain Node.js, no dependencies |
| `perf/`, `.github/scripts/` | The gates on the Power BI skills: context budget, stated rule count, public surface, tenant identifiers |
| `microsoft-365/` | The build of the Microsoft 365 Copilot app package from `skills/`, `mcp/` and the manifests |

`skills/report-authoring/` is generated from a private source and is never edited here.

`node microsoft-365/build.js` builds the Microsoft 365 Copilot app package: the Zebra BI connector, the chart, data story and Power BI skills, and the checker as scripts inside report-authoring. The Copilot-specific text lives in `microsoft-365/overlay/` and is applied at build time, so the skills here stay the only copy. [`microsoft-365/README.md`](microsoft-365/README.md) covers what the build changes and why.

`CONTRIBUTING.md` covers the internals and how to run the checks; `AGENTS.md` is the contributor contract for the Power BI part.

## Versions

The plugin and the MCP carry separate versions. Every plugin release bumps `version` in `.claude-plugin/plugin.json` and `plugin.json`, because Claude Code updates an install only when `version` changes and third-party marketplaces do not update plugins automatically; the release workflow makes that bump, and CI checks the two match and that every manifest carries the same MCP URL. The MCP server, its version and its MCP Registry entry are released from its own repository.

Releases are cut from `develop`; `CONTRIBUTING.md` covers how.

## License

Two licences, by path:

- [Apache-2.0](LICENSE) covers the plugin: the chart and story skills, `scripts/`, `assets/` and the manifests.
- The [Zebra BI Agent Skills Licence](LICENSE-ZEBRA-AGENT-SKILLS) covers the Power BI part: `skills/report-authoring/`, `skills/report-publishing/`, `skills/setup-doctor/`, `mcp/`, `perf/`, `.github/scripts/`, `AGENTS.md`, `POWER-BI.md`, `INSTALL.md` and `install.ps1`. It permits use with Zebra BI, including commercial use, and does not permit modification or redistribution, apart from a fork made to propose a contribution.

Neither includes a licence for the Zebra BI visuals.
