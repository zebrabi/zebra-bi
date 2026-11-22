# Microsoft 365 Copilot package

`build.js` turns this plugin into one Microsoft 365 app package named Zebra BI: the hosted Zebra BI MCP (the remote server in `mcp.json`) as a remote connector, and the skills `config.json` includes. It reads `skills/`, `mcp/`, `plugin.json` and `mcp.json` and never changes them. The package's skills are the plugin's own files; the only differences are the Copilot text in `overlay/` and the fits to Microsoft's limits below.

```
node microsoft-365/build.js
```

It writes `microsoft-365/dist/zebra-bi-microsoft-365-copilot.zip` and `validate.log` beside it. It needs Node 20 or later and network access for `npx`, which fetches the Microsoft 365 Agents Toolkit CLI at the version `config.json` pins. `--out DIR` writes elsewhere, `--app-id` sets the manifest id, and `--auth-reference-id` sets the connector's OAuth reference id, which is otherwise the placeholder the converter writes.

| Path | What it is |
|---|---|
| `build.js` | Stages the plugin, applies the overlay and the fits, then runs `atk import openplugin`, `atk package` and `atk validate` |
| `config.json` | The skills in the package and the reason for each skill left out, the overlay operations per skill, the sections of report-authoring moved to a reference file, the limits, the app name and descriptions |
| `overlay/` | The Copilot-specific text, the one place it is kept. Each file is inserted into, or replaces a section of, a skill's copy in the package |
| `scripts/zbi.js` | Front end for the checker in the package: `node zbi.js list`, `node zbi.js <tool> @args.json` |
| `icons/` | The app icons, 192 by 192 and 32 by 32 |
| `test-build.js` | Offline test of the staging step |

## What the build changes in the package

- **Overlay.** Each operation in `config.json` names an exact anchor: a heading, a line or a text. An anchor that matches no line or more than one fails the build, so a change to a skill that moves an anchor is caught at the next build.
- **The checker.** Connectors are remote HTTPS MCP servers only, so the checker in `mcp/` travels inside report-authoring as scripts: the files of `mcp/` bundled into one script that reads the skill's own `rules.json`, and `scripts/zbi.js` to call one tool. The overlay tells report-authoring and setup-doctor to call it with Node.
- **SKILL.md at most 64,000 characters**, with line breaks counted twice. The report-authoring sections that `config.json` lists move verbatim to a reference file in the package, each heading kept with a one-line pointer. A listed section that is not found, or a line lost in the move, fails the build.
- **Descriptions at most 1,000 characters, at most 20 companion files per skill, no hidden files**, and an app version that does not start with 0. Each one fails the build when broken.
- **No skill left out is named** in a packaged SKILL.md, unless `config.json` gives the reason it may be.

Run `node microsoft-365/test-build.js` after changing a skill, the overlay or `config.json`. It needs no network.
