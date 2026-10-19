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

## Checks (`office.py check`)

- **Package.** The zip opens, and every XML part is well-formed with no DOCTYPE or ENTITY. Every part has a content type, and every relationship target exists.
- **Add-in parts.** Each webextension names a Zebra BI add-in. Its settings parse as JSON, and its data decodes twice to the expected keys.
- **Excel ranges.** Each `SourceData` range is a quoted `'Sheet'!A1:B2` range on an existing sheet. Its `worksheetId` matches that sheet's position, it starts at a filled header cell and it ends inside the written data.
- **Snapshots.** Each snapshot resolves. A placeholder snapshot is a warning.

## Known limits

- **Licence.** The add-in still needs its own licence on the reader's machine. Nothing in the file carries one.
- **No live connection.** Charts are drawn from the numbers in the file. Re-run the story to refresh them.
- **Hierarchies.** One and two category levels are verified in PowerPoint and Excel. the Zebra AI app's matrix builder warns that three levels are not supported.
- **Long tables.** A long two-level table can scroll a little inside its frame on a 16:9 slide.
- **Font.** Segoe UI is used for text, matching the charts. Keynote and Numbers on a Mac without it substitute a font and say so.
- **Overlay.** The combo overlay of a chart (`render_visual`'s overlay series) is not carried. the Zebra AI app's export drops it too.
