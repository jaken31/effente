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
      model_version: "conveyor-07-20261114",
      at: "2026-11-14T16:02:11Z",
      diagnosis: "normal",
      confidence: 0.9,
      unknown_score: 0.4,
      reason: "",
      features: { vib_1x: 0.8, snd_level_db: -0.6, th_max_c: 0.5 },
      abnormal_minutes: 0,
      out_of_range: 0,
      note: "",
      problems: [],
      wrong: false,
      spectrum: new Array(20).fill(-5),
      simulated: false,
    },
    over
  );
}

const MODEL = {
  device_id: "effente-01",
  machine_id: "conveyor-07",
  model_version: "conveyor-07-20261114",
  fault_types: [
    { name: "unbalance_like", text: "once-per-turn vibration grown", group: "vibration", validated: false },
    { name: "vibration_too_high", text: "overall vibration well above normal", group: "vibration", validated: true },
  ],
  features: [
    { name: "vib_1x", text: "vibration once per turn", unit: "log" },
    { name: "snd_level_db", text: "sound level", unit: "db" },
  ],
  spectrum_edges_hz: Array.from({ length: 21 }, (_, i) => 5 * Math.pow(100, i / 20)),
  simulated: false,
};

const TOO_HOT = { name: "too_hot", kind: "limit", text: "over 100 C", value: 104.5, since: "2026-11-14T16:01:50Z" };
const ABOVE = { name: "vibration_above_normal", kind: "outside_normal", text: "vibrating well beyond its normal", value: 2.3, since: "2026-11-14T16:00:47Z" };

function live(s) {
  s.platformUp = true;
  return s;
}

test("display names: runtime names read as words, unknown names as sent", () => {
  assert.equal(L.displayName("normal"), "Normal");
  assert.equal(L.displayName("warming_up"), "Warming up");
  assert.equal(L.displayName("stopped"), "Stopped");
  assert.equal(L.displayName("unrecognised_pattern"), "Unrecognised pattern");
  assert.equal(L.displayName("unbalance_like"), "Unbalance-like");
  assert.equal(L.displayName("looseness_or_rattle"), "Looseness or rattle");
  assert.equal(L.displayName("bearing"), "Bearing");
  assert.equal(L.diagnosisKind("unbalance_like"), "fault");
  assert.equal(L.diagnosisKind("unrecognised_pattern"), "unrecognised");
  assert.equal(L.diagnosisKind("stopped"), "stopped");
  assert.equal(L.diagnosisKind(undefined), "none");
});

test("problem kinds and values read as the contract says", () => {
  assert.equal(L.kindLabel("limit"), "limit for any machine");
  assert.equal(L.kindLabel("outside_normal"), "beyond this machine's normal");
  assert.equal(L.kindLabel("new_kind"), "new kind"); // not in the contract: shown, not hidden
  assert.equal(L.problemValueText(TOO_HOT), "104.5 °C");
  assert.equal(L.problemValueText(ABOVE), "2.3× its normal");
  assert.equal(L.problemValueText({ name: "hotter_than_normal", value: 18.2 }), "+18 °C over its normal");
  assert.equal(L.problemValueText({ name: "smells_of_smoke", value: 3.14159 }), "3.14"); // unknown name: value as sent
  assert.equal(L.problemValueText({ name: "too_hot", value: null }), "");
});

test("headline follows wrong, in every mode", () => {
  const s = live(L.createState());
  assert.deepEqual(L.headline(s, null, T0), { state: "none", text: "Waiting for a device" });
  L.applyEvent(s, "record", rec(), T0);
  assert.equal(L.headline(s, "effente-01", T0).state, "ok");
  // a problem while the model says normal
  L.applyEvent(s, "record", rec({ problems: [TOO_HOT], wrong: true }), T0 + 1000);
  assert.deepEqual(L.headline(s, "effente-01", T0 + 1000), { state: "wrong", text: "Something is wrong" });
  assert.equal(L.modelLine(s, "effente-01").label, "Model: nothing unusual yet");
  // recording, with the limit still on
  L.applyEvent(s, "device", { device_id: "effente-01", connected: true, last_seen: "x", mode: "recording" }, T0 + 2000);
  assert.equal(L.deviceStatus(s, "effente-01", T0 + 60000), "checking_in"); // no records while recording is fine
  assert.equal(L.headline(s, "effente-01", T0 + 60000).state, "wrong");
  assert.equal(L.modelLine(s, "effente-01").label, "Model: not judging while recording");
  L.applyEvent(s, "problem_cleared", { device_id: "effente-01", machine_id: "conveyor-07", at: "x", name: "too_hot" }, T0 + 3000);
  assert.equal(L.headline(s, "effente-01", T0 + 3000).state, "ok");
});

