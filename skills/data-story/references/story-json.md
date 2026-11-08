<!-- Summary: the story.json format that data-story writes and the story-pdf, story-artifact and story-office builds read. -->
# story.json

One file per story at `.zebra/stories/<slug>/story.json`. `data-story` writes it while it tells the story in the conversation; `story-pdf` builds it into a shareable `<slug>.pdf`, `story-artifact` into an artifact page and `story-office` into a deck and a workbook; a later refresh re-runs it against new data. `title` is the key message and each section `heading` a supporting message (see `ibcs-narrative.md`).

```json
{
  "title": "2018 sales grew 20% to $722k, driven by more orders in the West",
  "kicker": "Superstore · 2018 vs 2017",
  "subtitle": "9,800 order lines, US, Jan 2015 to Dec 2018. Sales only.",
  "source": {"name": "superstore", "label": "Superstore (2).csv, 9,800 rows"},
  "kpis": [{"value": "$722k", "label": "2018 sales, +$122k (+20%) on 2017"}],
  "sections": [
    {"heading": "The West drove half of the growth",
     "body": "West added $66k (+36%). **Washington** alone went from $20k to $66k.",
     "visual": "sales-by-region"}
  ],
  "next_heading": "Where to look next",
  "next": ["**Machines:** the one sizeable sub-category in decline."],
  "visuals": {
    "sales-by-region": {"args": { "...": "exact render_visual arguments" }}
  }
}
```

| Field | Rules |
| --- | --- |
| `title`, `subtitle` | plain text |
| `kicker` | the label above the key message on the page, the cover and the Story sheet: reporting unit and period, at most 40 characters, no message ("P&L · September 2026 close"). It wins over the brand's `kicker`; without either, no label is shown |
| `source.name` | the manifest name in `.zebra/sources/`; `label` is what the footer shows |
| `kpis` | 0-4 tiles, `value` short, `label` one line |
| `sections[].body` | plain text; a blank line starts a new paragraph; `**bold**` is the only markup |
| `sections[].visual` | a key of `visuals`, or omitted for a text-only section |
| `rows` | optional, after the sections: the report builder's layout. Each row is `{"cells": [...]}` with 1 to 3 cells side by side, each `{"visual": id}` or `{"text": "..."}` (`**bold**` allowed). `story-pdf` and `story-artifact` keep the cells side by side, each chart drawn at its cell's width; `story-office` lays out sections only |
| `layout` | optional, with `rows`: the report builder's layout as the PDF prints it. `one-pager` prints on one A4 landscape page, scaled down to fit (to no less than 55%; a longer one runs on); `board-pack` starts a page at every row that holds a chart, after a cover with the title and KPI tiles; `variance-walk`, or none, flows on A4 portrait |
| `visuals` keys | lowercase letters, digits, dashes |
| `visuals.<id>.args` | exactly what `render_visual` takes; see `chart-rules.md` |

A report builder folder (`report.zbreport`, no `story.json`) builds as a story: its title as `title`, its rows as `rows`, its layout as `layout`, no sections and no source.

What a build adds next to it: `visuals/<id>.json` (the MCP's result plus a hash of the arguments, so unchanged visuals are not re-rendered), `<slug>.pdf` from `story-pdf`, `artifact/` (the page, `charts/<id>.png`, `build.json`) and `artifact.json` (the published URL) from `story-artifact`, and `snapshots/<id>.png`, `<slug>.pptx` and `<slug>.xlsx` from `story-office`.
