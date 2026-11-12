// Dashboard page: network and drawing. All decisions about state live in
// dashboard_logic.js. Text from the API only ever reaches the page through
// textContent or canvas fillText, never as HTML.
(function () {
  "use strict";
  const L = window.EffenteLogic;
  const state = L.createState();
  const SVG_NS = "http://www.w3.org/2000/svg";

  const ui = {
    putError: null,
    inflight: 0,
    loadedMachine: null,
    lastDiagKey: null,
    lastSwitchKey: null, // null until the first render, so nothing flashes on load
    es: null,
    heatTimer: null, // shows a record's next image when it is due
  };

  const $ = (id) => document.getElementById(id);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function fmtTime(iso) {
    const t = Date.parse(iso);
    if (isNaN(t)) return "";
    return new Date(t).toLocaleTimeString([], { hour12: false });
  }

  function agoText(ms) {
    if (ms == null) return "never";
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 90) return s + " s ago";
    const m = Math.round(s / 60);
    return m < 90 ? m + " min ago" : Math.round(m / 60) + " h ago";
  }

  // ---------------- network ----------------
  async function fetchJSON(url, opts) {
    const res = await fetch(url, Object.assign({ cache: "no-store" }, opts || {}));
    let body = null;
    try {
      body = await res.json();
    } catch (e) {
      body = null;
    }
    if (!res.ok || !body || body.ok === false) {
      const msg = (body && body.error) || "HTTP " + res.status;
      throw new Error(msg);
    }
    return body;
  }

  async function loadSnapshot() {
    try {
      const snap = await fetchJSON("/api/state");
      L.applySnapshot(state, snap, Date.now());
      await ensureMachineData(true);
    } catch (e) {
      // The stream's own error handler reports the platform as lost.
    }
    render();
  }

  function shownMachine() {
    const id = L.shownDeviceId(state);
    const d = id ? state.devices[id] : null;
    return d && d.latest ? d.latest.machine_id : null;
  }

  async function ensureMachineData(force) {
    const m = shownMachine();
    if (!m || (!force && m === ui.loadedMachine)) return;
    ui.loadedMachine = m;
    // Start empty: the fetch is the truth (a restarted platform may restart
    // seq), and records streamed in while it is out get merged in.
    state.history[m] = [];
    const enc = encodeURIComponent(m);
    try {
      const [recs, runs] = await Promise.all([
        fetchJSON("/api/machines/" + enc + "/records?limit=" + L.HISTORY_CAP),
        fetchJSON("/api/machines/" + enc + "/agent-runs?limit=20"),
      ]);
      L.setHistory(state, m, recs.records); // merges records already streamed in
      L.addRuns(state, runs.runs);
    } catch (e) {
      ui.loadedMachine = null; // retry on the next record
    }
    render();
  }

  function connectStream() {
    if (ui.es) ui.es.close();
    const es = new EventSource("/api/stream");
    ui.es = es;
    es.onopen = function () {
      state.platformUp = true;
      state.platformLost = false;
      loadSnapshot(); // every (re)connect starts again from /api/state
    };
    es.onerror = function () {
      state.platformUp = false;
      state.platformLost = true;
      render();
      if (es.readyState === EventSource.CLOSED) {
        setTimeout(connectStream, 2000);
      }
    };
    for (const name of ["record", "model", "mask", "agent_run", "device"]) {
      es.addEventListener(name, function (ev) {
        let data;
        try {
          data = JSON.parse(ev.data);
        } catch (e) {
          return;
        }
        L.applyEvent(state, name, data, Date.now());
        if (name === "record") ensureMachineData(false);
        scheduleRender();
      });
    }
  }

  async function sendToggle(deviceId, featureName) {
    const d = state.devices[deviceId];
    if (!d) return;
    const next = L.toggleMask(d.mask, featureName);
    ui.inflight += 1;
    ui.putError = null;
    render();
    try {
      const reply = await fetchJSON("/api/devices/" + encodeURIComponent(deviceId) + "/mask", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mask: next }),
      });
      L.applyMaskReply(state, reply);
    } catch (e) {
      ui.putError = "Could not send the switch-off: " + e.message;
    } finally {
      ui.inflight -= 1;
      render();
    }
  }

  // ---------------- glyphs ----------------
  function svgEl(tag, attrs) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const k of Object.keys(attrs)) e.setAttribute(k, attrs[k]);
    return e;
  }

  function glyphNodes(diag) {
    const W = "#fff";
    switch (diag) {
      case "normal":
        return [
          svgEl("circle", { cx: 50, cy: 50, r: 46, fill: "currentColor" }),
          svgEl("path", { d: "M28 52 L44 67 L73 35", fill: "none", stroke: W, "stroke-width": 11, "stroke-linecap": "round", "stroke-linejoin": "round" }),
        ];
      case "imbalance":
        return [
          svgEl("path", { d: "M50 5 L97 92 L3 92 Z", fill: "currentColor", "stroke-linejoin": "round" }),
          svgEl("rect", { x: 44, y: 33, width: 12, height: 32, rx: 3, fill: W }),
          svgEl("circle", { cx: 50, cy: 77, r: 6.5, fill: W }),
        ];
      case "loose_mount":
        return [
          svgEl("rect", { x: 6, y: 6, width: 88, height: 88, rx: 6, fill: "currentColor" }),
          svgEl("path", { d: "M20 40 L32 30 L44 40 L56 30 L68 40 L80 30 M20 68 L32 58 L44 68 L56 58 L68 68 L80 58", fill: "none", stroke: W, "stroke-width": 7, "stroke-linecap": "round", "stroke-linejoin": "round" }),
        ];
      case "unknown":
        return [
          svgEl("path", { d: "M31 4 H69 L96 31 V69 L69 96 H31 L4 69 V31 Z", fill: "currentColor" }),
          svgEl("path", { d: "M36 38 C36 22 64 22 64 38 C64 50 50 50 50 62", fill: "none", stroke: W, "stroke-width": 10, "stroke-linecap": "round" }),
          svgEl("circle", { cx: 50, cy: 78, r: 6.5, fill: W }),
        ];
      default:
        return [svgEl("circle", { cx: 50, cy: 50, r: 42, fill: "none", stroke: "currentColor", "stroke-width": 7, "stroke-dasharray": "12 10" })];
    }
  }

  function setGlyph(svg, diag) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    for (const n of glyphNodes(diag)) svg.appendChild(n);
  }

  function diagColor(diag) {
    return (
      {
        normal: css("--normal"),
        imbalance: css("--imbalance"),
        loose_mount: css("--loose"),
        unknown: css("--unknown"),
      }[diag] || css("--muted")
    );
  }

  // ---------------- render ----------------
  let renderQueued = false;
  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(function () {
      renderQueued = false;
      render();
    });
  }

  function render() {
    const now = Date.now();
    const id = L.shownDeviceId(state);
    const d = id ? state.devices[id] : null;
    const status = L.deviceStatus(state, id, now);
    const rec = d ? d.latest : null;
    const model = L.modelFor(state, id);
    const machine = rec ? rec.machine_id : null;
    const run = L.latestRunForMachine(state, machine);
    const live = state.platformUp && status === "live";

    document.body.classList.toggle("no-device", !d);
    document.body.classList.toggle("platform-lost", state.platformLost);
    document.body.classList.toggle("stale-all", !live);

    renderTop(id, d, status, now, run);
    renderHero(id, d, rec, status, now);
    renderFeatures(id, d, rec, model, live);
    drawSpectrum(rec, model, live);
    renderHeat(d, rec, machine, live, now);
    renderAgent(machine, run, model);
    renderHistory(machine, model);
  }

  function renderTop(id, d, status, now, run) {
    $("shown-device").textContent = id || "none";
    $("shown-machine").textContent = (d && d.latest && d.latest.machine_id) || "none";

    const sel = $("device-select");
    const ids = Object.keys(state.devices).sort();
    sel.hidden = ids.length < 2;
    if (!sel.hidden) {
      const want = ["", ...ids].join("|");
      if (sel.dataset.ids !== want) {
        sel.dataset.ids = want;
        sel.textContent = "";
        const auto = el("option", null, "Auto: most recent");
        auto.value = "";
        sel.appendChild(auto);
        for (const i of ids) {
          const o = el("option", null, i);
          o.value = i;
          sel.appendChild(o);
        }
      }
      sel.value = state.selectedDeviceId || "";
    }

    const dp = $("device-pill");
    const last = d ? (d.lastRecordLocalMs != null ? now - d.lastRecordLocalMs : null) : null;
    const pills = {
      none: ["idle", "No device yet"],
      live: ["ok", "Device live"],
      no_readings: ["warn", "Device quiet · no reading " + agoText(last)],
      disconnected: ["bad", "Device disconnected"],
    };
    const [cls, text] = pills[status];
    dp.className = "pill " + cls;
    dp.textContent = text;

    const pp = $("platform-pill");
    if (state.platformUp) {
      pp.className = "pill ok";
      pp.textContent = "Platform connected";
    } else if (state.platformLost) {
      pp.className = "pill bad";
      pp.textContent = "Platform lost";
    } else {
      pp.className = "pill idle";
      pp.textContent = "Connecting";
    }
    $("lost-banner").hidden = !state.platformLost;

    const sim = L.simulatedSources(state, id, run);
    $("sim-banner").hidden = sim.length === 0;
    $("sim-detail").textContent = sim.length ? "Not from a real sensor: " + sim.join(", ") : "";
  }

  // Shrink the diagnosis until it fits on one line. Never cut it off.
  function fitText(node) {
    node.style.fontSize = "";
    const base = parseFloat(getComputedStyle(node).fontSize);
    const avail = node.clientWidth;
    const need = node.scrollWidth;
    if (avail > 0 && need > avail) {
      node.style.fontSize = Math.floor(base * (avail / need) * 0.98) + "px";
    }
  }

  function renderHero(id, d, rec, status, now) {
    const hero = $("hero");
    const name = $("diag-name");
    const reason = $("reason");
    const meta = $("meta");
    const tag = $("stale-tag");
    let diag = "";

    if (!d) {
      name.textContent = "Waiting for a device";
      reason.textContent = state.snapshotLoaded
        ? "No device has connected to the platform yet."
        : "Connecting to the platform…";
      reason.className = "reason none";
      meta.textContent = "";
    } else if (!rec) {
      name.textContent = "Waiting for readings";
      reason.textContent = "Device " + id + " has not sent a reading yet.";
      reason.className = "reason none";
      meta.textContent = "";
    } else {
      diag = rec.diagnosis;
      name.textContent = L.displayName(rec.diagnosis);
      reason.textContent = rec.reason || "";
      reason.className = "reason";
      const bits = [];
      if (typeof rec.confidence === "number") bits.push("confidence " + Math.round(rec.confidence * 100) + "%");
      if (typeof rec.unknown_score === "number") bits.push("unknown score " + rec.unknown_score.toFixed(2));
      if (rec.model_version) bits.push("model " + rec.model_version);
      if (rec.at) bits.push("at " + fmtTime(rec.at));
      meta.textContent = bits.join("  ·  ");
    }
    hero.dataset.diag = diag;
    setGlyph($("diag-glyph"), diag);
    fitText(name);

    const stale = !!rec && (status !== "live" || !state.platformUp);
    hero.classList.toggle("stale", stale);
    tag.hidden = !stale;
    if (stale) {
      const ago = agoText(d.lastRecordLocalMs != null ? now - d.lastRecordLocalMs : null);
      if (!state.platformUp) {
        tag.textContent = state.platformLost ? "NOT LIVE · platform lost" : "NOT LIVE · connecting";
        tag.className = "stale-tag lost";
      } else if (status === "disconnected") {
        tag.textContent = "NOT LIVE · device disconnected · last reading " + ago;
        tag.className = "stale-tag lost";
      } else {
        tag.textContent = "NOT LIVE · last reading " + ago;
        tag.className = "stale-tag";
      }
    }

    const key = rec ? rec.diagnosis : "";
    if (ui.lastDiagKey && rec && key !== ui.lastDiagKey) {
      hero.classList.remove("flash");
      void hero.offsetWidth;
      hero.classList.add("flash");
    }
    ui.lastDiagKey = key;

    const so = L.switchOffSummary(state, id);
    const box = $("switchoff");
    box.hidden = !so;
    if (so) {
      const title = "Switched off: " + so.features.map((f) => f.sentence).join(" + ");
      $("switchoff-title").textContent = title;
      const ch = $("switchoff-change");
      ch.textContent = "";
      if (so.before) {
        ch.append("With all features on: ");
        ch.append(el("b", null, L.displayName(so.before)));
        ch.append(el("span", "arrow", "→"));
        ch.append("now: ");
        ch.append(el("b", null, L.displayName(so.now)));
        if (!so.changed) ch.append(" (unchanged)");
      } else {
        ch.append("Now: ");
        ch.append(el("b", null, L.displayName(so.now)));
      }
      const skey = so.features.map((f) => f.name).join(",");
      if (ui.lastSwitchKey !== null && skey !== ui.lastSwitchKey) {
        box.classList.remove("fresh");
        void box.offsetWidth;
        box.classList.add("fresh");
      }
      ui.lastSwitchKey = skey;
    } else if (rec) {
      ui.lastSwitchKey = "";
    }
  }

  const featureRows = new Map();

  function renderFeatures(id, d, rec, model, live) {
    const list = $("feature-list");
    const note = $("feature-note");
    const feats = (model && Array.isArray(model.features)) ? model.features : [];

    if (!d || !model) {
      featureRows.clear();
      list.textContent = "";
      const msg = !d
        ? "Features appear when a device announces its model."
        : "Model " + (d.model_version || "?") + " not announced yet, so its features are not known.";
      list.appendChild(el("div", "empty", msg));
      note.textContent = "";
      note.className = "panel-note";
      return;
    }
    if (feats.length === 0) {
      list.textContent = "";
      featureRows.clear();
      list.appendChild(el("div", "empty", "This model has no verified features."));
    }

    const names = feats.map((f) => f.name);
    for (const n of Array.from(featureRows.keys())) {
      if (!names.includes(n)) {
        featureRows.get(n).root.remove();
        featureRows.delete(n);
      }
    }
    const empty = list.querySelector(".empty");
    if (empty && feats.length) empty.remove();

    const acked = rec ? rec.mask : [];
    let anyPending = false;
    for (const f of feats) {
      let row = featureRows.get(f.name);
      if (!row) {
        row = buildFeatureRow(f);
        featureRows.set(f.name, row);
      }
      list.appendChild(row.root); // keeps model order
      row.sentence.textContent = f.sentence;
      row.name.textContent = f.name;
      const st = L.featureStatus(d.mask, acked, f.name);
      if (st.startsWith("pending")) anyPending = true;
      row.root.classList.toggle("is-off", st === "off");
      const v = rec && rec.features ? rec.features[f.name] : undefined;
      const has = typeof v === "number";
      row.fill.style.width = has ? Math.max(0, Math.min(1, v)) * 100 + "%" : "0%";
      row.val.textContent = has ? v.toFixed(2) : "–";
      row.btn.dataset.status = st;
      row.btn.dataset.device = id;
      row.state.textContent = L.featureStatusLabel(st);
      row.hint.textContent = {
        on: "click to switch off",
        off: "click to switch back on",
        pending_off: "waiting for the device",
        pending_on: "waiting for the device",
      }[st];
      row.btn.disabled = !state.platformUp;
      row.btn.setAttribute("aria-pressed", st === "off" || st === "pending_off" ? "true" : "false");
      row.btn.setAttribute("aria-label", "Feature " + f.sentence + ": " + L.featureStatusLabel(st));
    }

    if (ui.putError) {
      note.textContent = ui.putError;
      note.className = "panel-note error";
    } else if (anyPending) {
      note.textContent = "Pending until a reading from the device shows the new mask.";
      note.className = "panel-note";
    } else if (!live) {
      note.textContent = "Activity bars show the last reading, not live.";
      note.className = "panel-note";
    } else {
      note.textContent = "Bars: feature activity in the latest reading, 0 to 1.";
      note.className = "panel-note";
    }
  }

  function buildFeatureRow(f) {
    const root = el("div", "feature");
    const text = el("div", "feature-text");
    const sentence = el("div", "feature-sentence");
    const name = el("div", "feature-name");
    text.append(sentence, name);
    const meter = el("div", "meter");
    const bar = el("div", "bar");
    const fill = el("div", "bar-fill");
    const val = el("div", "bar-val");
    bar.append(fill);
    meter.append(bar, val);
    const btn = el("button", "toggle");
    btn.type = "button";
    const st = el("span", "toggle-state");
    const hint = el("span", "toggle-hint");
    btn.append(st, hint);
    btn.addEventListener("click", function () {
      sendToggle(btn.dataset.device, f.name);
    });
    root.append(text, meter, btn);
    return { root, sentence, name, fill, val, btn, state: st, hint };
  }

  // ---------------- canvases ----------------
  function prepCanvas(canvas) {
    const r = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(r.width * dpr));
    const h = Math.max(1, Math.round(r.height * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    return { ctx, w: r.width, h: r.height };
  }

  function remPx() {
    return parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  }

  function niceStep(max, target) {
    const raw = max / target;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const m of [1, 2, 2.5, 5, 10]) if (raw <= m * p) return m * p;
    return 10 * p;
  }

  function drawSpectrum(rec, model, live) {
    const { ctx, w, h } = prepCanvas($("spectrum-canvas"));
    const rem = remPx();
    const ink = css("--ink");
    const muted = css("--muted");
    const line = css("--line");
    const padL = 3.2 * rem, padR = 1 * rem, padT = 0.6 * rem, padB = 2.4 * rem;
    const pw = w - padL - padR, ph = h - padT - padB;
    const hzMax = model && typeof model.spectrum_hz_max === "number" ? model.spectrum_hz_max : null;
    const spec = rec && Array.isArray(rec.spectrum) ? rec.spectrum : null;

    ctx.font = "600 " + 0.95 * rem + "px " + css("--mono");
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    for (const v of [0, 0.25, 0.5, 0.75, 1]) {
      const y = padT + ph * (1 - v);
      ctx.strokeStyle = line;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + pw, y);
      ctx.stroke();
      ctx.fillStyle = muted;
      ctx.fillText(v.toFixed(2), padL - 0.4 * rem, y);
    }

    if (spec && spec.length) {
      const n = spec.length;
      const bw = pw / n;
      ctx.fillStyle = live ? css("--spectrum") : css("--stale");
      for (let i = 0; i < n; i++) {
        const v = Math.max(0, Math.min(1, Number(spec[i]) || 0));
        const bh = v * ph;
        ctx.fillRect(padL + i * bw + bw * 0.12, padT + ph - bh, bw * 0.76, bh);
      }
    }

    ctx.strokeStyle = ink;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(padL, padT);
    ctx.lineTo(padL, padT + ph);
    ctx.lineTo(padL + pw, padT + ph);
    ctx.stroke();

    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillStyle = ink;
    if (hzMax) {
      const step = niceStep(hzMax, Math.max(3, Math.floor(pw / (5.5 * rem))));
      for (let f = 0; f <= hzMax + 1e-9; f += step) {
        const x = padL + (f / hzMax) * pw;
        ctx.fillRect(x - 1, padT + ph, 2, 0.4 * rem);
        ctx.fillText(String(Math.round(f)), x, padT + ph + 0.55 * rem);
      }
      ctx.textAlign = "right";
      ctx.fillText("Hz", padL + pw, padT + ph + 1.4 * rem);
    } else {
      ctx.fillStyle = muted;
      ctx.fillText("frequency axis unknown until the model is announced", padL + pw / 2, padT + ph + 0.55 * rem);
    }

    if (!spec) {
      ctx.fillStyle = muted;
      ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      ctx.font = "600 " + 1.4 * rem + "px " + css("--sans");
      ctx.fillText(rec ? "This reading has no spectrum" : "No readings yet", padL + pw / 2, padT + ph / 2);
    }

    $("spectrum-note").textContent = spec
      ? (live ? "latest reading" : "last reading, not live") + " · " + spec.length + " bins · fixed 0 to 1 scale"
      : "";
  }

  // ---------------- heat (contract 3.3) ----------------
  const fmtC = (v) => (typeof v === "number" && isFinite(v) ? v.toFixed(1) + " °C" : "none");

  function heatFact(box, k, v, warn) {
    box.appendChild(el("span", "k", k));
    box.appendChild(el("span", "v" + (warn ? " warn" : ""), v));
  }

  function renderHeat(d, rec, machine, live, now) {
    clearTimeout(ui.heatTimer);
    ui.heatTimer = null;
    const v = L.heatView(rec);
    const facts = $("heat-facts");
    facts.textContent = "";
    $("heat-imu").hidden = v.kind !== "imu";
    const wrap = $("heat-wrap");
    wrap.hidden = v.kind === "imu";
    wrap.classList.toggle("fit", v.kind === "camera");

    if (v.kind === "imu") {
      $("heat-note").textContent = live ? "optional thermal camera" : "last reading, not live";
      $("heat-imu-v").textContent = fmtC(v.imuTempC);
      const t = L.imuTrend((machine && state.history[machine]) || []);
      $("heat-imu-trend").textContent = t
        ? (t.deltaC >= 0 ? "+" : "−") + Math.abs(t.deltaC).toFixed(1) + " °C in the last " + Math.round(t.spanMs / 60000) + " min"
        : "change shows after a minute of readings";
      return;
    }

    const rem = remPx();
    const barW = 0.8 * rem, barGap = 0.6 * rem, labelW = 3.6 * rem;
    // An image keeps the canvas just tall enough for it, so the facts sit right under it.
    wrap.style.height = v.kind === "camera"
      ? Math.max(0, (wrap.getBoundingClientRect().width - barW - barGap - labelW) * (L.HEAT_H / L.HEAT_W)) + "px"
      : "";
    const { ctx, w, h } = prepCanvas($("heat-canvas"));
    if (v.kind !== "camera") {
      $("heat-note").textContent = "";
      ctx.fillStyle = css("--muted");
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = "600 " + 1.2 * rem + "px " + css("--sans");
      const msg = !rec ? "No readings yet" : v.kind === "none" ? "This device sends no heat reading" : "This heat reading cannot be shown";
      ctx.fillText(msg, w / 2, h / 2);
      return;
    }

    const elapsed = d && d.lastRecordLocalMs != null ? now - d.lastRecordLocalMs : Infinity;
    const pick = L.frameAt(v.frames, elapsed);
    const frame = pick.index >= 0 ? v.frames[pick.index] : null;
    if (live && pick.nextMs != null) ui.heatTimer = setTimeout(scheduleRender, pick.nextMs + 5);
    const range = L.heatRange(v);

    // Layout: the image at 4:3 on the left, a colour bar on the right.
    const availW = Math.max(1, w - barW - barGap - labelW);
    const cell = Math.max(1, Math.min(availW / L.HEAT_W, h / L.HEAT_H));
    const iw = cell * L.HEAT_W, ih = cell * L.HEAT_H;
    const ix = 0, iy = 0;
    const scale = (t) => (t - range.lo) / (range.hi - range.lo);

    ctx.save();
    if (!live) ctx.filter = "grayscale(1) opacity(0.6)";
    if (frame) {
      const img = ctx.createImageData(L.HEAT_W, L.HEAT_H);
      for (let i = 0; i < frame.pixels.length; i++) {
        const c = L.heatColor(scale(frame.pixels[i]));
        img.data[i * 4] = c[0];
        img.data[i * 4 + 1] = c[1];
        img.data[i * 4 + 2] = c[2];
        img.data[i * 4 + 3] = 255;
      }
      const off = document.createElement("canvas");
      off.width = L.HEAT_W;
      off.height = L.HEAT_H;
      off.getContext("2d").putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = false; // one block per real pixel: no detail the camera did not see
      ctx.drawImage(off, ix, iy, iw, ih);
    } else {
      // No images in this reading: the 8 x 6 grid of block averages.
      const bw = iw / L.HEAT_COLS, bh = ih / L.HEAT_ROWS;
      for (let i = 0; i < v.cells.length; i++) {
        const c = L.heatColor(scale(v.backgroundC + v.cells[i]));
        ctx.fillStyle = "rgb(" + c.join(",") + ")";
        ctx.fillRect(ix + (i % L.HEAT_COLS) * bw, iy + Math.floor(i / L.HEAT_COLS) * bh, bw, bh);
      }
      ctx.strokeStyle = "rgba(255,255,255,0.35)";
      ctx.lineWidth = 1;
      for (let c = 1; c < L.HEAT_COLS; c++) {
        ctx.beginPath(); ctx.moveTo(ix + c * bw, iy); ctx.lineTo(ix + c * bw, iy + ih); ctx.stroke();
      }
      for (let r = 1; r < L.HEAT_ROWS; r++) {
        ctx.beginPath(); ctx.moveTo(ix, iy + r * bh); ctx.lineTo(ix + iw, iy + r * bh); ctx.stroke();
      }
    }
    ctx.restore();

    // Hottest pixel: a ring, so it reads in both colour and shape.
    if (v.hotRow != null && v.hotCol != null) {
      const cx = ix + (v.hotCol + 0.5) * cell, cy = iy + (v.hotRow + 0.5) * cell;
      const r = Math.max(cell * 1.3, 0.7 * rem);
      ctx.lineWidth = 0.32 * rem;
      ctx.strokeStyle = "#000";
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke();
      ctx.lineWidth = 0.16 * rem;
      ctx.strokeStyle = "#fff";
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke();
    }

    if (v.frozen) {
      ctx.fillStyle = css("--hazard");
      ctx.fillRect(ix, iy, iw, 1.9 * rem);
      ctx.fillStyle = "#111";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const msg = "FROZEN: CAMERA STUCK";
      let size = rem;
      ctx.font = "800 " + size + "px " + css("--sans");
      const fit = (iw - 0.8 * rem) / ctx.measureText(msg).width;
      if (fit < 1) ctx.font = "800 " + size * fit + "px " + css("--sans");
      ctx.fillText(msg, ix + iw / 2, iy + 0.95 * rem);
    }

    // Colour bar with its range.
    const bx = ix + iw + barGap;
    const grad = ctx.createLinearGradient(0, iy + ih, 0, iy);
    for (let k = 0; k <= 8; k++) grad.addColorStop(k / 8, "rgb(" + L.heatColor(k / 8).join(",") + ")");
    ctx.save();
    if (!live) ctx.filter = "grayscale(1) opacity(0.6)";
    ctx.fillStyle = grad;
    ctx.fillRect(bx, iy, barW, ih);
    ctx.restore();
    ctx.strokeStyle = css("--ink");
    ctx.lineWidth = 1.5;
    ctx.strokeRect(bx, iy, barW, ih);
    ctx.fillStyle = css("--ink");
    ctx.font = "700 " + 0.95 * rem + "px " + css("--sans");
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(range.hi + " °C", bx + barW + 0.35 * rem, iy);
    ctx.textBaseline = "bottom";
    ctx.fillText(range.lo + " °C", bx + barW + 0.35 * rem, iy + ih);

    $("heat-note").textContent = live
      ? "MLX90640 · " + (frame ? "image " + (pick.index + 1) + " of " + v.frames.length + " in this reading" : "8 × 6 grid, no image in this reading")
      : "last reading, not live";
    heatFact(facts, "Hottest", fmtC(v.maxC));
    if (v.hotRow != null && v.hotCol != null) heatFact(facts, "Where", "row " + (v.hotRow + 1) + ", col " + (v.hotCol + 1) + " (ringed)");
    heatFact(facts, "Background", fmtC(v.backgroundC));
    heatFact(facts, "View", v.frozen ? "frozen: camera stuck" : v.sceneMatch == null ? "usual view not learned" : v.sceneMatch.toFixed(2) + " match to usual", v.frozen);
  }

  const DASHES = [[], [10, 6], [3, 5], [14, 5, 3, 5]];

  function renderHistory(machine, model) {
    const recs = (machine && state.history[machine]) || [];
    const feats = (model && Array.isArray(model.features)) ? model.features : [];
    $("history-note").textContent = machine
      ? machine + " · last " + recs.length + " readings" +
        (recs.length ? " · " + fmtTime(recs[0].at) + " to " + fmtTime(recs[recs.length - 1].at) : "")
      : "";

    const legend = $("history-legend");
    const lkey = feats.map((f) => f.name + "=" + f.sentence).join("|");
    if (legend.dataset.key !== lkey) {
      legend.dataset.key = lkey;
      legend.textContent = "";
      for (const dg of ["normal", "imbalance", "loose_mount", "unknown"]) {
        const it = el("span", "legend-item");
        const s = svgEl("svg", { viewBox: "0 0 100 100" });
        s.style.color = diagColor(dg);
        for (const n of glyphNodes(dg)) s.appendChild(n);
        it.append(s, L.displayName(dg));
        legend.appendChild(it);
      }
      feats.forEach(function (f, i) {
        const it = el("span", "legend-item");
        const s = svgEl("svg", { viewBox: "0 0 40 10", width: "40", height: "10" });
        s.style.width = "2.2rem";
        s.appendChild(svgEl("line", { x1: 0, y1: 5, x2: 40, y2: 5, stroke: "currentColor", "stroke-width": 3, "stroke-dasharray": DASHES[i % DASHES.length].join(" ") }));
        it.append(s, f.sentence);
        legend.appendChild(it);
      });
    }

    const { ctx, w, h } = prepCanvas($("history-canvas"));
    const rem = remPx();
    const muted = css("--muted");
    const ink = css("--ink");
    const padL = 3.2 * rem, padR = 1 * rem;
    const stripT = 0, stripH = Math.max(1.6 * rem, h * 0.26);
    const lineT = stripH + 0.6 * rem, lineB = h - 0.2 * rem;
    const pw = w - padL - padR, lh = lineB - lineT;

    ctx.font = "600 " + 0.85 * rem + "px " + css("--mono");
    ctx.fillStyle = muted;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText("diag", padL - 0.4 * rem, stripT + stripH / 2);
    ctx.fillText("1", padL - 0.4 * rem, lineT + 0.4 * rem);
    ctx.fillText("0", padL - 0.4 * rem, lineB - 0.4 * rem);

    if (!recs.length) {
      ctx.textAlign = "center";
      ctx.font = "600 " + 1.2 * rem + "px " + css("--sans");
      ctx.fillText(machine ? "No readings yet" : "No machine yet", padL + pw / 2, h / 2);
      return;
    }

    const n = recs.length;
    const cw = pw / Math.max(n, 1);
    const xOf = (i) => padL + (i + 0.5) * cw;

    // switched-off periods, hatched
    ctx.save();
    ctx.beginPath();
    ctx.rect(padL, lineT, pw, lh);
    ctx.clip();
    for (let i = 0; i < n; i++) {
      if (Array.isArray(recs[i].mask) && recs[i].mask.length) {
        const x0 = padL + i * cw;
        ctx.fillStyle = "rgba(18,20,23,0.08)";
        ctx.fillRect(x0, lineT, cw + 0.5, lh);
        ctx.strokeStyle = "rgba(18,20,23,0.25)";
        ctx.lineWidth = 1;
        for (let y = lineT - cw; y < lineB + cw; y += 8) {
          ctx.beginPath();
          ctx.moveTo(x0, y + cw);
          ctx.lineTo(x0 + cw, y);
          ctx.stroke();
        }
      }
    }
    ctx.restore();

    // diagnosis strip, with names written in each long run
    let start = 0;
    for (let i = 1; i <= n; i++) {
      if (i === n || recs[i].diagnosis !== recs[start].diagnosis) {
        const dg = recs[start].diagnosis;
        const x0 = padL + start * cw, x1 = padL + i * cw;
        ctx.fillStyle = diagColor(dg);
        ctx.fillRect(x0, stripT, x1 - x0 + 0.5, stripH);
        if (dg === "unknown") {
          ctx.save();
          ctx.beginPath();
          ctx.rect(x0, stripT, x1 - x0, stripH * 0.22);
          ctx.clip();
          ctx.fillStyle = css("--hazard");
          ctx.fillRect(x0, stripT, x1 - x0, stripH);
          ctx.restore();
        }
        const label = L.displayName(dg);
        ctx.font = "800 " + 0.95 * rem + "px " + css("--sans");
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = "#fff";
        if (ctx.measureText(label).width + 0.8 * rem < x1 - x0) {
          ctx.fillText(label, (x0 + x1) / 2, stripT + stripH / 2);
        } else if (x1 - x0 > 1.1 * rem) {
          ctx.fillText(label.charAt(0), (x0 + x1) / 2, stripT + stripH / 2);
        }
        start = i;
      }
    }
    // separators between runs, so neighbouring runs read as distinct without colour
    ctx.fillStyle = css("--panel");
    for (let i = 1; i < n; i++) {
      if (recs[i].diagnosis !== recs[i - 1].diagnosis) ctx.fillRect(padL + i * cw - 1, stripT, 2, stripH);
    }

    // feature activity lines
    ctx.strokeStyle = css("--line");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, lineB);
    ctx.lineTo(padL + pw, lineB);
    ctx.moveTo(padL, lineT);
    ctx.lineTo(padL + pw, lineT);
    ctx.stroke();
    feats.forEach(function (f, fi) {
      ctx.strokeStyle = ink;
      ctx.lineWidth = 2.5;
      ctx.setLineDash(DASHES[fi % DASHES.length]);
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < n; i++) {
        const v = recs[i].features ? recs[i].features[f.name] : undefined;
        if (typeof v !== "number") {
          pen = false;
          continue;
        }
        const y = lineB - Math.max(0, Math.min(1, v)) * lh;
        if (pen) ctx.lineTo(xOf(i), y);
        else ctx.moveTo(xOf(i), y);
        pen = true;
      }
      ctx.stroke();
    });
    ctx.setLineDash([]);
  }

  // ---------------- agent ----------------
  function compact(v) {
    try {
      return typeof v === "string" ? v : JSON.stringify(v);
    } catch (e) {
      return "";
    }
  }

  function renderAgent(machine, run, model) {
    const body = $("agent-body");
    const tags = $("agent-tags");
    tags.textContent = "";
    body.textContent = "";
    if (!machine) {
      body.appendChild(el("div", "empty", "No machine yet."));
      return;
    }
    if (!run) {
      body.appendChild(el("div", "empty", "No agent run yet for " + machine + ". A person starts the agent."));
      return;
    }
    const done = run.status === "done";
    if (run.simulated === true) tags.appendChild(el("span", "tag sim", "Simulated"));
    if (!done) tags.appendChild(el("span", "tag dark", "Running"));
    else if (run.ok === true) tags.appendChild(el("span", "tag", "Done · checks passed"));
    else tags.appendChild(el("span", "tag bad", "Done · no report"));

    const metaBits = [fmtTime(run.at), run.model, run.run_id];
    if (run.usage && typeof run.usage.input_tokens === "number") {
      metaBits.push(run.usage.input_tokens + " in / " + (run.usage.output_tokens || 0) + " out tokens");
    }
    body.appendChild(el("div", "agent-meta", metaBits.filter(Boolean).join("  ·  ")));

    const calls = Array.isArray(run.tool_calls) ? run.tool_calls : [];
    const SHOW = 6;
    const ol = el("ol", "tools");
    if (calls.length > SHOW) {
      body.appendChild(el("div", "tools-more", calls.length - SHOW + " earlier tool calls not shown"));
      ol.style.counterReset = "tool " + (calls.length - SHOW);
    }
    for (const c of calls.slice(-SHOW)) {
      const li = el("li");
      li.append(el("span", "tool-name", c.name || "?"));
      li.append(el("span", "tool-io", compact(c.input) + "  →  " + compact(c.output)));
      li.title = (c.name || "") + " " + compact(c.input) + "\n" + compact(c.output);
      ol.appendChild(li);
    }
    if (!done) {
      const li = el("li", "waiting");
      li.append(el("span", "tool-name", "working…"), el("span", "tool-io", ""));
      ol.appendChild(li);
    }
    if (calls.length || !done) body.appendChild(ol);
    if (!calls.length && done) body.appendChild(el("div", "tools-more", "No tool calls."));

    if (!done) return;
    const box = el("div", "report");
    if (run.ok === true && run.report) {
      const r = run.report;
      box.appendChild(el("div", "report-summary", r.summary || ""));
      box.appendChild(reportRow("Priority", priorityNode(r.priority)));
      const sentences = {};
      for (const f of (model && model.features) || []) sentences[f.name] = f.sentence;
      const cited = Array.isArray(r.cited_features) ? r.cited_features : [];
      box.appendChild(reportRow("Cited features", cited.length
        ? cited.map((n) => (sentences[n] ? sentences[n] + " (" + n + ")" : n)).join(", ")
        : "none"));
      box.appendChild(reportRow("Action", r.recommended_action || "none given"));
      box.appendChild(reportRow("Part reference", r.part_reference || "none given"));
    } else {
      box.appendChild(el("div", "fail-reason", run.reason || "No report: the run did not pass its checks."));
      const probs = Array.isArray(run.problems) ? run.problems : [];
      if (probs.length) {
        const ul = el("ul", "problems");
        for (const p of probs) ul.appendChild(el("li", null, compact(p)));
        box.appendChild(ul);
      }
    }
    body.appendChild(box);
  }

  function reportRow(k, v) {
    const row = el("div", "report-row");
    row.appendChild(el("span", "report-k", k));
    const val = el("span", "report-v");
    if (typeof v === "string") val.textContent = v;
    else val.appendChild(v);
    row.appendChild(val);
    return row;
  }

  function priorityNode(p) {
    const level = { none: 0, low: 1, medium: 2, high: 3 }[p];
    const wrap = el("span");
    const bars = el("span", "priority");
    for (let i = 1; i <= 3; i++) bars.appendChild(el("i", level >= i ? "on" : ""));
    wrap.append(bars, el("span", "priority-word", p || "not given"));
    return wrap;
  }

  // ---------------- start ----------------
  $("device-select").addEventListener("change", function (e) {
    state.selectedDeviceId = e.target.value || null;
    ensureMachineData(false);
    render();
  });
  window.addEventListener("resize", scheduleRender);
  setInterval(render, 1000); // staleness ages even when nothing arrives

  render();
  fetchJSON("/api/state")
    .then(function (snap) {
      L.applySnapshot(state, snap, Date.now());
      return ensureMachineData(true);
    })
    .catch(function () {
      state.platformLost = true; // say so rather than spin forever
    })
    .finally(function () {
      render();
      connectStream();
    });
})();
