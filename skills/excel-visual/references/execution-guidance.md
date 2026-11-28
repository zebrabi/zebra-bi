# Excel and non-Excel execution guidance

## When the skill runs inside Excel

- Run `scripts/visual-from-selection.js` when, and only when, the user asks for a Zebra BI or IBCS chart or table of the selected data. Set `VISUAL` to "chart" or "table" when the user names one.
- The script reads the selection, builds the workbook in memory and calls `Excel.createWorkbook`. It sends nothing to any server and needs no sign-in.
- Excel shows a confirmation before the new workbook opens. Say so before running, and tell the user what to expect: a new workbook with the data and the visual, and the original workbook unchanged.
- The visual is drawn by the Zebra BI Charts or Tables for Office add-in. If the add-in is not installed, the frame stays empty.

## When the skill runs outside Excel

- In a chat with the Zebra BI connector, draw the chart in the conversation with `render_visual`, following the `chart-from-data` skill.
- Never claim a workbook was changed when the environment cannot change it.

## Quality checks for either environment

- State what was completed and what the user still has to do: confirm the new workbook, and install the Zebra BI Charts or Tables add-in if it is not installed.
