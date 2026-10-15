# Scenarios

Zebra BI follows the IBCS notation, which gives every scenario its own look so a reader can tell them apart without a legend. Map each column of your data to one of these keys in `measure`.

| Key | Scenario | Use for |
|---|---|---|
| `Val` | Actual (AC) | What happened. Required. |
| `PY` | Previous year | The same period one year earlier. |
| `PM` | Previous month | The month before. Send `PY` or `PM`, not both. |
| `PL` | Plan | Budget, target or plan. |
| `FC` | Forecast | The latest expected outcome. |

## Which comparison to pick

1. "Did we grow?" compares with `PY` (or `PM` for month-on-month questions).
2. "Did we hit the budget or target?" compares with `PL`.
3. "Where will we land?" sends `FC` together with `PL` or `PY`, with actuals left empty (`null`) for the periods that have not closed. The forecast fills those periods and the comparison runs over the whole range. When every period has actuals, a chart leaves the forecast out and the text result says so.
4. `FC` sent without `PL`, `PY` or `PM` is the comparison itself: the visual compares actuals with the forecast.

## Variances

The visual works out the variances itself, so send the scenario values, not the differences.

1. The absolute variance is actual minus the comparison, for example `Val - PL`, in the measure's units.
2. The relative variance is that difference divided by the size of the comparison, so it keeps the right sign when the comparison is negative, such as a loss.
3. A positive variance is good by default. For costs and expenses set `negative_impact` to `true`, so an increase counts as bad.

The sort keys `variance_abs_py`, `variance_abs_pm`, `variance_abs_pl` and `variance_abs_fc` sort by the absolute variance with its sign: `descending` puts the largest amount above the comparison first, `ascending` the largest amount below it. The `variance_rel_*` keys do the same with the relative variance.
