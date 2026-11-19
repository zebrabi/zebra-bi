## In Microsoft 365 Copilot: the Zebra validator

Here `validate_report` is not in your tool list. It runs as a script that ships with the report-authoring skill, `scripts/zbi.js` in that skill's folder, with Node. Copy that folder somewhere writable, as report-authoring describes, and run `node <copy>/scripts/zbi.js validate_report @args.json` with `{"projectPath": "<folder that holds the .pbip>"}` in the file. Wherever a check below asks whether `validate_report` is in your tool list, run that script instead, and report the rule count it returns.
