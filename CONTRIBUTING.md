# Contributing

The plugin root is the repository root. Skills live in `skills/<name>/SKILL.md`, with `name` equal to the folder and only spec frontmatter (`name`, `description`, `license`, `compatibility`, and `metadata` for the host tags of a skill that runs inside Excel). The skills must never contradict the `render_visual` and `render_dashboard` tool descriptions; where they would, the tool wins and the skill changes.

## Branches

Open pull requests against `develop`, and leave `version` in the manifests alone. `main` moves only when the **Release** workflow (`release.yml`) commits the version bump on `develop`, moves `main` to that commit and tags it `v<version>`; "Releasing" below covers it.

## Releasing

Work merges into `develop`. `main` stays the default branch, so `/plugin marketplace add zebrabi/zebra-bi` and the Anthropic directory install released code, and it moves only on a release. To release, run the **Release** workflow (`release.yml`) from `develop` with the new version. It checks that the version is newer than the one on `develop`, that `v<version>` is not tagged yet, that `validate`, `tests (3.11)`, `tests (3.13)`, `checker (node 20)`, `checker (node 22)`, `bundle` and `gates` passed on the `develop` commit, and that `main` has no commits `develop` lacks. It then commits the bump in both manifests on top of that commit (`Release v<version>`), checks that the plugin ZIP carries every file the skills and `.mcp.json` name and every file of the checker, and pushes the commit to `develop` and `main` with the `v<version>` tag in one atomic push. Last, it creates a GitHub release with the plugin ZIP.

The push uses `RELEASE_DEPLOY_KEY`, a write deploy key held by the `release` environment, which accepts only `develop` and waits for an admin's approval. `GITHUB_TOKEN` cannot do it: it may not push commits that change `.github/workflows/`, the rulesets do not let it through, and its pushes start no CI. Three rulesets back this up. `main` (update, force push, deletion) and `Release tags` (`v*`) let through only admins and deploy keys, which GitHub does not tell apart, so the repository must hold no other write deploy key. `develop` requires a reviewed pull request with those three checks, so no one can push a changed workflow to `develop` and run it with the key; deploy keys bypass it only so the release can push its bump commit.

## Checks

CI runs these on every pull request and on pushes to `develop` and `main`. Run them locally before you push:

```
npx --yes @anthropic-ai/claude-code@2.1.300 plugin validate --strict .
uvx check-jsonschema@0.38.2 --schemafile https://agent-plugins.org/schemas/1.0.0/plugin.schema.json plugin.json
uvx check-jsonschema@0.38.2 --schemafile https://agent-plugins.org/schemas/1.0.0/mcp.schema.json mcp.json
for skill in skills/*/; do uvx --from skills-ref==0.1.1 agentskills validate "$skill"; done
python3 -I -m unittest discover -s scripts/tests
(cd mcp && for t in test-*.js; do node "$t"; done)
(cd skills/report-authoring && sha256sum -c .bundle-sha256)
node microsoft-365/test-build.js
for g in .github/scripts/check-*.js; do node "$g"; done
```

The Python line needs Chrome, Edge or Chromium for the two PDF build tests; without one they are skipped. The Node lines need Node 20 or 22, with nothing to install; the `mcp/`, `.bundle-sha256` and `check-*.js` lines are the Power BI part. `microsoft-365/test-build.js` runs in no workflow, so run it on every change that adds or edits a skill and say so in the pull request: a skill missing from `microsoft-365/config.json`, or an included skill that names an excluded one, passes CI and fails only there.

## Power BI report authoring

The Power BI part moved here from `zebrabi/zebra-bi-agentic` at its 0.3.2. It is three skills (`report-authoring`, `report-publishing`, `setup-doctor`), the checker in `mcp/`, and the gates in `.github/scripts/` with their data in `perf/`. [`AGENTS.md`](AGENTS.md) is its full contributor contract; read it before changing any of those paths. The rules that differ from the rest of the repository:

