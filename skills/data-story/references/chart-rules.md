<!-- Summary: how to turn one finding into valid render_visual arguments, following Zebra BI's IBCS rules. -->
# Chart rules for render_visual

`render_visual` follows Zebra BI's IBCS rules. Request the chart the finding needs; the server decides the final form and says so in its text result.

## Form follows the category

| Category column | What Zebra BI draws | Use it for |
| --- | --- | --- |
| A time period, with `time_axis` set | a chart (columns, variance pins/bars) | trend, period against previous period or plan |
| Anything else | a table with inline charts, one row per member | comparing regions, products, segments |
| Two columns, parent first (not time) | a collapsible two-level table | category > sub-category |

`time_axis` names the period type and the format to send: `year` (2025), `semester` (2025-H1), `quarter` (2025-Q1), `month` (Jan), `yearmonth` (2025-01) or `date` (2025-01-31). Send the rows in time order. Prefer `yearmonth` once the year is known, so months of two years never merge. Without `time_axis` a time column is drawn as a table. A time column can never be part of a two-level hierarchy, and `sort_by` is ignored on a time axis.

The tool's own description and the `chart-from-data` skill carry the same rules; where they differ, the tool wins.

## chart_type

| chart_type | Needs | Shows |
| --- | --- | --- |
| `variance` | `Val` plus one of `PY`, `PM`, `PL`, `FC` | actual against a reference, absolute and % deltas |
| `bar` / `pin` | `Val` | one series, ranked |
| `waterfall` | `Val` | contributions to a total |
| `line` / `area` | `Val`, usually a time axis | trend over many periods |

`variance` without a reference column silently becomes a `bar`; check the text result.

## Measures

- One column per scenario: `Val` = actuals (AC), `PY` previous year, `PM` previous month (never both PY and PM), `PL` plan or budget, `FC` forecast.
- `negative_impact: true` for costs, expenses, churn, defects: an increase is then red.
- `measure.name` is the reader's word: "Sales", "Revenue", "Signups".

## Data shape

- Aggregate first: one row per category, or per parent/child pair. Duplicate combinations are rejected.
- `columns` lists every column in row order; `rows` is at most 10,000 rows of 50 columns.
- Numbers as numbers, not strings; nulls allowed for missing scenarios.
- Keep a table to about 20 members. Group the tail into "Other" when there are more.

## Labels (IBCS title elements)

- `title` = reporting unit and measure, with the dimension: "Superstore, Sales by region". The message belongs in the heading above the chart, never in the chart title.
- `subtitle` = unit, period and scenarios: "USD, 2018 AC vs PY".
- `sort_by` + `sort_order` together: `variance_abs_py` descending puts the biggest gain first; `variance_rel_py` ranks by %. Say in the text when a chart ranks by %.

## Pairing findings to charts

| Finding | Chart |
| --- | --- |
| Year or month against prior | `variance`, time axis, AC + PY |
| Which members drove the change | `variance`, member axis, sort `variance_abs_py` desc |
| Category with children | `variance`, two category columns |
| Fastest growers | `variance`, sort `variance_rel_py` desc |
| Long trend, no comparison | `line` |
| What adds up to a total change | `waterfall` |
