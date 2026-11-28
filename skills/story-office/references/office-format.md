# Office files: how the visuals get in

`scripts/office.py` writes each visual as a **Zebra BI for Office add-in part**, the same mechanism the Zebra AI app's own PowerPoint and Excel export uses. No native Office chart and no image-only slide: the add-in draws the visual from the settings and data stored in the file.

## The add-in part

| | Charts | Tables |
| --- | --- | --- |
| AppSource asset | `WA200004688`, version `1.0.4.0` | `WA200004689`, version `1.0.0.0` |
| Settings property | `ChartSettings` | `ZBITablesSettings` |
| Data property (PowerPoint) | `zebrabi:table-data`: a DataView (`values`, `categories`, `metadata.columns`) | `zebrabi:matrix-data`: a Power BI-style matrix (`matrix.rows`, `matrix.valueSources`, `metadata`) |
| Data in Excel | the cells: `SourceData` `{"range": "'Sheet'!A4:C8", "worksheetId": "{00000000-0001-0000-0X00-…}"}` plus a matrix binding | same, plus `zebrabi:matrix-data` |
| Chooser store | `ChartChooserStore` with `isVisible: false` | `TableChooserStore` with `isVisible: false` |

- **`visual_type`.** `render_visual` returns `visual_type` `Chart` or `Table` in `structuredContent.visual`. That picks the add-in.
- **Data encoding.** The data property is JSON stored as a JSON string, so it is encoded twice.
- **Partial settings.** The add-ins merge saved keys over their own defaults, so the settings are partial: title, chart type, measure roles, and for Excel Tables the `measureRoles` and `showCrossTables: false`.
- **Index shift.** Value columns are indexed from 0 in both hosts (as in the Zebra AI app's export).
- **Grand total.** Unnamed, because PowerPoint treats a node named "Total" as a ragged category.
- **The chooser store matters.** With it empty, the add-in opens its "pick a chart" screen instead of the saved visual.
- **Where the mapping lives.** `scripts/zoffice/visual.py`, which follows the Zebra AI app's export function by function.

## The picture for readers without the add-in

- **How it is taken.** Every visual is drawn once more by the same Zebra BI renderer the conversation uses, in headless Chrome driven over the DevTools protocol (`zoffice/snapshot.py`, `zoffice/cdp.py`). It is cropped to the drawn content and stored as `snapshots/<id>.png` at 2x.
- **Where it goes.** The PNG is both the add-in's own snapshot and the `mc:Fallback` picture.
- **Who shows it.** PowerPoint and Excel show it while the add-in loads. Keynote, Numbers and Office without the add-in show only the picture.
- **Frame size.** Frames keep the picture's aspect ratio, so it is never stretched. A short table is padded to at least 0.45 height to width, because the add-in draws at larger type than the chat and needs the room.

## The canvas sheet (`--canvas`)

An opt-in extra in the workbook, next to the add-in parts and never in place of them. `office.py build <story> --xlsx --canvas` adds one Excel Canvas sheet, after every other sheet, so the add-in sheets keep their positions and worksheet ids. Bare `--canvas` draws every visual of the story's sections as one dashboard, the way the `render_dashboard` tool does; `--canvas=<visual-id>` draws that one visual (a chart or a table) alone. How it is stored, what the host allowed and the size ceiling below come from canvas files that Excel saved or that were built by hand, opened in Excel on the web on 26 Nov 2026. What was seen drawing from this build is under Known limits.

- **The dashboard.** The page draws one tool result. For a dashboard it is `{"dashboard": {"title": <story title>, "items": [{"width": 3..12, "visual": <the visual render_visual returns>}]}}`, which the same renderer lays out on a 12-column grid. Items follow the order of the story's sections. Widths: a chart over time (`time_axis` set in the story's call, or a date category in the result) is 12, a table is 6 and any other chart is 6; each row sums to 12, and a half-width item with no partner in its row (before a 12 or last) becomes 12. A dashboard holds 1 to 6 items, as `render_dashboard` does: with more than six visuals the first six are drawn and the build prints which were left out.
- **Bound cells.** The binding (`zebra`, the hidden defined name) stays on the cells of one visual's data sheet: the first section's visual, or the one named. It exists so that the page can tell whether those cells still match what the build wrote. The other visuals' sheets are not bound; editing them changes nothing on the canvas and raises no notice. A table can be the bound visual.
- **Size.** The dashboard JSON adds the other visuals' results to the page, which stays under the 2 MB limit; the build prints the page size and refuses a page above the limit.

