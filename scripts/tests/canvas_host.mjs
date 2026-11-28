// Runs a filled canvas shim (assets/canvas-shim.js after scripts/zoffice/canvas.py fills it) in plain Node, with a stub
// window and document and a stand-in renderer that speaks the MCP Apps handshake through window.__zbiHost.
//
//   node canvas_host.mjs <filled-shim.js> <scenario.json>
//
// scenario.json: {"cells": rows or null, "steps": [{"key": "zebra", "cells": rows or null}], "delay": ms, "wait": ms}.
// `cells` is what window.getDataBindingValue answers with (null: the page has no such function, as outside Excel;
// {"throws": true}: the read rejects; {"raw": text}: the read answers with exactly that text; {"missing": true}: it answers with nothing),
// after `delay` ms (default 0; "never" does not answer). `wait` is how long to let the page settle after the start
// (default 25 ms). Each step sets the cells and fires dataBindingKeyChanged for its key. Prints JSON: the answer to ui/initialize, every tool-result the
// renderer received, how many it had after the start and after each step, and the notice line as the page shows it.
import fs from "fs";

const [shimPath, scenarioPath] = process.argv.slice(2);
const shim = fs.readFileSync(shimPath, "utf8");
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));

const listeners = {};
const results = [];
let init = null;
let cells = scenario.cells;

class MessageEvent {
  constructor(type, data) { this.type = type; Object.assign(this, data || {}); }
}
const body = { children: [], appendChild(el) { this.children.push(el); } };
const win = {
  postMessage() {},
  addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  dispatchEvent(ev) { (listeners[ev.type] || []).slice().forEach((fn) => fn(ev)); return true; },
};
const setCells = (value) => {
  cells = value;
  if (value === null) delete win.getDataBindingValue;
  else win.getDataBindingValue = async () => {
    if (scenario.delay === "never") return new Promise(() => {});
    if (scenario.delay) await new Promise((resolve) => setTimeout(resolve, scenario.delay));
    if (cells && cells.throws) throw new Error("the binding cannot be read");
    if (cells && cells.missing) return undefined;
    if (cells && typeof cells.raw === "string") return cells.raw;
    return JSON.stringify(cells);
  };
};
setCells(cells);
Object.assign(globalThis, {
  window: win,
  MessageEvent,
  location: { origin: "https://canvas.example" },
  document: {
    documentElement: { clientWidth: 800 },
    body,
    createElement: () => ({ style: {}, textContent: "", setAttribute() {} }),
    addEventListener() {},
  },
});

// The stand-in renderer.
win.addEventListener("message", (ev) => {
  const m = ev.data || {};
  if (m.id === 1 && m.result) {
    init = m.result;
    win.__zbiHost.postMessage({ jsonrpc: "2.0", method: "ui/notifications/initialized" }, "*");
  } else if (m.method === "ui/notifications/tool-result") {
    results.push(m.params);
  }
});

const tick = (ms = 25) => new Promise((resolve) => setTimeout(resolve, ms));
(0, eval)(shim);
win.__zbiHost.postMessage({ jsonrpc: "2.0", id: 1, method: "ui/initialize", params: { protocolVersion: "2025-11-21" } }, "*");
await tick(scenario.wait || 25);
const counts = [results.length];
for (const step of scenario.steps || []) {
  setCells(step.cells);
  win.dispatchEvent(new MessageEvent("dataBindingKeyChanged", { detail: { key: step.key } }));
  await tick();
  counts.push(results.length);
}
const el = body.children[0];
process.stdout.write(JSON.stringify({
  init, results, counts,
  notice: el ? { shown: el.style.display !== "none", text: el.textContent } : { shown: false, text: "" },
}) + "\n", () => process.exit(0)); // the page's own timers would hold the process open
