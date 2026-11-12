// Pure dashboard logic: no DOM, no network. Loaded by index.html as a plain
// script (exposes window.EffenteLogic) and by node --test via require().
// Shapes follow platform/CONTRACT.md section 5.
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.EffenteLogic = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DIAGNOSIS_NAMES = {
    normal: "Normal",
    imbalance: "Imbalance",
    loose_mount: "Loose mount",
    unknown: "Unknown, inspect",
  };

  // Contract: connected means heard from in the last 5 seconds. The brief adds
  // "no record for 5 seconds" as its own stale condition.
  const STALE_MS = 5000;
  const HISTORY_CAP = 300;

  function displayName(diagnosis) {
    if (Object.prototype.hasOwnProperty.call(DIAGNOSIS_NAMES, diagnosis)) {
      return DIAGNOSIS_NAMES[diagnosis];
    }
    // Not in the contract's list: show it as-is rather than guess a meaning.
    return diagnosis == null ? "" : String(diagnosis);
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
        mask: [], // desired mask, as the platform holds it
        mask_version: 0,
        latest: null, // newest record
        lastRecordLocalMs: null, // browser clock time the newest record counts as received
        lastHeardLocalMs: null,
        baselineDiagnosis: null, // diagnosis of the newest record with an empty mask
      };
    }
    return state.devices[id];
  }

  function asList(v) {
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
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
      d.mask = asList(src.mask);
      d.mask_version = typeof src.mask_version === "number" ? src.mask_version : 0;
      d.lastHeardLocalMs = ago(src.last_seen);
      if (src.latest && typeof src.latest === "object") {
        d.latest = src.latest;
        d.lastRecordLocalMs = ago(src.latest.received_at || src.latest.at);
        if (asList(src.latest.mask).length === 0) d.baselineDiagnosis = src.latest.diagnosis;
        if (!d.model_version && src.latest.model_version) d.model_version = src.latest.model_version;
      } else {
        d.latest = null;
        d.lastRecordLocalMs = null;
      }
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
      d.last_seen = data.received_at || data.at || d.last_seen;
      if (data.model_version) d.model_version = data.model_version;
      if (asList(data.mask).length === 0) d.baselineDiagnosis = data.diagnosis;
      addHistoryRecord(state, data);
    } else if (name === "model") {
      if (data.model_version) state.models[data.model_version] = data;
      if (data.device_id) {
        const d = ensureDevice(state, data.device_id);
        d.model_version = data.model_version || d.model_version;
        d.lastHeardLocalMs = nowMs;
      }
    } else if (name === "mask") {
      if (!data.device_id) return state;
      applyMaskReply(state, data);
    } else if (name === "agent_run") {
      addRun(state, data);
    } else if (name === "device") {
      if (!data.device_id) return state;
      const d = ensureDevice(state, data.device_id);
      d.connected = !!data.connected;
      if (data.last_seen) d.last_seen = data.last_seen;
      if (data.connected) d.lastHeardLocalMs = nowMs;
    }
    return state;
  }

  // Reply to PUT mask, or the `mask` event (same shape).
  function applyMaskReply(state, reply) {
    const d = ensureDevice(state, reply.device_id);
    const v = typeof reply.mask_version === "number" ? reply.mask_version : null;
    // Ignore an older reply arriving after a newer event.
    if (v !== null && v < d.mask_version) return state;
    d.mask = asList(reply.mask);
    if (v !== null) d.mask_version = v;
    return state;
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
  // 'no_readings'   connected, but no record for 5 seconds (or never)
  // 'live'          a record arrived in the last 5 seconds
  function deviceStatus(state, id, nowMs) {
    const d = id ? state.devices[id] : null;
    if (!d) return "none";
    if (!d.connected) return "disconnected";
    if (d.lastRecordLocalMs == null || nowMs - d.lastRecordLocalMs > STALE_MS) {
      return "no_readings";
    }
    return "live";
  }

  // New mask list after clicking a feature's toggle: switch it off if it is
  // on, back on if it is off. Order of the other names is kept.
  function toggleMask(mask, name) {
    const list = asList(mask);
    if (list.includes(name)) return list.filter((n) => n !== name);
    return list.concat([name]);
  }

  // desired = the platform's mask, acked = mask of the newest record.
  function featureStatus(desired, acked, name) {
    const want = asList(desired).includes(name);
    const has = asList(acked).includes(name);
    if (want && has) return "off";
    if (!want && !has) return "on";
    return want ? "pending_off" : "pending_on";
  }

  const FEATURE_STATUS_LABELS = {
    on: "On",
    off: "Switched off",
    pending_off: "Switching off…",
    pending_on: "Switching on…",
  };

  function featureStatusLabel(status) {
    return FEATURE_STATUS_LABELS[status] || "";
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

  // What the switch-off banner says. Only uses names and sentences the
  // device announced and diagnoses the device sent; never invents a link
  // between a feature and a diagnosis.
  function switchOffSummary(state, id) {
    const d = id ? state.devices[id] : null;
    if (!d || !d.latest) return null;
    const off = asList(d.latest.mask);
    if (off.length === 0) return null;
    const model = modelFor(state, id);
    const sentences = {};
    for (const f of (model && model.features) || []) sentences[f.name] = f.sentence;
    return {
      features: off.map((n) => ({ name: n, sentence: sentences[n] || n })),
      before: d.baselineDiagnosis,
      now: d.latest.diagnosis,
      changed: d.baselineDiagnosis != null && d.baselineDiagnosis !== d.latest.diagnosis,
    };
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
    DIAGNOSIS_NAMES,
    STALE_MS,
    HISTORY_CAP,
    displayName,
    createState,
    applySnapshot,
    applyEvent,
    applyMaskReply,
    setHistory,
    addRuns,
    shownDeviceId,
    deviceStatus,
    toggleMask,
    featureStatus,
    featureStatusLabel,
    modelFor,
    latestRunForMachine,
    switchOffSummary,
    simulatedSources,
  };
});
