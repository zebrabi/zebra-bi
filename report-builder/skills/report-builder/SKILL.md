---
name: report-builder
description: "Show business figures as Zebra BI charts in the chat (show_chart), the default for any question about business data (actuals against plan, previous year or forecast, a close, a P&L, revenue or costs), and build Zebra BI reports when the user asks: rows of Zebra BI charts and text in the report builder app, saved on this computer. Reports: list, open, create, rename, duplicate (next month's from this one), delete, restore, and editing cells. Use for 'build a report', 'my Zebra BI reports', 'open the ... report', 'make the November version', 'make the close report', 'make it a one-pager', 'make a board pack', 'put these side by side', 'save it as a PDF', 'make it a deck', 'save it as PowerPoint', 'sign me in to Zebra BI', or '/zebra-bi:report-builder'. Needs the zebra-bi-report tools (show_chart, open_report, add_chart...); where only the Zebra BI connector's render_visual is here, chart-from-data and data-story tell it in the chat."
license: Apache-2.0
---

# Report builder

The report builder is an app the user sees beside the chat or full screen. The reports are files in `~/Zebra BI Reports/` on the user's computer (`~/Documents/Zebra BI Reports/` on Windows and Linux); nothing about a report is kept on a Zebra BI server. Charts are drawn by Zebra BI (`render_visual`) and sent as aggregated rows only, one row per category.

If the `show_chart`, `open_report` and `add_chart` tools are not available here, say in one line that the Zebra BI report builder is not installed in this app, and stop.

## Chat or report

Answer in the chat unless the user asks for a report. The report builder is the step up for output that leaves the chat: a PDF, a deck, a board pack.

- **What leaves the computer**: before the first chart, say in one line that each chart's title, labels and aggregated values go to Zebra BI to be drawn; the source file stays on this computer.
- **When the app blocks a chart**: some apps review each tool call and may stop one that sends figures to Zebra BI. Never work around the review, and never swap in a table without a word: tell the user in one line that the app wants their OK to send the chart's aggregated values to Zebra BI, and ask for it. When they agree, call the chart again. Write the story in the meantime; the charts are what waits.
- **In the chat** (the default): read the data with code you run, then `show_chart` once per chart, its arguments shaped as in the first draft below (aggregated rows, one per category, `AC`, `PL`, `PY`, `FC`, `time_axis` for periods). Call one chart at a time, in the order of the story. Then tell the story in your reply: the key message first, then two or three sentences of evidence per chart, every number read from the rows you sent, with the IBCS rules below.
- **A report**: when the user asks for one ("build a report", "the October close report", "a one-pager", "a board pack", "save it as PDF", "make it a deck", "put these side by side"), names an existing report, or says yes to your offer. Then follow "First draft" below.
- **The offer**: after an answer in the chat with two or more charts, end your reply with one line: "Want this as a report you can arrange and save as PDF or PowerPoint?" Once per conversation; never after a single chart or while a report is open in this chat.
- **From the chat to a report**: `create_report`, then `add_chart` with the same chart arguments you showed (never recompute them), the message and note taken from your answer, `set_key_message`, `set_kpis` and `arrange_report`, as in "First draft" steps 2 to 7.

## Sign-in

Zebra BI draws the charts once the user has signed in to Zebra BI; everything else in a report works without it. Never wait for sign-in and never stop a draft for it: without it `add_chart` and `replace_chart` save the chart, and the builder shows a grey placeholder in its place until it is drawn.

- "Sign me in", "sign in to Zebra BI": `sign_in`. It opens the Zebra BI sign-in page in the user's browser and returns at once; say in one line that the page is open and the charts appear in the builder once they have signed in. If it says the page could not be opened, give the user the address it returns.
- The user can also sign in with the "Sign in to Zebra BI" button on a chart in the chat or in the builder; that is the same sign-in, and it draws every chart waiting for it.
- A chart in the chat (`show_chart`) waits the same way: it shows a grey placeholder with the sign-in button. End your reply by inviting the user to sign in.
- When a tool says Zebra BI did not draw a chart because of the user's licence or an expired sign-in, keep building and say it once at the end in one line. The builder shows the user what to do next. Never mention a trial.

## Which report

Each chat works on its own report: the one it opened or created last. A chat that never opened one works on the report opened last anywhere. Every content tool also takes `report` (a name from `list_reports`, or the title) when the user names another one.

- "What reports do I have?": `list_reports`. The one marked `*` is open in this chat.
- "Open the October close": `open_report` with `report`. It prints the cells with their ids, `[c1] chart 'P&L by line'`; use those ids, never guess them.
- New work: `create_report` with a short title (period and subject, for example "P&L close November 2026"). Do not reuse an existing report for unrelated work.
- "Rename it": `rename_report`. Only the title changes.
- "Make the November version": `duplicate_report` with the new title, then `replace_chart` on each chart with November's figures, `update_text` on each message and note, and `set_key_message` and `set_kpis` again. Keep the layout.

## Deleting

- Delete only when the user asks for it, naming the report. `delete_report` moves it to Recently deleted, restorable for 30 days; say so in one line.
- "Bring it back": `restore_report`. `list_reports` with `deleted: true` shows what can be restored.
- Deleting for good is done by the user in the builder (Recently deleted, Delete forever). You cannot do it.

## First draft

When the user gives you numbers and asks for a report ("make the October close report", "build a report from this file"), build the whole draft first and ask afterwards:

