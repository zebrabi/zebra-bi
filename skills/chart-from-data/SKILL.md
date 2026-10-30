---
name: chart-from-data
description: Show business figures as an interactive IBCS-style Zebra BI chart or table with the render_visual tool. Use when presenting findings from business data such as sales, costs, budgets or KPIs, even if the user did not ask for a chart, and when the user asks to chart, plot or visualize numbers or to compare actuals with previous year, plan or forecast.
license: Apache-2.0
---

# Chart business figures with Zebra BI

When you present findings from business data, show the main ones with `render_visual` as well as describing them. A visual of actuals against a comparison reads faster than the same numbers in text, and the user does not have to ask for it. Use it in place of drawing your own chart.

Skip the visual when there is nothing to compare or rank, for example a single number, or when the user asks for text only.

Before the first chart in a conversation, say in one line that its labels and aggregated values go to Zebra BI to be drawn. The labels are the data's own values, such as customer or employee names.

## Shape the data

1. Aggregate first. Send one row per category, or one row per category pair for a two-level hierarchy with the parent column first. Duplicate category combinations are rejected.
2. Send `columns` plus `rows`, with each scenario in its own column. Every row lists its values in the order of `columns`.
3. Map the scenario columns in `measure`. `Val` is actuals and is required. Add `PY` for previous year or `PM` for previous month (one of them, not both), `PL` for plan or budget, and `FC` for forecast. What each one means: [references/scenarios.md](references/scenarios.md).
4. When the category is a time period, set `time_axis` and send the rows in time order. Its description in the tool lists each period and the format to send. Months read best when the question allows it.
5. Set `negative_impact` to `true` for costs and expenses, so an increase shows as bad.

## Pick the chart

1. `variance` compares actuals with a scenario. It needs `Val` plus at least one of `PY`, `PM`, `PL` or `FC`. This is the default for a comparison.
2. `bar` or `pin` show a single series. With a comparison scenario they are drawn as a variance chart.
3. `waterfall` shows how parts add up to a total.
4. `line` or `area` show a trend over time.

A category with `time_axis` is drawn as a chart, and so are `line` and `area`. Any other category is drawn as a table with inline charts, rows for the categories and columns for the scenarios.

Give the visual a short `title` that names the measure, and a `subtitle` with the unit and period, such as `EUR, FY2025`. Use `sort_by` with `sort_order` to rank categories, for example by `variance_abs_pl` descending to put the biggest amount above plan first. Variance sorts keep the sign, so `ascending` puts the biggest shortfall first.

## After the call

The text result says what was drawn and lists every adjustment made to the request: a different chart type, a dropped scenario, ignored columns. Describe the visual from that text, not from your request. If the call returns an error, fix the input it names and call again.

Hosts that cannot display the visual show only that text, so keep your own summary of the main findings in the reply.

## Example

Monthly sales against last year and plan:

```json
{
  "title": "Sales",
  "subtitle": "EUR thousands, Q1 2026",
  "chart_type": "variance",
  "category_columns": ["Month"],
  "time_axis": "yearmonth",
  "measure": { "name": "Sales", "Val": "Actual", "PY": "Last year", "PL": "Plan" },
  "columns": ["Month", "Actual", "Last year", "Plan"],
  "rows": [
    ["2026-01", 42, 38, 40],
    ["2026-02", 45, 44, 46],
    ["2026-03", 51, 47, 50]
  ]
}
```