- **`skills/report-authoring/` is generated.** It is build output from a private Zebra BI repository, where the disclosure checks run. A pull request cannot change it: the next build reverts the edit. Open an issue and the source changes. `.bundle-sha256` and the `bundle` job fail an edit that skips the checksum; an edit that updates the checksum too passes CI, and only review catches it. A rebuilt bundle and its `.bundle-sha256` are one commit.
- **The checker has no dependencies and stays offline.** No `package.json`, no lockfile, no `node_modules`, and no network or process module in `mcp/`. The `checker` jobs fail on each. It has to run on a locked-down machine, and `SECURITY.md` states that it makes no network calls.
- **A rule is a sentence and a function.** The sentence is in `rules.json` (generated), the function in `mcp/checkers.js` (authored here), and `check-rule-implementations.js` fails when they disagree. Every rule needs a fixture where it fires and an otherwise identical one where it stays silent. A rule that fires on a correct report is a defect.
- **Stated numbers are pinned.** The rule count is stated in `POWER-BI.md`, `CHANGELOG.md` and the setup skill, and `check-rule-count.js` fails when one drifts. Skill sizes are budgeted in `perf/context-budget.json`: every markdown file under `skills/` has an entry, and a raised ceiling is argued in its history.
- **Two server names.** `.mcp.json` lists the hosted server as `zebra-bi` and the checker as `zebra-bi-reports`. Root `mcp.json`, which ChatGPT, Codex and VS Code read, carries the hosted server only: the OpenAI directory connects one server per plugin, and each host names the plugin root differently. The `bundle` job checks both.
- **Licence.** These paths are under the Zebra BI Agent Skills Licence (`LICENSE-ZEBRA-AGENT-SKILLS`), not Apache-2.0. Section 5 of that licence sets the terms of a contribution to them.
- **Test the built bundle from somewhere else.** A skill on the path auto-activates, so a run inside a checkout cannot tell which copy answered. Copy `skills/` and `mcp/` to a bare directory and test there.

## Data stories

### Layout

