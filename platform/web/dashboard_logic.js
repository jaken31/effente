// Pure dashboard logic: no DOM, no network. Loaded by index.html as a plain
// script (exposes window.EffenteLogic) and by node --test via require().
// Shapes follow platform/CONTRACT.md section 5.
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.EffenteLogic = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Diagnoses that are not fault types (contract "Names").
  const NOT_ABNORMAL = ["normal", "stopped", "warming_up"];
  const WATCHING = "watching";

  // Contract: connected means heard from in the last 5 seconds. The brief adds
  // "no record for 5 seconds" as its own stale condition while watching.
  const STALE_MS = 5000;
  const HISTORY_CAP = 300;

  // Contract section 5, "Showing problems next to the diagnosis".
  const KIND_LABELS = {
    limit: "limit for any machine",
    outside_normal: "beyond this machine's normal",
  };

  // How the value of each known problem reads. A name not listed here is
  // still shown, with its value as sent: names are the device's, not ours.
  const PROBLEM_VALUES = {
    vibration_over_limit: (v) => fixed(v, 1) + " mm/s",
    knocking: (v) => "kurtosis " + fixed(v, 1),
    too_hot: (v) => fixed(v, 1) + " °C",
    heating_fast: (v) => fixed(v, 1) + " °C a minute",
    repeated_jolts: (v) => fixed(v, 0) + " jolts in 10 min",
    vibration_above_normal: (v) => fixed(v, 1) + "× its normal",
    hotter_than_normal: (v) => "+" + fixed(v, 0) + " °C over its normal",
    louder_than_normal: (v) => "+" + fixed(v, 0) + " dB over its normal",
  };

  const MODE_NAMES = {
    watching: "Watching",
    display: "Display",
    calibrating: "Calibrating",
    recording: "Recording",
    training: "Training",
  };

  function fixed(v, digits) {
    return Number(v).toFixed(digits);
  }

  function capitalise(text) {
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }

  // "unbalance_like" -> "Unbalance-like", "looseness_or_rattle" -> "Looseness or rattle".
  function displayName(diagnosis) {
    if (diagnosis == null) return "";
    const text = String(diagnosis);
    if (text === "normal") return "Normal";
    if (text === "warming_up") return "Warming up";
    if (text === "stopped") return "Stopped";
    if (text === "unrecognised_pattern") return "Unrecognised pattern";
    return capitalise(text.replace(/_like$/, "-like").replace(/_/g, " "));
  }

  // 'none' (no diagnosis), 'normal', 'warming_up', 'stopped', 'unrecognised' or 'fault'.
  function diagnosisKind(diagnosis) {
    if (typeof diagnosis !== "string" || diagnosis === "") return "none";
    if (diagnosis === "unrecognised_pattern") return "unrecognised";
    if (NOT_ABNORMAL.includes(diagnosis)) return diagnosis;
    return "fault";
  }

  function modeName(mode) {
    if (!mode) return "";
    return MODE_NAMES[mode] || capitalise(String(mode).replace(/_/g, " "));
  }

  function kindLabel(kind) {
    return KIND_LABELS[kind] || (kind ? String(kind).replace(/_/g, " ") : "");
  }

  function problemValueText(problem) {
    const v = problem ? problem.value : null;
    if (typeof v !== "number" || !isFinite(v)) return "";
    const f = PROBLEM_VALUES[problem.name];
    return f ? f(v) : String(Math.round(v * 100) / 100);
  }

  function asProblems(v) {
    return Array.isArray(v) ? v.filter((p) => p && typeof p === "object" && typeof p.name === "string" && p.name) : [];
  }

  function parseTime(iso) {
    if (typeof iso !== "string") return NaN;
    return Date.parse(iso);
  }

  function createState() {
    return {
      devices: {}, // device_id -> device view (see ensureDevice)
      models: {}, // model_version -> message 1
      runs: {}, // run_id -> {run, order}
      runOrder: 0,
      history: {}, // machine_id -> records without spectrum, oldest first
      selectedDeviceId: null, // null = auto (most recently heard)
      platformUp: false, // the event stream is open
      platformLost: false, // the stream or /api/state failed and has not recovered
      snapshotLoaded: false,
    };
  }

  function ensureDevice(state, id) {
    if (!state.devices[id]) {
      state.devices[id] = {
        device_id: id,
        connected: false,
        last_seen: null,
        model_version: null,
        mode: null, // "watching" or a check-in mode; null until the platform says
        machine_id: null,
        wrong: false, // from the newest record or check-in; false while not connected
        problems: [], // likewise; [] while not connected
        latest: null, // newest record
        lastRecordLocalMs: null, // browser clock time the newest record counts as received
        lastHeardLocalMs: null,
      };
    }
    return state.devices[id];
  }

  // Load GET /api/state. nowMs is the browser clock. The platform's own `now`
  // is used to convert its timestamps into "how long ago", so a clock skew
  // between browser and platform cannot make a fresh record look stale.
  function applySnapshot(state, snap, nowMs) {
    const serverNow = parseTime(snap && snap.now);
    const ago = (iso) => {
      const t = parseTime(iso);
      if (isNaN(t) || isNaN(serverNow)) return null;
      return nowMs - Math.max(0, serverNow - t);
    };
    state.models = Object.assign({}, state.models, (snap && snap.models) || {});
    const devs = (snap && snap.devices) || {};
    const seen = new Set(Object.keys(devs));
    for (const id of Object.keys(state.devices)) {
      if (!seen.has(id)) delete state.devices[id];
    }
    for (const id of Object.keys(devs)) {
      const src = devs[id] || {};
      const d = ensureDevice(state, id);
      d.connected = !!src.connected;
      d.last_seen = src.last_seen || null;
      d.model_version = src.model_version || null;
      d.mode = src.mode || null;
      d.wrong = d.connected && src.wrong === true;
      d.problems = d.connected ? asProblems(src.problems) : [];
      d.lastHeardLocalMs = ago(src.last_seen);
      if (src.latest && typeof src.latest === "object") {
        d.latest = src.latest;
        d.lastRecordLocalMs = ago(src.latest.received_at || src.latest.at);
        if (!d.model_version && src.latest.model_version) d.model_version = src.latest.model_version;
      } else {
        d.latest = null;
        d.lastRecordLocalMs = null;
      }
      d.machine_id = src.machine_id || (d.latest && d.latest.machine_id) || null;
    }
    state.snapshotLoaded = true;
    return state;
  }

  function addHistoryRecord(state, rec) {
    if (!rec || !rec.machine_id) return;
    const list = state.history[rec.machine_id];
    if (!list) return; // only machines whose history was loaded are kept
    const slim = Object.assign({}, rec);
    delete slim.spectrum;
    if (typeof slim.seq === "number") {
      for (let i = list.length - 1; i >= 0; i--) {
        if (list[i].seq === slim.seq) return; // duplicate after reconnect
        if (typeof list[i].seq === "number" && list[i].seq < slim.seq) break;
      }
    }
    list.push(slim);
    if (typeof slim.seq === "number") {
      list.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
    }
    if (list.length > HISTORY_CAP) list.splice(0, list.length - HISTORY_CAP);
  }

  // Load GET /api/machines/<id>/records. Records already received on the
  // stream for this machine are kept and merged in (deduplicated by seq).
  function setHistory(state, machineId, records) {
    const prev = state.history[machineId] || [];
    state.history[machineId] = [];
    for (const r of records || []) addHistoryRecord(state, r);
    for (const r of prev) addHistoryRecord(state, r);
    return state;
  }

  function addRun(state, run) {
    if (!run || typeof run.run_id !== "string") return;
    state.runOrder += 1;
    state.runs[run.run_id] = { run: run, order: state.runOrder };
  }

  function addRuns(state, runs) {
    // Contract lists runs newest first; add oldest first so arrival order
    // breaks ties in the same direction.
    const list = Array.isArray(runs) ? runs.slice().reverse() : [];
    for (const r of list) {
      if (!r || typeof r.run_id !== "string") continue;
      const have = state.runs[r.run_id];
      // A copy fetched after a reconnect may be newer than the streamed one,
      // or older if the stream got ahead of the fetch. Keep the further one.
      if (have && runProgress(have.run) > runProgress(r)) continue;
      addRun(state, r);
    }
    return state;
  }

  function runProgress(run) {
    const calls = Array.isArray(run.tool_calls) ? run.tool_calls.length : 0;
    return (run.status === "done" ? 1e6 : 0) + calls;
  }

  // Apply one SSE event (contract section 5). Mutates and returns state.
  function applyEvent(state, name, data, nowMs) {
    if (!data || typeof data !== "object") return state;
    if (name === "record") {
      if (!data.device_id) return state;
      const d = ensureDevice(state, data.device_id);
      d.latest = data;
      d.lastRecordLocalMs = nowMs;
      d.lastHeardLocalMs = nowMs;
      d.connected = true;
      d.mode = WATCHING;
      d.last_seen = data.received_at || data.at || d.last_seen;
      if (data.machine_id) d.machine_id = data.machine_id;
      if (data.model_version) d.model_version = data.model_version;
      d.wrong = data.wrong === true;
      d.problems = asProblems(data.problems);
      addHistoryRecord(state, data);
    } else if (name === "model") {
      if (data.model_version) state.models[data.model_version] = data;
      if (data.device_id) {
        const d = ensureDevice(state, data.device_id);
        d.model_version = data.model_version || d.model_version;
      }
    } else if (name === "agent_run") {
      addRun(state, data);
    } else if (name === "device") {
      if (!data.device_id) return state;
      const d = ensureDevice(state, data.device_id);
      d.connected = !!data.connected;
      if (data.last_seen) d.last_seen = data.last_seen;
      if (data.mode) d.mode = data.mode;
      if (data.connected) d.lastHeardLocalMs = nowMs;
      else {
        d.wrong = false;
        d.problems = [];
      }
    } else if (name === "problem") {
      if (!data.device_id || !data.problem || typeof data.problem.name !== "string") return state;
      const d = ensureDevice(state, data.device_id);
      if (data.machine_id) d.machine_id = data.machine_id;
      d.problems = d.problems.filter((p) => p.name !== data.problem.name).concat([data.problem]);
      d.wrong = true;
    } else if (name === "problem_cleared") {
      if (!data.device_id || typeof data.name !== "string") return state;
      const d = ensureDevice(state, data.device_id);
      d.problems = d.problems.filter((p) => p.name !== data.name);
      // `wrong` also depends on the diagnosis: the next record or check-in says.
      // Without problems and without an abnormal diagnosis nothing is wrong.
      if (!d.problems.length && !(d.mode === WATCHING && d.latest && isAbnormal(d.latest.diagnosis))) d.wrong = false;
    }
    return state;
  }

  function isAbnormal(diagnosis) {
    const k = diagnosisKind(diagnosis);
    return k === "fault" || k === "unrecognised";
  }

  // The device shown: the one picked by hand if it still exists, else the
  // one heard from most recently.
  function shownDeviceId(state) {
    if (state.selectedDeviceId && state.devices[state.selectedDeviceId]) {
      return state.selectedDeviceId;
    }
    let best = null;
    let bestT = -Infinity;
    for (const id of Object.keys(state.devices).sort()) {
      const d = state.devices[id];
      let t = d.lastHeardLocalMs;
      if (t == null) t = parseTime(d.last_seen);
      if (t == null || isNaN(t)) t = -Infinity + 1;
      if (best === null || t > bestT) {
        best = id;
        bestT = t;
      }
    }
    return best;
  }

  // 'none'          no device ever seen
  // 'disconnected'  platform says the device is not connected
  // 'checking_in'   connected and not watching: check-ins, no records
  // 'no_readings'   connected, but no record for 5 seconds (or never)
  // 'live'          a record arrived in the last 5 seconds
  function deviceStatus(state, id, nowMs) {
    const d = id ? state.devices[id] : null;
    if (!d) return "none";
    if (!d.connected) return "disconnected";
    if (d.mode && d.mode !== WATCHING) return "checking_in"; // no records outside watching, by design
    if (d.lastRecordLocalMs == null || nowMs - d.lastRecordLocalMs > STALE_MS) {
      return "no_readings";
    }
    return "live";
  }

  function modelFor(state, id) {
    const d = id ? state.devices[id] : null;
    if (!d) return null;
    const v = d.model_version || (d.latest && d.latest.model_version);
    return (v && state.models[v]) || null;
  }

  function latestRunForMachine(state, machineId) {
    if (!machineId) return null;
    let best = null;
    for (const id of Object.keys(state.runs)) {
      const entry = state.runs[id];
      if (entry.run.machine_id !== machineId) continue;
      if (!best) {
        best = entry;
        continue;
      }
      const a = parseTime(entry.run.at);
      const b = parseTime(best.run.at);
      if (a > b || (a === b && entry.order > best.order) || (isNaN(b) && !isNaN(a))) best = entry;
    }
    return best ? best.run : null;
  }

  // What the page's headline says (contract: `wrong` decides it, in every
  // mode). Only a live device can say nothing is wrong: a device that is
  // quiet, disconnected or behind a lost platform says nothing at all.
  //   {state: 'none' | 'waiting' | 'not_live' | 'wrong' | 'ok', text}
  function headline(state, id, nowMs) {
    const d = id ? state.devices[id] : null;
    if (!d) return { state: "none", text: "Waiting for a device" };
    const status = deviceStatus(state, id, nowMs);
    if (!state.platformUp || status === "disconnected" || status === "no_readings") {
      return { state: "not_live", text: "Not live" };
    }
    if (!d.latest && (d.mode === WATCHING || !d.mode)) return { state: "waiting", text: "Waiting for readings" };
    return d.wrong ? { state: "wrong", text: "Something is wrong" } : { state: "ok", text: "Nothing is wrong" };
  }

  // The model's panel, next to the problems. Never hidden by them.
  //   {kind, label, detail, note, possible}
  function modelLine(state, id) {
    const d = id ? state.devices[id] : null;
    if (!d) return null;
    if (d.mode && d.mode !== WATCHING) {
      return {
        kind: "none",
        label: "Model: not judging while " + modeName(d.mode).toLowerCase(),
        detail: "There is no diagnosis outside watching. The limits still run.",
        note: "",
        possible: false,
      };
    }
    const rec = d.latest;
    if (!rec) return null;
    const kind = diagnosisKind(rec.diagnosis);
    if (kind === "normal") {
      return { kind, label: "Model: nothing unusual yet", detail: "", note: rec.note || "", possible: false };
    }
    if (kind === "warming_up" || kind === "stopped") {
      return {
        kind,
        label: "Model: " + (kind === "stopped" ? "machine stopped" : "warming up"),
        detail: rec.note || "",
        note: "",
        possible: false,
      };
    }
    let possible = false;
    if (kind === "fault") {
      const model = modelFor(state, id);
      const types = (model && Array.isArray(model.fault_types)) ? model.fault_types : [];
      const t = types.find((x) => x && x.name === rec.diagnosis);
      possible = !t || t.validated !== true; // the contract: unvalidated types are "possible"
    }
    return { kind, label: "Model: " + displayName(rec.diagnosis), detail: rec.reason || "", note: rec.note || "", possible };
  }

  // The newest record's `features` (readings that moved, in spreads), with
  // the text the model announced for each. Order as sent.
  function movedReadings(state, id) {
    const d = id ? state.devices[id] : null;
    const rec = d ? d.latest : null;
    if (!rec || !rec.features || typeof rec.features !== "object") return [];
    const model = modelFor(state, id);
    const texts = {};
    for (const f of (model && model.features) || []) if (f && f.name) texts[f.name] = f.text;
    return Object.keys(rec.features)
      .filter((n) => typeof rec.features[n] === "number" && isFinite(rec.features[n]))
      .map((n) => ({ name: n, text: texts[n] || n, value: rec.features[n] }));
  }

  // Feature name -> its announced text, for the agent's cited readings.
  function featureTexts(state, id) {
    const out = {};
    const model = modelFor(state, id);
    for (const f of (model && model.features) || []) if (f && f.name) out[f.name] = f.text || f.name;
    return out;
  }

  // Which parts of what is on screen are simulated.
  function simulatedSources(state, id, run) {
    const out = [];
    const d = id ? state.devices[id] : null;
    if (d && d.latest && d.latest.simulated === true) out.push("readings");
    const m = modelFor(state, id);
    if (m && m.simulated === true) out.push("model");
    if (run && run.simulated === true) out.push("agent report");
    return out;
  }

  return {
    STALE_MS,
    HISTORY_CAP,
    WATCHING,
    displayName,
    diagnosisKind,
    modeName,
    kindLabel,
    problemValueText,
    createState,
    applySnapshot,
    applyEvent,
    setHistory,
    addRuns,
    shownDeviceId,
    deviceStatus,
    headline,
    modelLine,
    movedReadings,
    featureTexts,
    modelFor,
    latestRunForMachine,
    simulatedSources,
  };
});
