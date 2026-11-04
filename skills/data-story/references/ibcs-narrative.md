<!-- Summary: how a data story follows IBCS (SUCCESS rules): one key message, supporting messages as headings, standard notation, and charts that prove each message. -->
# IBCS narrative

IBCS (International Business Communication Standards) is the notation Zebra BI charts already follow. The narrative follows it too, so text and charts read as one report. The rules below are the parts of SUCCESS that apply to written text.

## SAY: every story delivers a message

Structure the story as a pyramid:

1. **Key message** (the story title). One sentence a manager could repeat: what happened, how big, against what. "2018 sales grew 20% to $722k, driven by more orders in the West."
2. **Supporting messages** (the section headings). Each one proves part of the key message, and together they cover it without overlap. Order them by contribution, largest first.
3. **Evidence** (the section's two or three sentences and its chart). Numbers that prove the heading, nothing else.

Each message is one of three kinds, and the story uses them in this order:

| Kind | Answers | Example |
| --- | --- | --- |
| Detection | what happened | "Sales grew 20% on PY" |
| Explanation | where it came from, in the data | "The West made 54% of the gain" |
| Suggestion | what to check or do next | "Check whether Washington's jump repeats" |

An explanation must come from the data (a split by region, product, customer). A cause the data does not show ("because of a campaign") is not an explanation; leave it out or label it as a question in the suggestions.

## UNIFY: one notation

| Term | Meaning |
| --- | --- |
| AC | actual |
| PY | previous year (PM previous month) |
| PL | plan or budget |
| FC | forecast |
| ΔPY, ΔPL | absolute variance to PY, to PL |
| ΔPY%, ΔPL% | relative variance |

Use these in subtitles and wherever a sentence names a scenario ("AC 2018 vs PY 2017"). Units are stated once per chart (`USD`, `USD k`) and never mixed within one story. Positive variances are good unless the measure is a cost (`negative_impact`), and the text says "up" or "down", never "positive variance" for a cost.

## CONDENSE and SIMPLIFY

- One chart per supporting message. A section without a chart is allowed when the message is a single number.
- KPI tiles: at most four, each a value plus one line of context with its scenario ("+$122k (+20%) on PY").
- Round for reading: $722k, +20%, 54%. Keep the precision the decision needs, nothing more.
- No decoration words ("significant", "notable", "impressive"). The variance says how big.

## A brand's voice

A company brand (set with the `story-brand` skill) can set spelling, form of address, tone notes and words to avoid. It shapes how headings and findings sound. Everything in this file takes precedence over it: the message structure, the notation, the units, rounding, and never stating a cause the data does not show.

## CHECK

- Every number in text matches its chart, from the same computation.
- Compare like with like: full periods only, or label partial periods ("YTD Sep").
- Same scale for charts that are compared side by side; say so when a chart ranks by % instead of absolute.

## STRUCTURE: title elements

Every chart carries IBCS title elements: `title` = reporting unit and measure ("Superstore, Sales by region"), `subtitle` = unit, period and scenarios ("USD, 2018 AC vs PY"). The message lives in the section heading above the chart, never in the chart title.