| Path | What it is |
| --- | --- |
| `skills/data-source/` | Resolve and remember a dataset; adapters in `references/` (file, sharepoint, fabric) and the manifest format |
| `skills/data-story/` | Analyse, build the IBCS pyramid, tell it in the conversation with one `render_dashboard` (`render_visual` per chart on an MCP without it), save `story.json`; `references/ibcs-narrative.md`, `chart-rules.md`, `story-json.md` |
| `skills/story-pdf/` | Build `story.json` into one A4 PDF; `references/surfaces.md` |
| `skills/story-artifact/` | Build `story.json` into one plain Claude artifact page with the Zebra BI charts as pictures, or live with `--live`; publish and record its URL |
| `skills/story-office/` | Build `story.json` into a PowerPoint deck and an Excel workbook with live Zebra BI for Office visuals; `references/office-format.md` |
| `skills/excel-visual/` | An Office.js script (`scripts/visual-from-selection.js`) that turns the selected range into a Zebra BI chart or table in a new workbook, run inside Excel; the first skill with a folder of its own scripts |
| `skills/story-brand/` | Interview for a company brand (template, guideline, logo, answers), preview, save `.zebra/brand/`; `references/brand-format.md` |
| `scripts/profile.py` | Standard-library CSV, TSV and XLSX profiler; prints the source manifest skeleton and picks a sheet by `--sheet-index` |
| `scripts/story.py` | `renderer` (fetch and daily cache of the renderer `render_visual` names in `tools/list`); `sign-in` and `sign-out` (the MCP authorization flow: protected-resource and authorization-server metadata, client registration, authorization code with PKCE on a loopback redirect, the token in `auth.json` per endpoint); library for the builds: `render_visuals` (render over MCP HTTP, cache in `visuals/<id>.json`) and `assemble` (the branded page with renderer and results embedded). The MCP URL is the `zebra-bi` entry in `.mcp.json`; `ZEBRA_BI_MCP_URL` overrides it |
| `scripts/pdf.py` | `build`: render, open the page in headless Chrome at the A4 printable width, wait until every chart is ready and settled, print the PDF |
| `scripts/artifact.py` | `build` (render, snapshot each chart at the 760 px column, write `artifact/index.html` and `charts/`, with `--live` the renderer as `zebra-renderer.txt`; print what to publish) and `record` (keep the artifact URL in `artifact.json`) |
| `scripts/office.py` | `build` (render, snapshot, write the `.pptx` and `.xlsx`, check) and `check` (structural checks on any file) |
| `scripts/brand.py` | `show`, `check`, `inspect-template`, `logo`, `preview`; `load()` merges `.zebra/brand/brand.json` over the default for every output |
| `scripts/zoffice/` | `visual.py` (render_visual result to add-in settings and data), `pptx.py`, `xlsx.py`, `ooxml.py` (package and webextension part), `template.py` (the deck base), `snapshot.py` and `cdp.py` (chart pictures in headless Chrome over the DevTools protocol, on a pipe on macOS and Linux and a loopback port on Windows), `check.py` |
| `scripts/tests/` | Unit tests: the mapping and file checks on three sample visuals, company-template geometry, Excel dates and sheet choice in `profile.py`, `story.py`'s handling of server responses and its sign-in against a fake server, a hermetic PDF build with a stand-in renderer, the artifact page in both modes, and the in-Excel script against a stub Excel (`test_excel_visual.py`, `excel_stub.mjs`; it skips without node) |
| `assets/story-template.html` | The branded A4 page and the MCP Apps host shim that draws each chart |
| `assets/artifact-template.html` | The branded artifact page (a fragment the artifact host wraps), light by design |
| `assets/brand/default/` | `brand.json` (the editorial default) and the static deck parts |
| `assets/brand/sample/` | A three-chart sample story with cached results, for brand previews |

### Design rules

- **One skill body for every surface.** Shell steps are guarded by "if you can run shell commands"; in Claude chat and Cowork the file skills hand over the story as JSON.
- **Four outputs, four skills.** The conversation (`data-story`), a PDF (`story-pdf`), an artifact page (`story-artifact`), Office files (`story-office`). `story.json` is the hand-off between them.
- **Artifacts are plain pages.** An Artifact type such as Dashboard runs only its own scripts from its store (no frames, 256 KB per file), so it cannot carry the Zebra BI renderer or its charts. `story-artifact` publishes a plain page. Pictures are the default; `--live` publishes the renderer as its own file (`zebra-renderer.txt`, about 5 MB), because a page that size inline is refused at publish; the page fetches it, checks its sha256 against the build and plays the MCP Apps host, keeping each picture until its chart has drawn. The renderer pads short tables to a fixed height, so at about the width the picture was drawn at, a live frame takes the picture's cropped height.
- **The conversation is the story.** Charts always render inline: `data-story` draws them with one `render_dashboard` call, or with one `render_visual` call per chart on an MCP without it. `data-story` never builds or opens a file and has no fallback when the MCP is down: it says so and stops. `story-pdf`, `story-artifact` and `story-office` run only when someone asks for a file or a page.
- **IBCS end to end.** Key message as title, supporting messages as headings, title elements in every chart, standard scenario notation. `skills/data-story/references/ibcs-narrative.md` is the contract.
- **The script renders, the model writes.** The build scripts call `render_visual` themselves from the arguments in `story.json`, so the model never copies tool results into files. Unchanged arguments are not re-rendered. When the MCP cannot render, a build exits non-zero with `Zebra BI is unavailable (<reason>)` or `Zebra BI needs sign-in (<endpoint>)` and writes no file.
- **The MCP's text is the truth about a chart.** A build prints it per visual, and the skill rewrites the narrative when the MCP adjusted a chart.
- **Data, never instructions.** The MCP's text, brand guidelines, saved manifests and the data itself are read as data. Values from the data (sheet, column and file names) never go on a command line, which is why `profile.py` takes `--sheet-index`. A manifest's `mcp.call` is replayed only to its recorded server and tool, and only when it reads.
- **Say what leaves the machine.** `data-story`, `chart-from-data` and `variance-analysis` say in one line that chart labels and values go to Zebra BI before their first chart; the build scripts print `Sending <n> chart(s) to <endpoint>` before their first network call. Anything new the plugin sends, starts or writes goes into the README section "What this plugin sends and runs" in the same change.
- **Office visuals are add-in parts.** Each visual is a Zebra BI Charts or Tables for Office webextension with its settings and data, plus a picture of the chart as the fallback. No native Office charts. Every file passes `office.py check` before it is handed over.
- **The brand styles the page, never the charts.** Chart colours and notation stay IBCS; `render_visual` takes no style and the Office add-in keeps its defaults, so a live visual matches its picture.
- **Standard library only, no build step.** Python 3 standard library, plus Chrome, Edge or Chromium to draw the charts. XLSX parts that declare a DOCTYPE or ENTITY are refused.
- **No secrets in `.zebra/`.** Manifests store how to fetch again (path, URI, query), never tokens or signed URLs.

