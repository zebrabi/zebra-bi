## In Microsoft 365 Copilot: the Zebra BI tools

This skill names Zebra BI tools such as `profile_source`, `write_report`, the `encode_*` tools and `validate_report`. Here they are not in your tool list: they run as a script that ships with this skill, `scripts/zbi.js`, with Node. Copy the skill folder somewhere writable first, then call the tools from that copy:

```
cp -r <this skill folder> /tmp/zbi
node /tmp/zbi/scripts/zbi.js list
node /tmp/zbi/scripts/zbi.js <tool_name> @/tmp/args.json
```

Write the tool arguments to a JSON file and pass it with `@`. Use the writer and encoder tools instead of hand-writing visual, page or report JSON, as this file says.

## In Microsoft 365 Copilot: create the project with `write_project`

Create the project and model with `write_project`; never write the `.pbip`, `definition.pbir`, `report.json`, `definition.pbism` or any `.tmdl` file yourself. It reads the CSV or JSON, embeds the rows, adds a marked calendar and the AC/PL/FC/PY measures, writes everything into a new folder and returns the files, its assumptions and the checker's findings: `{"name": "Sales", "outPath": "/tmp/out/Sales", "source": {"path": "/tmp/data/sales.csv"}, "pages": "propose"}`. A refusal says what to pass. Put every `assumptions` line in your hand-over.

## In Microsoft 365 Copilot: before you hand anything back

1. Run `validate_report` on the finished project folder (`{"projectPath": "<folder that holds the .pbip>"}`).
2. Fix every finding with severity `wont-open` or `blank`, then run it again. Repeat until it returns none of those.
3. There is no Power BI Desktop here, so the screenshot steps cannot run. Return the `.pbip` with its `.Report` and `.SemanticModel` folders as one downloadable `.zip`, say what the checker found and fixed, and say the page has not been rendered yet.
