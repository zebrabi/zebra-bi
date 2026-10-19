---
name: variance-analysis
description: Explain how actual business results differ from previous year, previous month, plan or forecast, and show the variances as a Zebra BI chart with render_visual. Use when the user asks why results are above or below budget, how this period compares with last year, which items drove a gap, or for a variance or budget-versus-actual review. For a month-end close or a review of a data file or saved source across several charts, the data-story skill tells the whole story with this method.
license: Apache-2.0
---

# Analyse variances against PY, PM, PL and FC

A variance analysis answers two questions: how far actuals are from the comparison, and which items caused the gap. Work through the steps below, then show the result with `render_visual`.

## 1. Pick the comparison

Choose from the user's question, and say which one you used.

1. **Previous year (`PY`)**: growth questions and seasonal business. Compare the same period, such as March with last March.
2. **Previous month (`PM`)**: month-on-month questions. Use `PY` or `PM`, not both.
3. **Plan (`PL`)**: budget, target or plan questions.
4. **Forecast (`FC`)**: where the year will land. Send it with plan or previous year, and leave actuals empty (`null`) for the periods that have not closed, so the forecast fills them. Sent without another comparison, the forecast is itself the comparison.

If the data holds more than one comparison and the question does not settle it, lead with plan, then previous year.

## 2. Work out the variances

1. Total the actuals and the comparison over the period the user asked about.
2. The absolute variance is actual minus comparison. The relative variance is that difference divided by the size of the comparison, which keeps the sign right when the comparison is negative, such as a loss.
3. Decide the sign. For revenue and profit, above the comparison is good. For costs and expenses, above the comparison is bad, so set `negative_impact` to `true` in the visual and describe the increase as unfavourable.

## 3. Find the drivers

1. Break the total down by the most useful category, such as product, region, account or month.
2. Rank the categories by the size of their absolute variance. Name the two or three that explain most of the gap and give their share of it.
3. Point out offsets, where a large favourable item hides a large unfavourable one.
4. Separate one-off items from trends when the data shows a pattern over time.

## 4. Show it

Call `render_visual` with `chart_type` `variance`, the category from step 3 and the comparison mapped in `measure`. To rank the drivers, sort by the matching absolute variance, such as `variance_abs_pl`. The sort keeps the sign, so use `descending` when the total is above the comparison and `ascending` when it is below, which puts the biggest drivers of the gap first. For a trend, use the time period as the category with `time_axis` set instead. The `chart-from-data` skill covers how to shape the request.

Then write the explanation:

1. One sentence with the total variance, absolute and relative, and whether it is good or bad.
2. The main drivers with their amounts.
3. Anything the text result of `render_visual` says it changed or left out.

Keep the numbers in your text the same as the ones you sent to the visual.