- **How it is stored.** A chartsheet whose drawing holds a `chartEx` part with a `cx:dashboard`: one binding (`zebra`) that reads a hidden defined name (`_xlchart.v7.0`), which holds the same `'Sheet'!A4:C16` range as the add-in's `SourceData`, and one HTML artifact. A plain empty chart is the fallback for an Excel that does not know the part, with a note naming the data sheet. Written in `scripts/zoffice/canvas.py`.
- **What the page reads.** `await window.getDataBindingValue("zebra")` answers a JSON string of the bound cells as a 2D array of text, and the window event `dataBindingKeyChanged` (`detail.key`) says the cells changed.
- **What the host allowed.** The page ran under a content security policy with inline scripts and eval, no network and no workers. A page holding an `<iframe>` was refused ("Something went wrong"). The host drops `<script>` blocks that are not JavaScript (`type="application/octet-stream"` and `"application/json"` came back null), so data never rides in a typed script element.
- **Size ceiling.** Excel's canvas host does not load a large page in an ordinary workbook, and the ceiling depends on the workbook. In one workbook of several sheets a tiny canvas page loaded, the same page padded with a 4.4 MB comment did not, and a 1.49 MB page loaded and drew the Zebra BI chart. A page that inlines the 4.3 MB renderer never drew in a workbook written by `office.py`. The page therefore has to stay small: the build and the check refuse a page above 2 MB.
- **The renderer inside the page.** There is no iframe, so the Zebra BI renderer (the page `render_visual` names, cached by `story.py renderer`) is in the page, but compressed. Its five uses of the parent window are the MCP Apps transport; they are pointed at the page, which plays the host (`assets/canvas-shim.js`, first in the head). The renderer's one inline script is gzipped (level 9) and base64-encoded into a JavaScript string, `window.__zbiGz`, and a small loader placed where the script was unpacks it with `DecompressionStream` and appends it to the body; the styles and `<app-mcp-visual>` stay as they are. That takes the page from about 4.4 MB to about 1.5 MB. The build stops, with no file written, unless the renderer has exactly those five uses, a head tag, the handshake strings, no iframe, no script address, no character XML cannot carry, and exactly one plain inline script after the body tag, and unless the finished page is under 2 MB.
- **Data rule.** The page always draws the result the build embedded, so totals that are not sums (ratios, stock measures) stay as the server drew them. No content of a worksheet cell becomes chart or table data, a label or a total. On open, and when a cell changes, the page compares the bound cells with what the build wrote. Unchanged: no notice. Changed, whether a value, a label, a cell that is not a plain number, a different row count or a changed header: the built drawing stays and the page shows one line that names the bound data sheet and the kind of visual: `The cells on the sheet "<sheet>" changed after this chart was built. It still shows the figures it was built from. To show new figures, build the story again from the updated data.` (a table or a dashboard says so in place of "chart"). A read that throws, does not parse or gives no rows counts as changed too, so the notice is not cleared by a binding that cannot be read. To draw new data, build the story again; a visual is regenerated from new data through the Zebra BI MCP server (`render_visual` or `render_dashboard`).
- **Raw or shown text.** It is not yet known whether Excel hands the page raw values or the text a cell shows. So a built number matches in either form, with any thousands grouping, and a regional number format does not raise the notice. A cell that reads as a grouped number with other digits than the build wrote counts as changed, and so does a read that mixes raw values with shown text in one read.
- **Cost.** The page adds about 1.5 MB of XML to the workbook (the 4.3 MB renderer, compressed), once.

