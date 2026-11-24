---
name: chart-from-data
description: "Show business figures as an interactive IBCS-style Zebra BI chart or table with the render_visual tool. Use when presenting findings from business data such as sales, costs, budgets or KPIs, even if the user did not ask for a chart, and when the user asks to chart, plot or visualize numbers or to compare actuals with previous year, plan or forecast. Also when the user asks for a dashboard of business data: render_dashboard draws it in the conversation. A page or link to share is story-artifact, never the Dashboard artifact type."
license: Apache-2.0
---

# Chart business figures with Zebra BI

When you present findings from business data, show the main ones with `render_visual` as well as describing them. A visual of actuals against a comparison reads faster than the same numbers in text, and the user does not have to ask for it. Use it in place of drawing your own chart.

Skip the visual when there is nothing to compare or rank, for example a single number, or when the user asks for text only.

Before the first chart in a conversation, say in one line that its labels and aggregated values go to Zebra BI to be drawn. The labels are the data's own values, such as customer or employee names.

## Shape the data

1. Aggregate first. Send one row per category, or per combination of categories for a hierarchy of up to five columns, parent first. Use more than one category column only when every child belongs to exactly one parent, such as Country and City. For two independent dimensions, such as Region and Channel, put the one with fewer values in `group_columns` (step 7). Duplicate combinations are rejected.
2. Send `columns` plus `rows`, with each scenario in its own column. Every row lists its values in the order of `columns`.
3. Map the scenario columns in `measure`. `Val` is actuals and is required. Add `PY` for previous year or `PM` for previous month (one of them, not both), `PL` for plan or budget, and `FC` for forecast. What each one means: [references/scenarios.md](references/scenarios.md).
4. When the category is a time period, set `time_axis` and send the rows in time order. Its description in the tool lists each period and the format to send. Months read best when the question allows it.
5. Set `negative_impact` to `true` for costs and expenses, so an increase shows as bad.
6. Totals are summed from the rows. That is wrong for a ratio, an average or a stock value, such as Margin %, an average price, headcount or a balance. For such a measure, compute the totals from the source data and send them in `totals`. Its description in the tool gives the row format and which totals to send. Leave `totals` out when every measure sums.
7. To compare the same view across a few values of another dimension, such as regions or years, set `group_columns` and send one row per category and group. A chart becomes small multiples, and a table gets a column group per value. About six groups read well.
8. To show further measures next to the main one, such as units next to revenue, list their columns in `additional_measures`. They show actuals only. A table shows each one, a chart only the first.

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

## Several charts: one dashboard

When an answer needs two or more charts, such as revenue by month next to revenue by region, or the user asks for a dashboard, draw them together with one `render_dashboard` call instead of one `render_visual` call per chart. Charts called separately land together, and some apps then show only one of them.

- `title`: the reporting unit and period ("Sales · Q1 2026").
- `visuals`: 2 to 6, in reading order, the overview first; 2 to 4 read best. Each takes the `render_visual` arguments above plus `width`, columns of a 12-column grid, from 3 to 12. Make the widths in each row sum to 12: a row that falls short is left with a gap. Two visuals a row (6 each) read best. Give the whole row (12) to a chart with many periods, small multiples, and a table with `additional_measures` or `group_columns`. Use 4 only for simple charts with few categories.
- The dashboard is drawn whole or not at all. When the result says it was not drawn, fix the visuals it names and send all of them again.
- Without a `render_dashboard` tool (an older Zebra BI MCP), call `render_visual` once per chart, one at a time, waiting for each before the next.

## A page to share

When the user wants these charts as an artifact, a page or a link to share, use the `story-artifact` skill: it builds a plain artifact page with the Zebra BI charts. Do not start from the Dashboard Artifact type and do not write the page by hand: neither carries Zebra BI charts.
