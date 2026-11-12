// node --test platform/web/tests/dashboard_logic.test.js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const L = require("../dashboard_logic.js");

const T0 = 1_800_000_000_000;

function rec(over) {
  return Object.assign(
    {
      device_id: "effente-01",
      machine_id: "conveyor-07",
      model_version: "fake-0.1",
      at: "2026-11-14T16:02:11Z",
      diagnosis: "normal",
      confidence: 0.9,
      unknown_score: 0.1,
      reason: "",
      features: { once_per_turn: 0.05, rattle: 0.03 },
      mask: [],
      spectrum: new Array(64).fill(0.1),
      simulated: false,
    },
    over
  );
}

const MODEL = {
  device_id: "effente-01",
  model_version: "fake-0.1",
  spectrum_hz_max: 500,
  features: [
    { name: "once_per_turn", sentence: "once-per-turn vibration" },
    { name: "rattle", sentence: "rattling pattern" },
  ],
  simulated: false,
};

test("display names follow the brief", () => {
  assert.equal(L.displayName("normal"), "Normal");
  assert.equal(L.displayName("imbalance"), "Imbalance");
  assert.equal(L.displayName("loose_mount"), "Loose mount");
  assert.equal(L.displayName("unknown"), "Unknown, inspect");
  assert.equal(L.displayName("bearing"), "bearing"); // shown as sent, not guessed
});

test("toggleMask adds and removes, keeping order", () => {
  assert.deepEqual(L.toggleMask([], "rattle"), ["rattle"]);
  assert.deepEqual(L.toggleMask(["rattle"], "once_per_turn"), ["rattle", "once_per_turn"]);
  assert.deepEqual(L.toggleMask(["rattle", "once_per_turn"], "rattle"), ["once_per_turn"]);
  assert.deepEqual(L.toggleMask(undefined, "rattle"), ["rattle"]);
});

test("featureStatus: pending until a record acknowledges", () => {
  assert.equal(L.featureStatus([], [], "rattle"), "on");
  assert.equal(L.featureStatus(["rattle"], [], "rattle"), "pending_off");
  assert.equal(L.featureStatus(["rattle"], ["rattle"], "rattle"), "off");
  assert.equal(L.featureStatus([], ["rattle"], "rattle"), "pending_on");
  assert.equal(L.featureStatusLabel("pending_off"), "Switching off…");
});

test("toggle flow: PUT reply, then a record confirms it", () => {
  const s = L.createState();
  L.applyEvent(s, "model", MODEL, T0);
  L.applyEvent(s, "record", rec({ diagnosis: "imbalance", reason: "once-per-turn vibration" }), T0);
  const d = s.devices["effente-01"];
  const next = L.toggleMask(d.mask, "once_per_turn");
  assert.deepEqual(next, ["once_per_turn"]);
  L.applyMaskReply(s, { ok: true, device_id: "effente-01", mask: next, mask_version: 1 });
  assert.equal(L.featureStatus(d.mask, d.latest.mask, "once_per_turn"), "pending_off");
  // a window diagnosed before the device picked up the mask: still pending
  L.applyEvent(s, "record", rec({ diagnosis: "imbalance", reason: "once-per-turn vibration" }), T0 + 1000);
  assert.equal(L.featureStatus(d.mask, d.latest.mask, "once_per_turn"), "pending_off");
  L.applyEvent(s, "record", rec({ diagnosis: "normal", mask: ["once_per_turn"], features: { once_per_turn: 0, rattle: 0.02 } }), T0 + 2000);
  assert.equal(L.featureStatus(d.mask, d.latest.mask, "once_per_turn"), "off");
  const so = L.switchOffSummary(s, "effente-01");
  assert.deepEqual(so.features, [{ name: "once_per_turn", sentence: "once-per-turn vibration" }]);
  assert.equal(so.before, "imbalance");
  assert.equal(so.now, "normal");
  assert.equal(so.changed, true);
});

