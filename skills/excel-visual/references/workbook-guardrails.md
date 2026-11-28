# Workbook data guardrails

## Core principles

- Start from the labels already in the workbook.
- One category column, then scenario columns. Column order does not matter: the script orders them AC, FC, PL, PY for a chart and AC, PY, PL, FC for a table.
- A table adds its own total row, summed from the rows. Do not select a total row with the data, and do not use a table for measures that do not sum (ratios, averages, headcount): set `VISUAL` to "chart".
- Values are numbers, already aggregated to one row per category.
- Name the scenario columns so their role is clear (AC, PY, PL, FC). A column the script left out is reported in its result: say so to the user.

## Search rules

- Use the user's selection. If nothing suitable is selected, look for a table whose headers include AC, PY, PL or FC (or Actual, Previous year, Plan, Budget, Forecast) and ask the user to select it.
- Stop once the needed range is found.

## Quality checks

- Confirm the measure, the unit and the period for the title.
- Mark costs (`COSTS = true`) so that increases show red.
- Keep the user's numbers; never fill gaps with estimates.
