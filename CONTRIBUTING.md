# Contributing

The plugin root is the repository root. Skills live in `skills/<name>/SKILL.md`, with `name` equal to the folder and only spec frontmatter (`name`, `description`, `license`, `compatibility`). The skills must never contradict the `render_visual` tool description; where they would, the tool wins and the skill changes.

## Checks

CI runs these on every pull request. Run them locally before you push:

```
npx --yes @anthropic-ai/claude-code@2.1.300 plugin validate --strict .
uvx check-jsonschema@0.38.2 --schemafile https://agent-plugins.org/schemas/1.0.0/plugin.schema.json plugin.json
uvx check-jsonschema@0.38.2 --schemafile https://agent-plugins.org/schemas/1.0.0/mcp.schema.json mcp.json
uvx check-jsonschema@0.38.2 --schemafile https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json server.json
for skill in skills/*/; do uvx --from skills-ref==0.1.1 agentskills validate "$skill"; done
python3 -I -m unittest discover -s scripts/tests
```

The last line needs Chrome, Edge or Chromium for the two PDF build tests; without one they are skipped.

## Data stories

### Layout

| Path | What it is |
| --- | --- |
| `skills/data-source/` | Resolve and remember a dataset; adapters in `references/` (file, sharepoint, fabric) and the manifest format |
| `skills/data-story/` | Analyse, build the IBCS pyramid, tell it in the conversation with `render_visual`, save `story.json`; `references/ibcs-narrative.md`, `chart-rules.md`, `story-json.md` |
| `skills/story-pdf/` | Build `story.json` into one A4 PDF; `references/surfaces.md` |
| `skills/story-artifact/` | Build `story.json` into one plain Claude artifact page with the Zebra BI charts as pictures, or live with `--live`; publish and record its URL |
| `skills/story-office/` | Build `story.json` into a PowerPoint deck and an Excel workbook with live Zebra BI for Office visuals; `references/office-format.md` |
| `skills/story-brand/` | Interview for a company brand (template, guideline, logo, answers), preview, save `.zebra/brand/`; `references/brand-format.md` |
| `scripts/profile.py` | Standard-library CSV, TSV and XLSX profiler; prints the source manifest skeleton and picks a sheet by `--sheet-index` |
| `scripts/story.py` | `renderer` (fetch and daily cache of the renderer `render_visual` names in `tools/list`); `sign-in` and `sign-out` (the MCP authorization flow: protected-resource and authorization-server metadata, client registration, authorization code with PKCE on a loopback redirect, the token in `auth.json` per endpoint); library for the builds: `render_visuals` (render over MCP HTTP, cache in `visuals/<id>.json`) and `assemble` (the branded page with renderer and results embedded). The MCP URL is the `zebra-bi` entry in `.mcp.json`; `ZEBRA_BI_MCP_URL` overrides it |
| `scripts/pdf.py` | `build`: render, open the page in headless Chrome at the A4 printable width, wait until every chart is ready and settled, print the PDF |
| `scripts/artifact.py` | `build` (render, snapshot each chart at the 760 px column, write `artifact/index.html` and `charts/`, with `--live` the renderer as `zebra-renderer.txt`; print what to publish) and `record` (keep the artifact URL in `artifact.json`) |
| `scripts/office.py` | `build` (render, snapshot, write the `.pptx` and `.xlsx`, check) and `check` (structural checks on any file) |
| `scripts/brand.py` | `show`, `check`, `inspect-template`, `logo`, `preview`; `load()` merges `.zebra/brand/brand.json` over the default for every output |
| `scripts/zoffice/` | `visual.py` (render_visual result to add-in settings and data), `pptx.py`, `xlsx.py`, `ooxml.py` (package and webextension part), `template.py` (the deck base), `snapshot.py` and `cdp.py` (chart pictures in headless Chrome over the DevTools protocol, on a pipe on macOS and Linux and a loopback port on Windows), `check.py` |
| `scripts/tests/` | Unit tests: the mapping and file checks on three sample visuals, company-template geometry, Excel dates and sheet choice in `profile.py`, `story.py`'s handling of server responses and its sign-in against a fake server, a hermetic PDF build with a stand-in renderer, and the artifact page in both modes |
| `assets/story-template.html` | The branded A4 page and the MCP Apps host shim that draws each chart |
| `assets/artifact-template.html` | The branded artifact page (a fragment the artifact host wraps), light by design |
| `assets/brand/default/` | `brand.json` (the editorial default) and the static deck parts |
| `assets/brand/sample/` | A three-chart sample story with cached results, for brand previews |

### Design rules