test("switchOffSummary is null with an empty mask", () => {
  const s = L.createState();
  L.applyEvent(s, "record", rec(), T0);
  assert.equal(L.switchOffSummary(s, "effente-01"), null);
});

test("older mask reply does not overwrite a newer mask event", () => {
  const s = L.createState();
  L.applyEvent(s, "mask", { ok: true, device_id: "effente-01", mask: ["rattle"], mask_version: 3 }, T0);
  L.applyMaskReply(s, { ok: true, device_id: "effente-01", mask: [], mask_version: 2 });
  assert.deepEqual(s.devices["effente-01"].mask, ["rattle"]);
});

test("device status: live, quiet after 5 s, disconnected, none", () => {
  const s = L.createState();
  assert.equal(L.deviceStatus(s, null, T0), "none");
  L.applyEvent(s, "record", rec(), T0);
  assert.equal(L.deviceStatus(s, "effente-01", T0 + 4000), "live");
  assert.equal(L.deviceStatus(s, "effente-01", T0 + 5001), "no_readings");
  L.applyEvent(s, "device", { device_id: "effente-01", connected: false, last_seen: "x" }, T0 + 6000);
  assert.equal(L.deviceStatus(s, "effente-01", T0 + 6000), "disconnected");
});

test("snapshot: uses the platform's now for ages, drops vanished devices", () => {
  const s = L.createState();
  L.applyEvent(s, "record", rec({ device_id: "gone" }), T0);
  L.applySnapshot(
    s,
    {
      ok: true,
      now: "2026-11-14T16:02:13Z",
      devices: {
        "effente-01": {
          connected: true,
          last_seen: "2026-11-14T16:02:11Z",
          model_version: "fake-0.1",
          mask: ["rattle"],
          mask_version: 4,
          latest: rec({ received_at: "2026-11-14T16:02:11Z", mask: ["rattle"] }),
        },
      },
      models: { "fake-0.1": MODEL },
      machines: ["conveyor-07"],
    },
    T0
  );
  assert.deepEqual(Object.keys(s.devices), ["effente-01"]);
  const d = s.devices["effente-01"];
  assert.equal(d.lastRecordLocalMs, T0 - 2000);
  assert.equal(L.deviceStatus(s, "effente-01", T0), "live");
  assert.equal(L.deviceStatus(s, "effente-01", T0 + 3500), "no_readings");
  assert.equal(d.mask_version, 4);
  assert.equal(L.modelFor(s, "effente-01"), MODEL);
  assert.equal(s.snapshotLoaded, true);
});

test("snapshot with a device but no record", () => {
  const s = L.createState();
  L.applySnapshot(s, { ok: true, now: "2026-11-14T16:02:13Z", devices: { a: { connected: true, last_seen: "2026-11-14T16:02:13Z", model_version: null, mask: [], mask_version: 0, latest: null } }, models: {}, machines: [] }, T0);
  assert.equal(s.devices.a.latest, null);
  assert.equal(L.deviceStatus(s, "a", T0), "no_readings");
});

test("shown device: most recently heard, unless one is picked", () => {
  const s = L.createState();
  L.applyEvent(s, "record", rec({ device_id: "a" }), T0);
  L.applyEvent(s, "record", rec({ device_id: "b" }), T0 + 10);
  assert.equal(L.shownDeviceId(s), "b");
  L.applyEvent(s, "record", rec({ device_id: "a" }), T0 + 20);
  assert.equal(L.shownDeviceId(s), "a");
  s.selectedDeviceId = "b";
  assert.equal(L.shownDeviceId(s), "b");
  s.selectedDeviceId = "missing";
  assert.equal(L.shownDeviceId(s), "a");
});

