# Report builder

The report builder runs as its own local MCP server (`scripts/report_server.py`) with an app page (`assets/report-builder.html`), so its skill lives here, outside `skills/`: every host that installs this plugin loads `skills/`, and the skill's tools exist only where the report server is installed. Until the plugin declares the server, it is installed with the skill by a separate dev step, for ChatGPT desktop.
