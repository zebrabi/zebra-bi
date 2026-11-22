## In Microsoft 365 Copilot: the Zebra BI tools

This skill names Zebra BI tools, such as `profile_source`, `write_report`, `encode_*` and `validate_report`. Here they are not in your tool list: they run as a script that ships with this skill, `scripts/zbi.js`, with Node. Copy the skill folder somewhere writable and call the tools from the copy:

```
cp -r <this skill folder> /tmp/zbi
node /tmp/zbi/scripts/zbi.js list
node /tmp/zbi/scripts/zbi.js <tool_name> @/tmp/args.json
```

Pass the arguments as a JSON file, with `@`. Use the writer and encoder tools, never hand-written visual, page or report JSON.

## In Microsoft 365 Copilot: create the project with `write_project`

Create the project and model with `write_project` and add any other measure with `write_measures`; write no `.pbip`, `.pbir`, `report.json`, `.pbism` or `.tmdl` file by hand. `write_project` embeds the CSV or JSON rows, adds a calendar and AC/PL/FC/PY measures, writes a new folder and returns its files and assumptions: `{"name": "Sales", "outPath": "/tmp/out/Sales", "source": {"path": "/tmp/data/sales.csv"}, "pages": "propose"}`. For a statement, pass `order` (line and group members in statement order); add no order column later. A refusal says what to pass. Put every `assumptions` line in your hand-over.

## In Microsoft 365 Copilot: before you hand anything back

1. Run `validate_report` on the finished project folder (`{"projectPath": "<folder that holds the .pbip>"}`).
2. Fix every finding with severity `wont-open` or `blank`, then run it again. Repeat until it returns none of those.
3. There is no Power BI Desktop here, so the screenshot steps cannot run. Return the `.pbip` with its `.Report` and `.SemanticModel` folders as one downloadable `.zip`, say what the checker found and fixed, and say the page has not been rendered yet.
