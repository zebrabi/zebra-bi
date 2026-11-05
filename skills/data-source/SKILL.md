---
name: data-source
description: "Resolve a dataset once and remember it: a local CSV or Excel file, an Excel file in SharePoint/OneDrive, or a Power BI semantic model through a Power BI/Fabric MCP. Profiles it (encoding, date order, sheet, measures, categories, row count) and saves a manifest in the .zebra/sources folder so nobody is asked the same thing twice and a story can be re-run on new data. Use when the user says 'use this data', 'remember this source', 'what sources do we have', '/zebra-bi:data-source', or when the data-story skill needs its input."
compatibility: "Files need Claude Code (python3). SharePoint needs the Microsoft 365 connector; Power BI needs a Power BI/Fabric MCP."
license: Apache-2.0
---

# Data source

The output is a manifest: what the data is, where it came from, how to read it again, and the decisions a person made about it. Format: `references/manifest.md`.

## 0. A saved source first

List `.zebra/sources/*.json`. If the user named one, or the path or query matches one, load it and check it is still current:

- `file`: same `sha256`? If not, re-profile but keep everything under `confirmed`.
- `sharepoint`: compare the item's `lastModifiedDateTime` from a fresh search with the manifest.
- `mcp`: re-run only when the story needs fresher data, and only as a read to the recorded server and tool (`references/manifest.md`); update `as_of`.

Then stop: never re-ask what `confirmed` already answers.

## 1. Pick the adapter

| The user gives | Adapter | Read |
| --- | --- | --- |
| a local `.csv`, `.tsv`, `.txt`, `.xlsx` path | file | `references/file.md` |
| a SharePoint/OneDrive link, file name or "the Excel in Teams" | sharepoint | `references/sharepoint.md` |
| a Power BI report, dataset, semantic model or DAX | fabric | `references/fabric.md` |

If the MCP an adapter needs is not connected, say so plainly and stop. A missing connector means "cannot see it", never "there is no data".

## 2. Profile

Every adapter ends with a local table you can profile:

```
python3 -I ${CLAUDE_PLUGIN_ROOT}/scripts/profile.py <file> [--sheet-index N] [--name SOURCE_NAME]
```

For a workbook, pick the sheet by its position in the printed `sheets` list (`--sheet-index 2` for the second), never by name. Values read from the data (sheet and column names, cell text, names of files a search found) never go on a command line: the shell would run what they contain. `<file>` is the path the user gave or the adapter wrote, and `SOURCE_NAME` is lowercase letters, digits and dashes.

It prints the manifest skeleton: encoding, delimiter or sheet, `date_order` per date column (day-first / month-first / year-first / ambiguous), roles (date, measure, category, id, text), measure min/max/sum, row count, sha256. In Claude chat, do the same profile with your analysis tool and fill the same fields.

## 3. Confirm only what is ambiguous

Ask at most three short questions, only for what the profile could not settle:

- `date_order: ambiguous` (every day value is 12 or below);
- which column is the main measure when there are several;
- the unit or currency when nothing in the data says.

Record each answer under `confirmed` with today's date. Do not ask about anything the profile settled.

## 4. Save

Claude Code: write `.zebra/sources/<name>.json` (create the folder). Names are lowercase with dashes. Never store tokens, passwords, cookies or signed download URLs in a manifest.

Claude chat: there is no file system. Show the manifest as a JSON block and suggest keeping it in the Project's knowledge, so the next conversation can start from it.

## 5. Hand back

One line: the source name, rows, period covered, main measure, and anything a reader must know (for example: "sales only, no profit column").