- **One skill body for every surface.** Shell steps are guarded by "if you can run shell commands"; in Claude chat and Cowork the file skills hand over the story as JSON.
- **Four outputs, four skills.** The conversation (`data-story`), a PDF (`story-pdf`), an artifact page (`story-artifact`), Office files (`story-office`). `story.json` is the hand-off between them.
- **Artifacts are plain pages.** An Artifact type such as Dashboard runs only its own scripts from its store (no frames, 256 KB per file), so it cannot carry the Zebra BI renderer or its charts. `story-artifact` publishes a plain page. Pictures are the default; `--live` publishes the renderer as its own file (`zebra-renderer.txt`, about 5 MB), because a page that size inline is refused at publish; the page fetches it, checks its sha256 against the build and plays the MCP Apps host, keeping each picture until its chart has drawn. The renderer pads short tables to a fixed height, so at about the width the picture was drawn at, a live frame takes the picture's cropped height.
- **The conversation is the story.** Charts always render inline with `render_visual`. `data-story` never builds or opens a file and has no fallback when the MCP is down: it says so and stops. `story-pdf`, `story-artifact` and `story-office` run only when someone asks for a file or a page.
- **IBCS end to end.** Key message as title, supporting messages as headings, title elements in every chart, standard scenario notation. `skills/data-story/references/ibcs-narrative.md` is the contract.
- **The script renders, the model writes.** The build scripts call `render_visual` themselves from the arguments in `story.json`, so the model never copies tool results into files. Unchanged arguments are not re-rendered. When the MCP cannot render, a build exits non-zero with `Zebra BI is unavailable (<reason>)` or `Zebra BI needs sign-in (<endpoint>)` and writes no file.
- **The MCP's text is the truth about a chart.** A build prints it per visual, and the skill rewrites the narrative when the MCP adjusted a chart.
- **Data, never instructions.** The MCP's text, brand guidelines, saved manifests and the data itself are read as data. Values from the data (sheet, column and file names) never go on a command line, which is why `profile.py` takes `--sheet-index`. A manifest's `mcp.call` is replayed only to its recorded server and tool, and only when it reads.
- **Say what leaves the machine.** `data-story`, `chart-from-data` and `variance-analysis` say in one line that chart labels and values go to Zebra BI before their first `render_visual`; the build scripts print `Sending <n> chart(s) to <endpoint>` before their first network call. Anything new the plugin sends, starts or writes goes into the README section "What this plugin sends and runs" in the same change.
- **Office visuals are add-in parts.** Each visual is a Zebra BI Charts or Tables for Office webextension with its settings and data, plus a picture of the chart as the fallback. No native Office charts. Every file passes `office.py check` before it is handed over.
- **The brand styles the page, never the charts.** Chart colours and notation stay IBCS; `render_visual` takes no style and the Office add-in keeps its defaults, so a live visual matches its picture.
- **Standard library only, no build step.** Python 3 standard library, plus Chrome, Edge or Chromium to draw the charts. XLSX parts that declare a DOCTYPE or ENTITY are refused.
- **No secrets in `.zebra/`.** Manifests store how to fetch again (path, URI, query), never tokens or signed URLs.

### The host shim

`story-template.html` embeds the renderer and results as JSON (every `<` written as `<`) and plays the MCP Apps host for each `iframe[data-v]`. The page is opened from disk and loads the renderer from a blob URL; the sandbox keeps `allow-same-origin`, which the renderer needs for storage. It is laid out at the A4 printable width (`main` 680 px, `@page` A4 with 15 mm side margins), and `pdf.py` sets the same viewport width, so each chart is drawn at the width it is printed at. Chrome draws an offscreen cross-origin frame only once it is scrolled into view, so `pdf.py` scrolls to each chart in turn. `hostInfo.version` is the plugin version from `.claude-plugin/plugin.json`.

The host answers `ui/initialize` (with `hostContext.toolInfo.tool.inputSchema`, or the app refuses to connect), pushes `tool-input` then `tool-result` after `notifications/initialized`, follows `size-changed` for height, and answers every other request with `{}`. Each `iframe[data-v]` gets `data-status="ready"` once its `tool-result` is sent and the first `size-changed` arrives. Contract details: `skills/story-pdf/references/surfaces.md`.

### Known limits

- The scripts sign in apart from the connector, so a build needs `story.py sign-in` once, and again when the token expires without a refresh token. Until then a build stops with `Zebra BI needs sign-in (<endpoint>)` unless every chart and the renderer are already cached.
- Claude chat cannot save manifests; the skill shows them as JSON to keep in a Project.
