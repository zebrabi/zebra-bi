#!/usr/bin/env node
// Copyright (c) 2026 ZEBRA BI informacijske rešitve d.d. Distributed under the terms in LICENSE-ZEBRA-AGENT-SKILLS at the repository root.
"use strict";
// Command-line front end to the Zebra BI tools in this folder, for a host that loads the skills but
// does not register the local tool server. It requires the server module and calls one tool in this
// process: no server is started, nothing is spawned and nothing is bundled.
//
// Usage: node cli.js list
//        node cli.js <tool_name>                 (arguments {})
//        node cli.js <tool_name> @args.json      (arguments from a JSON file)
//        node cli.js <tool_name> '<json>'        (arguments inline)
//
// A call prints exactly the tool's JSON result on stdout. Exit code 0: the tool answered. 1: the tool
// reported an error (its message is the JSON on stdout, or on stderr if it crashed). 2: the call was not made (usage, an unknown
// tool, an arguments file that is missing or is not a JSON object); the reason is on stderr.

const fs = require("fs");
const srv = require("./server.js");

const USAGE = "usage: node cli.js list | node cli.js <tool_name> [@args.json | '<json>']";
const stop = (message) => { process.stderr.write(message + "\n"); process.exitCode = 2; };

function argumentsFrom(raw) {
  if (raw === undefined) return {};
  let text = raw;
  if (raw.startsWith("@")) {
    const file = raw.slice(1);
    try { text = fs.readFileSync(file, "utf8"); } catch (err) {
      throw new Error(`cannot read the arguments file ${file}: ${err.code || err.message}`);
    }
  }
  let parsed;
  try { parsed = JSON.parse(text.replace(/^﻿/, "")); } catch (err) {
    throw new Error(`${raw.startsWith("@") ? `the arguments file ${raw.slice(1)}` : "the arguments"} `
      + `are not valid JSON: ${err.message}`);
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("the arguments must be a JSON object, as in {\"projectPath\": \"...\"}");
  }
  return parsed;
}

function main(argv) {
  const [tool, rawArgs, ...extra] = argv;
  if (!tool || extra.length) return stop(USAGE);
  if (tool === "list") {
    for (const t of srv.listTools()) {
      process.stdout.write(`${t.name} - ${(t.description || "").split("\n")[0].slice(0, 160)}\n`);
    }
    return;
  }
  let args;
  try { args = argumentsFrom(rawArgs); } catch (err) { return stop(err.message); }
  let out;
  try { out = srv.callTool(tool, args); } catch (err) {
    process.stderr.write(`the tool '${tool}' failed: ${err && err.message}
`);
    process.exitCode = 1;
    return;
  }
  if (!out) {
    return stop(`unknown tool '${tool}'. Tools: ${srv.listTools().map((t) => t.name).join(", ")}`);
  }
  process.stdout.write(out.text + "\n");
  process.exitCode = out.isError ? 1 : 0;
}

if (require.main === module) main(process.argv.slice(2));

module.exports = { main, argumentsFrom };
