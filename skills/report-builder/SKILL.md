---
name: report-builder
description: "Build and keep Zebra BI reports the user arranges in the report builder app: rows of up to three Zebra BI charts and text, side by side, saved as files on this computer. Covers every report flow: list, open, create, rename, duplicate (next month's report from this one), delete and restore, and editing cells (add, move, change, remove, replace a chart). Use when the user says 'build a report', 'my Zebra BI reports', 'open the ... report', 'make the November version', 'rename', 'delete', 'restore', 'put these side by side', 'move this chart next to that one', 'save it as a PDF', or '/zebra-bi:report-builder'. Needs the zebra-bi-report tools (open_report, add_chart...); for one chart in the chat use chart-from-data, for a story told in the chat data-story."
license: Apache-2.0
---

# Report builder

The report builder is an app the user sees beside the chat or full screen. The reports are files in `~/Documents/Zebra BI Reports/` on the user's computer; nothing about a report is kept on a Zebra BI server. Charts are drawn by Zebra BI (`render_visual`) and sent as aggregated rows only, one row per category.

If the `open_report` and `add_chart` tools are not available here, say in one line that the Zebra BI report builder is not installed in this app, and stop.

## Which report

Each chat works on its own report: the one it opened or created last. A chat that never opened one works on the report opened last anywhere. Every content tool also takes `report` (a name from `list_reports`, or the title) when the user names another one.

- "What reports do I have?": `list_reports`. The one marked `*` is open in this chat.
- "Open the October close": `open_report` with `report`. It prints the cells with their ids, `[c1] chart 'P&L by line'`; use those ids, never guess them.
- New work: `create_report` with a short title (period and subject, for example "P&L close November 2026"). Do not reuse an existing report for unrelated work.
- "Rename it": `rename_report`. Only the title changes.
- "Make the November version": `duplicate_report` with the new title, then `replace_chart` on each chart with November's figures and `update_text` on the narrative. Keep the layout.

## Deleting

- Delete only when the user asks for it, naming the report. `delete_report` moves it to Recently deleted, restorable for 30 days; say so in one line.
- "Bring it back": `restore_report`. `list_reports` with `deleted: true` shows what can be restored.
- Deleting for good is done by the user in the builder (Recently deleted, Delete forever). You cannot do it.

## Building and editing

1. Shape each chart as `chart-from-data` does: aggregate first, one row per category, the scenarios as columns (`AC`, `PL`, `PY`, `FC`), `time_axis` for periods. `add_chart` returns Zebra BI's notes on the chart; read them and fix the arguments if it changed something you did not intend.
2. Lead with the key message as text (`add_text`, numbers in `**bold**`), then the chart that proves it, then the breakdowns. Put related breakdowns side by side: give `add_chart` the same `row`, at most three per row.
3. Edits by cell id: `move_cell` (row and position; one past the last row makes a new row), `update_text`, `remove_cell`, `replace_chart` (new data, period or chart type in the same place).
4. After a change, tell the user in one or two lines what is where now. Do not paste the chart data into the chat.

The user can do all of this in the builder too: drag cells by the grip, edit text and the title in place, and use ⋯ for duplicate and delete. Their edits are saved to the same file, so open the report again (`open_report`) before you change something they may have rearranged.

## Sharing

The report is a file on this computer, in its folder `~/Documents/Zebra BI Reports/<name>` (or under `ZEBRA_BI_REPORTS` when that is set).

- "Save it as a PDF": `save_pdf`. It prints an A4 PDF into the report's folder, its charts and texts side by side as in the builder, and returns the path; give the path, and say in one line that the PDF holds the report's aggregated numbers, so it goes only where the data may go. In the builder the same is ⋯ → Save as PDF.
- A page to share (Claude Code only): `story-artifact` builds the report from its folder, rows included.
- PowerPoint and Excel (`story-office`) are built from data stories, not from reports yet.