test("not live never says nothing is wrong", () => {
  const s = live(L.createState());
  L.applyEvent(s, "record", rec(), T0);
  assert.equal(L.headline(s, "effente-01", T0 + 6000).state, "not_live"); // no record for 5 s
  L.applyEvent(s, "device", { device_id: "effente-01", connected: false, last_seen: "x", mode: "watching" }, T0 + 6000);
  assert.equal(L.headline(s, "effente-01", T0 + 6000).state, "not_live");
  const s2 = L.createState(); // platform not up
  L.applyEvent(s2, "record", rec(), T0);
  assert.equal(L.headline(s2, "effente-01", T0).state, "not_live");
});

test("model line: next to problems, never hidden; unvalidated fault types are possible", () => {
  const s = live(L.createState());
  L.applyEvent(s, "model", MODEL, T0);
  L.applyEvent(s, "record", rec({ diagnosis: "stopped", unknown_score: null, note: "the machine is not running", problems: [TOO_HOT], wrong: true }), T0);
  assert.deepEqual(L.modelLine(s, "effente-01"), { kind: "stopped", label: "Model: machine stopped", detail: "the machine is not running", note: "", possible: false });
  assert.equal(L.headline(s, "effente-01", T0).state, "wrong");
  L.applyEvent(s, "record", rec({ diagnosis: "warming_up", unknown_score: null, problems: [TOO_HOT], wrong: true }), T0);
  assert.equal(L.modelLine(s, "effente-01").label, "Model: warming up");
  L.applyEvent(s, "record", rec({ diagnosis: "unbalance_like", reason: "once-per-turn vibration grown", wrong: true }), T0);
  assert.deepEqual(L.modelLine(s, "effente-01"), { kind: "fault", label: "Model: Unbalance-like", detail: "once-per-turn vibration grown", note: "", possible: true });
  L.applyEvent(s, "record", rec({ diagnosis: "vibration_too_high", wrong: true }), T0);
  assert.equal(L.modelLine(s, "effente-01").possible, false);
  L.applyEvent(s, "record", rec({ diagnosis: "unrecognised_pattern", reason: "abnormal, no known fault type fits", wrong: true, note: "1 reading far out" }), T0);
  const ml = L.modelLine(s, "effente-01");
  assert.equal(ml.kind, "unrecognised");
  assert.equal(ml.note, "1 reading far out");
});

test("problem events: added and removed by name; quiet clears everything", () => {
  const s = live(L.createState());
  L.applyEvent(s, "record", rec({ diagnosis: "unbalance_like", wrong: true }), T0);
  const d = s.devices["effente-01"];
  L.applyEvent(s, "problem", { device_id: "effente-01", machine_id: "conveyor-07", at: "x", problem: TOO_HOT }, T0);
  L.applyEvent(s, "problem", { device_id: "effente-01", machine_id: "conveyor-07", at: "x", problem: ABOVE }, T0);
  L.applyEvent(s, "problem", { device_id: "effente-01", machine_id: "conveyor-07", at: "x", problem: Object.assign({}, TOO_HOT, { value: 110 }) }, T0);
  assert.deepEqual(d.problems.map((p) => p.name), ["vibration_above_normal", "too_hot"]);
  L.applyEvent(s, "problem_cleared", { device_id: "effente-01", machine_id: "conveyor-07", at: "x", name: "vibration_above_normal" }, T0);
  L.applyEvent(s, "problem_cleared", { device_id: "effente-01", machine_id: "conveyor-07", at: "x", name: "too_hot" }, T0);
  assert.deepEqual(d.problems, []);
  assert.equal(d.wrong, true); // the fault type is still there
  L.applyEvent(s, "problem", { device_id: "effente-01", machine_id: "conveyor-07", at: "x", problem: TOO_HOT }, T0);
  L.applyEvent(s, "device", { device_id: "effente-01", connected: false, last_seen: "x", mode: "watching" }, T0);
  L.applyEvent(s, "problem_cleared", { device_id: "effente-01", machine_id: "conveyor-07", at: "x", name: "too_hot", reason: "device quiet" }, T0);
  assert.deepEqual(d.problems, []);
  assert.equal(d.wrong, false);
});