## Checks (`office.py check`)

- **Package.** The zip opens, and every XML part is well-formed with no DOCTYPE or ENTITY. Every part has a content type, and every relationship target exists.
- **Add-in parts.** Each webextension names a Zebra BI add-in. Its settings parse as JSON, and its data decodes twice to the expected keys.
- **Excel ranges.** Each `SourceData` range is a quoted `'Sheet'!A1:B2` range on an existing sheet. Its `worksheetId` matches that sheet's position, it starts at a filled header cell and it ends inside the written data.
- **Snapshots.** Each snapshot resolves. A placeholder snapshot is a warning.
- **Canvas.** Each dashboard binding has a `cx:data` with its id, which names a defined name in the workbook. That name is a quoted range on an existing sheet, starting at a filled header cell and ending inside the written data. The entrypoint names an html artifact, the page embeds a built result that is either `{visual}` or `{dashboard: {items}}` with 1 to 6 items (each with a visual and a width from 3 to 12), and the page has no `<iframe>`, no script address, no `<script>` block that is not JavaScript and no remaining parent-window use (the packed renderer script is unpacked to look), and it calls `getDataBindingValue`, names its binding and, when it packs the renderer, carries the loader that unpacks it. The `chartEx` part is reachable from a chartsheet listed in `workbook.xml`, and that sheet comes after every sheet an add-in binds. A page above 2 MB is an error that names the sizes seen to load and not to load.

## Known limits

- **Licence.** The add-in still needs its own licence on the reader's machine. Nothing in the file carries one. The canvas sheet is the exception to what readers see: it carries the whole renderer, which can be read out of the file, and draws without the add-in, a Zebra BI sign-in or a licence check. It draws only what the build embedded: a cell is never read into a chart, a table, a label or a total, and regenerating a visual from new data goes through the Zebra BI MCP server. That is why it is opt-in and why the build prints a line saying what the workbook carries.
- **No live connection.** Charts are drawn from the numbers in the file. Re-run the story to refresh them.
- **Hierarchies.** One and two category levels are verified in PowerPoint and Excel. The Zebra AI app's matrix builder warns that three levels are not supported.
- **Groups.** Charts and tables with `group_columns` are not verified in PowerPoint and Excel.
- **Long tables.** A long two-level table can scroll a little inside its frame on a 16:9 slide.
- **Font.** Segoe UI is used for text, matching the charts. Keynote and Numbers on a Mac without it substitute a font and say so.
- **Canvas: one sheet.** One canvas sheet per workbook, drawing a dashboard of up to six visuals or one visual. More than one canvas is untried, and each would add about 1.5 MB, which is also about as large as a page has been seen to load.
- **Canvas: what was seen.** On 26 Nov 2026, in Excel on the web, with sample data, workbooks written by `office.py` with `--canvas` drew the single-chart canvas and the two-visual dashboard canvas (a story's chart and table side by side under its title), and the dashboard was interactive: hovering dims and offers "Click to filter by <category>", clicking a table row highlights it, and a column header opens a menu that changes the chart type. The Charts and Tables add-ins drew on their sheets. Not seen: Excel desktop, and the notice after a cell edit in a workbook written by `office.py`. The file follows the structure of a canvas Excel saved, apart from the parts left out on purpose (identity and tenant data, theme and shared strings) and the chart reference tracking extension Excel writes.
- **Canvas: other Excels.** A version of Excel that does not show canvas sheets shows the note on the fallback chart.
- **Overlay.** The combo overlay of a chart (`render_visual`'s overlay series) is not carried. The Zebra AI app's export drops it too.