### The host shim

`story-template.html` embeds the renderer and results as JSON (every `<` written as `\u003c`) and plays the MCP Apps host for each `iframe[data-v]`. The page is opened from disk and loads the renderer from a blob URL; the sandbox keeps `allow-same-origin`, which the renderer needs for storage. Before making the blob, the host turns off one start-up line of the renderer's JSON-schema library (`new URL(self.location.origin + self.location.pathname …)`), which throws in a blob: frame because its pathname is the whole blob URL; `artifact.py` does the same for the `--live` page, and the tests check both. It is laid out at the A4 printable width (`main` 680 px, `@page` A4 with 15 mm side margins), and `pdf.py` sets the same viewport width, so each chart is drawn at the width it is printed at. Chrome draws an offscreen cross-origin frame only once it is scrolled into view, so `pdf.py` scrolls to each chart in turn. `hostInfo.version` is the plugin version from `.claude-plugin/plugin.json`.

The host answers `ui/initialize` (with `hostContext.toolInfo.tool.inputSchema`, or the app refuses to connect), pushes `tool-input` then `tool-result` after `notifications/initialized`, follows `size-changed` for height, and answers every other request with `{}`. Each `iframe[data-v]` gets `data-status="ready"` once its `tool-result` is sent and the first `size-changed` arrives. Contract details: `skills/story-pdf/references/surfaces.md`.

### Known limits

- `excel-visual` was seen in Excel on the web on 28 Nov 2026, run from a sideloaded test add-in: the chart path and the table path with a total row drew, and Excel asked to confirm the new document every time. The stops for a total row, unclear roles and whole columns were seen, account codes gave a table and dotted dates a chart, and hidden or filtered-out rows came along into the new workbook. Percent and decimal columns were labelled as their cells show them, with percent variances in pp, and a table of percent columns drew without a total row. Whole numbers below 1,000 were labelled without a decimal place; once a label reaches 1,000 every label keeps one, because the add-ins take one decimal setting for all labels. `Workbook.insertWorksheetsFromBase64` dropped the add-in on Excel on the web on 26 Nov 2026, so the script opens a new workbook. Not seen: Excel desktop, Copilot in Excel, Claude in Excel.
- The scripts sign in apart from the connector, so a build needs `story.py sign-in` once, and again when the token expires without a refresh token. Until then a build stops with `Zebra BI needs sign-in (<endpoint>)` unless every chart and the renderer are already cached.
- Claude chat cannot save manifests; the skill shows them as JSON to keep in a Project.