test("history: loaded, extended from the stream, deduplicated, capped, no spectrum", () => {
  const s = L.createState();
  L.applyEvent(s, "record", rec({ seq: 1 }), T0); // before history load: not kept
  assert.equal(s.history["conveyor-07"], undefined);
  L.setHistory(s, "conveyor-07", [rec({ seq: 1 }), rec({ seq: 2 })]);
  L.applyEvent(s, "record", rec({ seq: 3 }), T0);
  L.applyEvent(s, "record", rec({ seq: 3 }), T0); // duplicate
  L.applyEvent(s, "record", rec({ seq: 4, machine_id: "other" }), T0);
  const h = s.history["conveyor-07"];
  assert.deepEqual(h.map((r) => r.seq), [1, 2, 3]);
  assert.ok(h.every((r) => !("spectrum" in r)));
  // a reload keeps what streamed in meanwhile
  L.setHistory(s, "conveyor-07", [rec({ seq: 2 })]);
  assert.deepEqual(s.history["conveyor-07"].map((r) => r.seq), [1, 2, 3]);
  const many = [];
  for (let i = 1; i <= L.HISTORY_CAP + 50; i++) many.push(rec({ seq: 100 + i }));
  L.setHistory(s, "x", many.map((r) => Object.assign(r, { machine_id: "x" })));
  assert.equal(s.history.x.length, L.HISTORY_CAP);
  assert.equal(s.history.x[0].seq, 151);
});

test("agent runs: latest per machine; stream updates replace by run_id", () => {
  const s = L.createState();
  const run = (id, at, over) => Object.assign({ run_id: id, machine_id: "conveyor-07", at, status: "running", tool_calls: [] }, over);
  L.addRuns(s, [run("r2", "2026-11-14T16:05:00Z", { status: "done" }), run("r1", "2026-11-14T16:03:00Z", { status: "done" })]);
  assert.equal(L.latestRunForMachine(s, "conveyor-07").run_id, "r2");
  L.applyEvent(s, "agent_run", run("r3", "2026-11-14T16:06:00Z"), T0);
  assert.equal(L.latestRunForMachine(s, "conveyor-07").run_id, "r3");
  L.applyEvent(s, "agent_run", run("r3", "2026-11-14T16:06:00Z", { tool_calls: [{ name: "get_readings" }] }), T0);
  assert.equal(L.latestRunForMachine(s, "conveyor-07").tool_calls.length, 1);
  // an older fetched copy does not undo the streamed progress
  L.addRuns(s, [run("r3", "2026-11-14T16:06:00Z")]);
  assert.equal(L.latestRunForMachine(s, "conveyor-07").tool_calls.length, 1);
  // a newer fetched copy (after a reconnect) wins
  L.addRuns(s, [run("r3", "2026-11-14T16:06:00Z", { status: "done", tool_calls: [{}, {}] })]);
  assert.equal(L.latestRunForMachine(s, "conveyor-07").status, "done");
  assert.equal(L.latestRunForMachine(s, "other"), null);
  assert.equal(L.latestRunForMachine(s, null), null);
});

test("simulated sources", () => {
  const s = L.createState();
  L.applyEvent(s, "model", Object.assign({}, MODEL, { simulated: true }), T0);
  L.applyEvent(s, "record", rec({ simulated: true }), T0);
  assert.deepEqual(L.simulatedSources(s, "effente-01", { simulated: true }), ["readings", "model", "agent report"]);
  const s2 = L.createState();
  L.applyEvent(s2, "record", rec(), T0); // simulated missing means false
  assert.deepEqual(L.simulatedSources(s2, "effente-01", null), []);
});

test("bad events are ignored", () => {
  const s = L.createState();
  L.applyEvent(s, "record", null, T0);
  L.applyEvent(s, "record", { diagnosis: "normal" }, T0);
  L.applyEvent(s, "mask", { mask: [] }, T0);
  L.applyEvent(s, "nonsense", { device_id: "x" }, T0);
  assert.deepEqual(s.devices, {});
});

test("page never puts API text into innerHTML", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "dashboard.js"), "utf8");
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(src));
});
