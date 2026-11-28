---
name: excel-visual
description: "Turn the data selected in Excel into a Zebra BI chart or table in IBCS notation: actuals against previous year, plan or forecast, with the variances marked in green and red. A time category (months, quarters, years) gets a chart; products, regions or departments get a table with a total row. Use when the user has a range selected in Excel and asks for a Zebra BI or IBCS chart or table, a variance chart or table, or to chart the selection against plan or last year. The script runs inside Excel, opens the result in a new workbook and draws it with the Zebra BI Charts or Tables for Office add-in. Outside Excel, use chart-from-data."
compatibility: "Runs only inside Excel, in an agent that can run an Office.js script (Excel.run and Excel.createWorkbook). Makes no network call and needs no sign-in. Elsewhere it hands over to chart-from-data."
license: Apache-2.0
metadata:
  tags: excel
---

# Zebra BI chart or table from the selection

The visual is a Zebra BI Charts or Tables for Office visual. The Zebra BI add-in draws it from the cells, so it stays interactive and editable like any Zebra BI visual. It needs the add-in, and its licence, on the machine that opens the workbook.

## What the user selects

A header row, one category column first (months, quarters, products, regions), then one to four scenario columns named AC, PY, PL or FC. Actual, Previous year, Plan, Budget and Forecast are recognised too. One row per category, already aggregated. The category column is written as the text the cells show, so a year or a date stays as the reader sees it.

The script gives each column its role from its header. Only when no header reads as actuals does the first other numeric column stand in for them. Any further columns are left out, and the run says which. A scenario column with a text cell in it, or two columns for one scenario, stops the script with a message that names the columns; fix the selection and run again. Empty columns are skipped.

## Workbook output

Run `scripts/visual-from-selection.js`. It opens a new workbook with one sheet: the title, the selected data at A3, and the Zebra BI visual bound to that data. Excel asks the user to confirm the new workbook, so say so before running.

- Time periods (months, quarters, years, dates) get a chart, on a sheet named "Zebra BI chart".
- Other categories (products, regions, departments, accounts) get a table with inline variance charts and a total row, on a sheet named "Zebra BI table".

Before running, set the three settings at the top of the script when the request says so:

- `TITLE`: an IBCS title naming the measure, unit and period, for example "Sample Co, Revenue by month (EUR thousands, 2026 AC vs PL)". Otherwise the scenario headers and the category header make the title, for example "AC vs PL by Month".
- `COSTS = true` for costs and expenses, so that an increase shows red.
- `VISUAL`: "chart" or "table" when the user asks for one; "auto" (the default) picks as above.

## Totals

A table sums its rows for the total row. For a measure that does not sum (a ratio, an average, headcount), set `VISUAL` to "chart", where the script writes no total, and do not select a total row with the data.

## Common pitfalls to avoid

- Do not invent scenario columns or values. If no column reads as AC, PY, PL or FC, ask which column is the actual and which is the comparison.
- Do not aggregate silently. If the selection has repeated categories, say so and ask.
- Do not claim the visual is in the current workbook: it opens in a new one.
- Do not claim people without the Zebra BI Charts or Tables add-in can see the visual: they see an empty frame.

## Outside Excel

Draw the chart in the conversation with `render_visual`, following `chart-from-data`. In an agent with a file system, `story-office` writes a workbook with the same add-in visuals. Never claim a workbook was changed when the environment cannot change it.

## References

- `references/workbook-guardrails.md`: how to prepare the selection.
- `references/execution-guidance.md`: when to run the script, and what to do outside Excel.
