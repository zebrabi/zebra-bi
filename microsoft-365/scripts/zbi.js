#!/usr/bin/env node
"use strict";
// Command-line front end for the Zebra BI checker in the Microsoft 365 Copilot package. It starts
// the bundled checker (zbi-server.js, the MCP server in mcp/) over stdio, calls one tool and
// prints its result.
//
// Usage: node zbi.js list
//        node zbi.js <tool_name> '<json>'
//        node zbi.js <tool_name> @args.json

const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const [tool, rawArgs] = process.argv.slice(2);
if (!tool) {
  console.error("usage: node zbi.js list | node zbi.js <tool_name> <json-args|@file>");
  process.exit(2);
}
let args = {};
if (rawArgs) args = JSON.parse(rawArgs.startsWith("@") ? fs.readFileSync(rawArgs.slice(1), "utf8") : rawArgs);

const srv = spawn(process.execPath, [path.join(__dirname, "zbi-server.js")], { stdio: ["pipe", "pipe", "inherit"] });
let buf = "";
const done = (code) => { srv.kill(); process.exit(code); };
srv.on("exit", (code) => { console.error(`the checker exited before answering (code ${code})`); process.exit(1); });
srv.stdout.on("data", (d) => {
  buf += d;
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i);
    buf = buf.slice(i + 1);
    let m;
    try { m = JSON.parse(line); } catch { continue; }
    if (m.id !== 2) continue;
    srv.removeAllListeners("exit");
    if (m.error) { console.error(JSON.stringify(m.error, null, 2)); return done(1); }
    if (tool === "list") {
      for (const t of m.result.tools) console.log(t.name + " - " + (t.description || "").split("\n")[0].slice(0, 160));
      return done(0);
    }
    console.log(m.result.content.map((c) => c.text).join("\n"));
    return done(m.result.isError ? 1 : 0);
  }
});
const send = (o) => srv.stdin.write(JSON.stringify(o) + "\n");
send({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "zbi-cli", version: "1" } } });
send({ jsonrpc: "2.0", method: "notifications/initialized" });
send(tool === "list"
  ? { jsonrpc: "2.0", id: 2, method: "tools/list" }
  : { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: tool, arguments: args } });
