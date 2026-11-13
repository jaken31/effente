// Runs cases from tests/data/test_data_logic_parity.py through
// platform/web/dashboard_logic.js and prints every result as JSON, so the
// Python port can be checked against the JavaScript on the same inputs.
//   node logic_parity.js <path to dashboard_logic.js> < cases.json
"use strict";
const fs = require("node:fs");
const L = require(process.argv[2]);

function plain(v) {
  if (v instanceof Float32Array) return Array.from(v);
  if (Array.isArray(v)) return v.map(plain);
  if (v && typeof v === "object") {
    const out = {};
    for (const k of Object.keys(v)) out[k] = plain(v[k]);
    return out;
  }
  if (typeof v === "number" && !isFinite(v)) return null;
  return v;
}

const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map((steps) => {
  let state = null;
  const results = [];
  const arg = (a) => {
    if (a === "$state") return state;
    if (a && typeof a === "object" && !Array.isArray(a) && "$ref" in a) {
      const raw = results[a.$ref].raw;
      return a.key ? raw[a.key] : raw;
    }
    return a;
  };
  for (const step of steps) {
    const [op, ...rest] = step;
    if (op === "new") state = L.createState();
    else if (op === "set") state[rest[0]] = rest[1];
    else if (op === "dump") results.push({ raw: state, out: plain(state) });
    else if (op === "call") {
      const fn = L[rest[0]];
      if (typeof fn !== "function") throw new Error("no function " + rest[0]);
      const raw = fn(...rest.slice(1).map(arg));
      results.push({ raw, out: plain(raw === undefined ? null : raw) }); // copied now: state changes later
    } else throw new Error("unknown step " + op);
  }
  return results.map((r) => r.out);
});
process.stdout.write(JSON.stringify(out));