test("a record sets mode, wrong and problems", () => {
  const s = live(L.createState());
  L.applyEvent(s, "device", { device_id: "effente-01", connected: true, last_seen: "x", mode: "calibrating" }, T0);
  L.applyEvent(s, "record", rec({ problems: [ABOVE], wrong: true }), T0);
  const d = s.devices["effente-01"];
  assert.equal(d.mode, "watching");
  assert.equal(d.machine_id, "conveyor-07");
  assert.deepEqual(d.problems, [ABOVE]);
  assert.equal(d.wrong, true);
});

test("moved readings use the announced text, in record order", () => {
  const s = live(L.createState());
  L.applyEvent(s, "model", MODEL, T0);
  L.applyEvent(s, "record", rec({ features: { snd_level_db: -0.6, vib_1x: 7.2, not_announced: 1.5, bad: "x" } }), T0);
  assert.deepEqual(L.movedReadings(s, "effente-01"), [
    { name: "snd_level_db", text: "sound level", value: -0.6 },
    { name: "vib_1x", text: "vibration once per turn", value: 7.2 },
    { name: "not_announced", text: "not_announced", value: 1.5 },
  ]);
  assert.deepEqual(L.featureTexts(s, "effente-01"), { vib_1x: "vibration once per turn", snd_level_db: "sound level" });
});

test("device status: live, quiet after 5 s, disconnected, none", () => {
  const s = L.createState();
  assert.equal(L.deviceStatus(s, null, T0), "none");
  L.applyEvent(s, "record", rec(), T0);
  assert.equal(L.deviceStatus(s, "effente-01", T0 + 4000), "live");
  assert.equal(L.deviceStatus(s, "effente-01", T0 + 5001), "no_readings");
  L.applyEvent(s, "device", { device_id: "effente-01", connected: false, last_seen: "x", mode: "watching" }, T0 + 6000);
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
          mode: "watching",
          machine_id: "conveyor-07",
          model_version: "conveyor-07-20261114",
          wrong: true,
          problems: [TOO_HOT],
          latest: rec({ received_at: "2026-11-14T16:02:11Z", problems: [TOO_HOT], wrong: true }),
        },
        quiet: {
          connected: false,
          last_seen: "2026-11-14T16:00:00Z",
          mode: "recording",
          machine_id: "fan-02",
          model_version: null,
          wrong: true, // the platform says false while not connected; the page does not trust it either way
          problems: [TOO_HOT],
          latest: null,
        },
      },
      models: { "conveyor-07-20261114": MODEL },
      machines: ["conveyor-07"],
    },
    T0
  );
  assert.deepEqual(Object.keys(s.devices).sort(), ["effente-01", "quiet"]);
  const d = s.devices["effente-01"];
  assert.equal(d.lastRecordLocalMs, T0 - 2000);
  assert.equal(L.deviceStatus(s, "effente-01", T0), "live");
  assert.equal(L.deviceStatus(s, "effente-01", T0 + 3500), "no_readings");
  assert.equal(d.mode, "watching");
  assert.equal(d.wrong, true);
  assert.deepEqual(d.problems, [TOO_HOT]);
  const q = s.devices.quiet;
  assert.equal(q.wrong, false);
  assert.deepEqual(q.problems, []);
  assert.equal(q.mode, "recording");
  assert.equal(q.machine_id, "fan-02"); // known from its check-ins, with no record
  assert.equal(L.modelFor(s, "effente-01"), MODEL);
  assert.equal(s.snapshotLoaded, true);
});

test("snapshot with a device but no record", () => {
  const s = L.createState();
  L.applySnapshot(s, { ok: true, now: "2026-11-14T16:02:13Z", devices: { a: { connected: true, last_seen: "2026-11-14T16:02:13Z", mode: "watching", machine_id: "m", model_version: null, wrong: false, problems: [], latest: null } }, models: {}, machines: [] }, T0);
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
  L.applyEvent(s, "problem", { device_id: "x" }, T0); // no problem in it
  L.applyEvent(s, "problem_cleared", { name: "too_hot" }, T0); // no device
  L.applyEvent(s, "mask", { device_id: "x", mask: [] }, T0); // contract version 1
  L.applyEvent(s, "nonsense", { device_id: "x" }, T0);
  assert.deepEqual(s.devices, {});
});

test("page never puts API text into innerHTML", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "dashboard.js"), "utf8");
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write/.test(src));
});