1. Read the data with code you run; never add up or subtract in your head. Keep that code for the chart's `recipe`.
2. `create_report` with a short title: subject and period ("P&L close October 2026").
3. One `add_chart` per chart, its arguments shaped as `chart-from-data` does (aggregate first, one row per category, the scenarios as columns `AC`, `PL`, `PY`, `FC`, `time_axis` for periods), with:
   - `message`, the chart's supporting message above it, one line, numbers in `**bold**`: "Costs, not revenue, carried EBITDA past plan";
   - `note`, two or three sentences of evidence under it, read from the chart: "COGS came in **98.4k** below PL; Material costs gave **78.7k** of it.";
   - `why`, one sentence on why this chart type: "A P&L reads top-down with ΔPL beside each line.";
   - `recipe`: `source` (the file or model), `query` (or the sheet), `code` (what you ran to get the rows), `period`.
   Add the charts in the order the analysis reads: detection first (what happened), then explanation from the data (which split drove it). The variance walk keeps that order; the one-pager and the board pack sort the charts by size themselves. Never a cause the data does not show.
4. `set_key_message`: what happened, how big, against what, numbers in `**bold**`.
5. `set_kpis`: two to four tiles, a short value and a one-line label with its variance ("1.0M", "Revenue, ΔPL −39.2k (−3.7%)").
6. `arrange_report` with `variance-walk`: every chart on its own row with its note, in your order, so the user checks the draft with the evidence in view. Offer the other two when it fits what the user is making:
   - `one-pager` for a reader with a minute: largest variances first, up to three charts a row, notes hidden, and the PDF prints it on one landscape page (if it is too long to read at that size, `save_pdf` says so; fewer charts fit it);
   - `board-pack` for a pack sent ahead of a meeting: largest variances first, notes shown, and the PDF puts the key message and KPI tiles on a cover and each chart on its own page, its message as the heading.
   A layout keeps the same charts and text; switching shows or hides the notes as above.
7. Tell the user in two or three lines what the draft holds and offer the next step. Do not paste the chart data into the chat. When the charts wait for sign-in (`add_chart` says so), end the reply by inviting the user to sign in to Zebra BI so the charts are drawn: the "Sign in to Zebra BI" button in the builder, or they say "sign me in".

IBCS, the notation Zebra BI charts use, applies to every text (the `data-story` skill's `references/ibcs-narrative.md` has the full rules): AC, PL, PY, FC; ΔPY, ΔPL and ΔPY% for variances; the unit once per chart; chart `title` is the reporting unit and measure and `subtitle` the unit, period and scenarios, never the message; round for reading; no decoration words ("significant", "notable"); a cost that went down is "down" or "below PL", never "a positive variance".

## Numbers

The builder checks every number in a message, note, text cell, key message and KPI tile against the charts' rows. A chart's message and note are checked against that chart; the key message, KPI tiles and text cells against all charts. A number passes when it is, within the rounding you write:

- a row's value, a column's total, its smallest or largest value;
- a variance to PL, PY, PM or FC, absolute or in percent, for a row or for the total;
- a row's share of the total, or of the total variance;
- with `time_axis`, the move between two periods, absolute or as a percent of the earlier one.

Write signs in words ("39.2k under plan") or with + and −, thousands as k and millions as M, with English separators (1,026,887.77). Years, labels (Q4, FY26) and dates as the charts write them (2026-07, 31 Oct) are not checked. Anything else, such as a count, a ratio of two measures or a figure from the file that no chart shows, is flagged.

When a tool answers `Unchecked numbers: …`, fix the text, or the chart's rows if they are wrong; never ask the user to keep a number. Keeping one is the user's choice in the builder (Keep as mine), and `save_pdf` and `save_pptx` refuse while any number is flagged.

## Editing

Edits by cell id: `move_cell` (row and position; one past the last row makes a new row), `update_text` (a text cell, or a chart cell's `field`: `message`, `note` or `why`), `remove_cell`, `replace_chart` (new data, period or chart type in the same place; then `update_text` its message and note so the numbers match). `open_report` marks a chart without a message or note `MISSING`: write one. To write or check the text of a chart you did not just add, `read_chart` gives its rows, message, note, why and recipe first. Never write a placeholder: when the numbers are not there (a copied report whose data has not been replaced yet), say so in the chat and leave the text as it is. After a change, tell the user in one or two lines what is where now.

"Hide the notes" / "show the notes": `show_notes`. It switches the notes under all charts off or on, in the builder and in the PDF and page; hidden notes are kept. In the builder the same is the Notes button.

The user can do all of this in the builder too: drag cells by the grip, edit the title, key message, KPI tiles, messages and notes in place, switch the layout, and use ⋯ for duplicate, PDF and delete. Their edits are saved to the same file, so open the report again (`open_report`) before you change something they may have rearranged.

## Sharing

The report is a file on this computer, in its folder `~/Zebra BI Reports/<name>` on macOS and `~/Documents/Zebra BI Reports/<name>` elsewhere (or under `ZEBRA_BI_REPORTS` when that is set).

Exports need every chart drawn: while one waits for sign-in, `save_pdf` and `save_pptx` save nothing and say so; invite the user to sign in first.

- "Save it as a PDF": `save_pdf`. It prints an A4 PDF into the report's folder, its charts and texts side by side as in the builder, and returns the path; give the path, and say in one line that the PDF holds the report's aggregated numbers, so it goes only where the data may go. In the builder the same is ⋯ → Save as PDF.
- A page to share (Claude Code only): `story-artifact` builds the report from its folder, rows included.
- "Make it a deck", "save it as PowerPoint": `save_pptx`. It writes `<name>.pptx` into the report's folder: a cover with the key message and KPI tiles, then a slide per chart in the report's order (the layout's order, or the user's own), the chart's message as the slide title and its note beside it and in the speaker notes. Each chart is a live Zebra BI visual that the Zebra BI add-in can edit, with a picture for everyone else. Give the path, with the same line about aggregated numbers. In the builder the same is ⋯ → Save as PowerPoint. Excel is built from data stories (`story-office`), not from reports.
