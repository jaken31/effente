// Dashboard page: network and drawing. All decisions about state live in
// dashboard_logic.js. Text from the API only ever reaches the page through
// textContent or canvas fillText, never as HTML.
(function () {
  "use strict";
  const L = window.EffenteLogic;
  const state = L.createState();
  const SVG_NS = "http://www.w3.org/2000/svg";

  const ui = {
    loadedMachine: null,
    lastHeadKey: null, // null until the first render, so nothing flashes on load
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
    return d ? d.machine_id || (d.latest && d.latest.machine_id) || null : null;
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
    for (const name of ["record", "model", "agent_run", "device", "problem", "problem_cleared"]) {
      es.addEventListener(name, function (ev) {
        let data;
        try {
          data = JSON.parse(ev.data);
        } catch (e) {
          return;
        }
        L.applyEvent(state, name, data, Date.now());
        if (name === "record" || name === "device") ensureMachineData(false);
        scheduleRender();
      });
    }
  }

  // ---------------- glyphs ----------------
  // Every colour on the page comes with a shape, so nothing relies on colour alone.
  function svgEl(tag, attrs) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const k of Object.keys(attrs)) e.setAttribute(k, attrs[k]);
    return e;
  }

  const W = "#fff";
  const GLYPHS = {
    ok: () => [
      svgEl("circle", { cx: 50, cy: 50, r: 46, fill: "currentColor" }),
      svgEl("path", { d: "M28 52 L44 67 L73 35", fill: "none", stroke: W, "stroke-width": 11, "stroke-linecap": "round", "stroke-linejoin": "round" }),
    ],
    wrong: () => [
      svgEl("path", { d: "M50 5 L97 92 L3 92 Z", fill: "currentColor", "stroke-linejoin": "round" }),
      svgEl("rect", { x: 44, y: 33, width: 12, height: 32, rx: 3, fill: W }),
      svgEl("circle", { cx: 50, cy: 77, r: 6.5, fill: W }),
    ],
    unrecognised: () => [
      svgEl("path", { d: "M31 4 H69 L96 31 V69 L69 96 H31 L4 69 V31 Z", fill: "currentColor" }),
      svgEl("path", { d: "M36 38 C36 22 64 22 64 38 C64 50 50 50 50 62", fill: "none", stroke: W, "stroke-width": 10, "stroke-linecap": "round" }),
      svgEl("circle", { cx: 50, cy: 78, r: 6.5, fill: W }),
    ],
    stopped: () => [
      svgEl("rect", { x: 6, y: 6, width: 88, height: 88, rx: 8, fill: "currentColor" }),
      svgEl("rect", { x: 30, y: 28, width: 14, height: 44, rx: 2, fill: W }),
      svgEl("rect", { x: 56, y: 28, width: 14, height: 44, rx: 2, fill: W }),
    ],
    warming_up: () => [
      svgEl("circle", { cx: 50, cy: 50, r: 41, fill: "none", stroke: "currentColor", "stroke-width": 9, "stroke-dasharray": "16 9" }),
      svgEl("path", { d: "M50 28 V52 L65 61", fill: "none", stroke: "currentColor", "stroke-width": 9, "stroke-linecap": "round" }),
    ],
    problem: () => [
      svgEl("path", { d: "M50 4 L96 50 L50 96 L4 50 Z", fill: "currentColor", "stroke-linejoin": "round" }),
      svgEl("rect", { x: 44, y: 24, width: 12, height: 34, rx: 3, fill: W }),
      svgEl("circle", { cx: 50, cy: 72, r: 6.5, fill: W }),
    ],
    none: () => [svgEl("circle", { cx: 50, cy: 50, r: 42, fill: "none", stroke: "currentColor", "stroke-width": 7, "stroke-dasharray": "12 10" })],
  };
  // The model's diagnosis kinds use the same shapes: a fault type is the
  // warning triangle, normal the tick.
  GLYPHS.fault = GLYPHS.wrong;
  GLYPHS.normal = GLYPHS.ok;

  function setGlyph(svg, kind) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    for (const n of (GLYPHS[kind] || GLYPHS.none)()) svg.appendChild(n);
  }

  function kindColor(kind) {
    return (
      {
        ok: css("--ok"),
        normal: css("--ok"),
        wrong: css("--wrong"),
        fault: css("--wrong"),
        unrecognised: css("--unknown"),
        stopped: css("--ink-2"),
        warming_up: css("--muted"),
        problem: css("--problem"),
      }[kind] || css("--muted")
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
    const machine = shownMachine();
    const run = L.latestRunForMachine(state, machine);
    const watching = !!d && (!d.mode || d.mode === L.WATCHING);
    const live = state.platformUp && status === "live" && watching;

    document.body.classList.toggle("no-device", !d);
    document.body.classList.toggle("platform-lost", state.platformLost);
    document.body.classList.toggle("stale-all", !live);

    renderTop(id, d, status, now, run, machine);
    renderHero(id, d, rec, status, now);
    renderReadings(id, d, rec, live, watching);
    drawSpectrum(rec, model, live, watching);
    renderHeat(d, rec, machine, live, now);
    renderAgent(machine, run, L.featureTexts(state, id));
    renderHistory(machine);
  }

  function renderTop(id, d, status, now, run, machine) {
    $("shown-device").textContent = id || "none";
    $("shown-machine").textContent = machine || "none";

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
      checking_in: ["ok", "Device live · " + (d ? L.modeName(d.mode).toLowerCase() : "")],
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

  // Shrink a line until it fits. Never cut it off.
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
    const head = L.headline(state, id, now);
    const name = $("diag-name");
    name.textContent = head.text;
    hero.dataset.state = head.state;
    setGlyph($("diag-glyph"), { wrong: "wrong", ok: "ok" }[head.state] || "none");
    fitText(name);

    // Mode: always said, so a recording device is never mistaken for a watching one.
    const modeTag = $("mode-tag");
    modeTag.hidden = !d || !d.mode;
    if (d && d.mode) {
      modeTag.textContent = L.modeName(d.mode);
      modeTag.className = "mode-tag" + (d.mode === L.WATCHING ? "" : " other");
    }

    // Not live: say why, and how old the last word is.
    const tag = $("stale-tag");
    const stale = head.state === "not_live";
    tag.hidden = !stale;
    if (stale) {
      const ago = agoText(d.lastHeardLocalMs != null ? now - d.lastHeardLocalMs : null);
      if (!state.platformUp) {
        tag.textContent = state.platformLost ? "Platform lost" : "Connecting";
        tag.className = "stale-tag lost";
      } else if (status === "disconnected") {
        tag.textContent = "Device disconnected · last heard " + ago;
        tag.className = "stale-tag lost";
      } else {
        tag.textContent = "No reading for " + agoText(d.lastRecordLocalMs != null ? now - d.lastRecordLocalMs : null).replace(" ago", "");
        tag.className = "stale-tag";
      }
    }

    // The model's panel. First, and never hidden by the problems.
    const ml = L.modelLine(state, id);
    const label = $("model-label");
    const reason = $("reason");
    const meta = $("meta");
    $("model-block").classList.toggle("stale", stale);
    if (!d) {
      label.textContent = state.snapshotLoaded ? "No device has connected to the platform yet." : "Connecting to the platform…";
      reason.textContent = "";
      $("note").textContent = "";
      meta.textContent = "";
      setGlyph($("model-glyph"), "none");
      $("model-possible").hidden = true;
    } else if (!ml) {
      label.textContent = "Model: no reading yet from " + id;
      reason.textContent = "";
      $("note").textContent = "";
      meta.textContent = "";
      setGlyph($("model-glyph"), "none");
      $("model-possible").hidden = true;
    } else {
      label.textContent = ml.label;
      reason.textContent = ml.detail;
      $("note").textContent = ml.note ? "Note: " + ml.note : "";
      $("model-possible").hidden = !ml.possible;
      setGlyph($("model-glyph"), ml.kind);
      $("model-glyph").style.color = stale ? css("--stale") : kindColor(ml.kind);
      const bits = [];
      if (rec && d.mode === L.WATCHING) {
        if (typeof rec.confidence === "number") bits.push("confidence " + Math.round(rec.confidence * 100) + "%");
        if (typeof rec.unknown_score === "number") {
          const line = typeof rec.alarm_line === "number" ? " (line " + rec.alarm_line.toFixed(2) + ")" : "";
          bits.push("unknown score " + rec.unknown_score.toFixed(2) + line);
        }
        if (typeof rec.abnormal_minutes === "number" && rec.abnormal_minutes > 0) {
          bits.push(rec.abnormal_minutes + (rec.abnormal_minutes === 1 ? " period" : " periods") + " abnormal");
        }
        if (rec.model_version) bits.push("model " + rec.model_version);
        if (rec.at) bits.push("at " + fmtTime(rec.at));
      } else if (rec && rec.at) {
        bits.push("last reading " + fmtTime(rec.at) + ": " + L.displayName(rec.diagnosis));
      }
      meta.textContent = bits.join("  ·  ");
    }

    renderProblems(d, stale);

    const key = head.state + "|" + (d ? d.problems.map((p) => p.name).join(",") : "") + "|" + (rec ? rec.diagnosis : "");
    if (ui.lastHeadKey !== null && key !== ui.lastHeadKey && (head.state === "wrong" || head.state === "ok")) {
      hero.classList.remove("flash");
      void hero.offsetWidth;
      hero.classList.add("flash");
    }
    ui.lastHeadKey = key;
  }

  function renderProblems(d, stale) {
    const block = $("problem-block");
    const list = $("prob-list");
    list.textContent = "";
    if (!d || stale) {
      // Not connected: the platform reports no problems, which does not mean there are none.
      block.hidden = !d;
      if (d) {
        $("problem-head").textContent = "Problems";
        list.appendChild(el("li", "prob-none", "Not known while the device is not live."));
      }
      return;
    }
    block.hidden = false;
    const probs = d.problems;
    $("problem-head").textContent = probs.length ? "Problems · " + probs.length : "Problems";
    if (!probs.length) {
      list.appendChild(el("li", "prob-none", "None found by the safety checks."));
      return;
    }
    for (const p of probs) {
      const li = el("li", "prob");
      li.dataset.kind = p.kind || "";
      const g = svgEl("svg", { viewBox: "0 0 100 100", class: "prob-glyph", "aria-hidden": "true" });
      for (const n of GLYPHS.problem()) g.appendChild(n);
      const main = el("div", "prob-main");
      const line = el("div", "prob-line");
      line.append(el("span", "prob-text", capitalise(p.text || p.name)));
      const value = L.problemValueText(p);
      if (value) line.append(el("span", "prob-value", value));
      const sub = el("div", "prob-sub");
      sub.append(el("span", "prob-kind", L.kindLabel(p.kind)));
      const bits = [p.name];
      if (p.since) bits.push("since " + fmtTime(p.since));
      sub.append(el("span", "prob-meta", bits.join("  ·  ")));
      main.append(line, sub);
      li.append(g, main);
      list.appendChild(li);
    }
  }

  function capitalise(text) {
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }

  const SPREAD_SCALE = 8; // bars fill at 8 spreads either way

  function renderReadings(id, d, rec, live, watching) {
    const list = $("reading-list");
    const foot = $("readings-foot");
    list.textContent = "";
    $("readings-note").textContent = "";
    if (!d) {
      list.appendChild(el("div", "empty", "Readings appear when a device sends its first record."));
      foot.textContent = "";
      return;
    }
    if (!watching) {
      list.appendChild(el("div", "empty", "No readings while " + L.modeName(d.mode).toLowerCase() + ". The device sends readings only while watching."));
      foot.textContent = "";
      return;
    }
    const rows = L.movedReadings(state, id);
    if (!rec) {
      list.appendChild(el("div", "empty", "No reading yet."));
    } else if (!rows.length) {
      const kind = L.diagnosisKind(rec.diagnosis);
      list.appendChild(el("div", "empty", kind === "stopped" || kind === "warming_up"
        ? "Nothing judged while " + (kind === "stopped" ? "the machine is stopped." : "the model warms up.")
        : "This reading lists no readings that moved."));
    }
    const normal = rec && L.diagnosisKind(rec.diagnosis) === "normal";
    $("readings-note").textContent = rec && rows.length ? (normal ? "top 3 while normal" : "the ones that moved most") : "";
    for (const r of rows) {
      const row = el("div", "reading");
      const text = el("div", "reading-text");
      text.append(el("div", "reading-sentence", capitalise(r.text)), el("div", "reading-name", r.name));
      const meter = el("div", "meter");
      const bar = el("div", "bar");
      const fill = el("div", "bar-fill" + (r.value < 0 ? " neg" : ""));
      const frac = Math.min(1, Math.abs(r.value) / SPREAD_SCALE) * 50;
      fill.style.width = frac + "%";
      fill.style.left = r.value < 0 ? 50 - frac + "%" : "50%";
      bar.append(el("div", "bar-zero"), fill);
      meter.append(bar, el("div", "bar-val", (r.value > 0 ? "+" : r.value < 0 ? "−" : "") + Math.abs(r.value).toFixed(1)));
      row.append(text, meter);
      list.appendChild(row);
    }
    foot.textContent = rows.length
      ? (live ? "" : "Last reading, not live. ") + "Spreads of each reading's normal variation; bars fill at ±" + SPREAD_SCALE + "."
      : "";
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

  // Fixed level scale, so a change shows as a change. Widened only if a
  // reading falls outside it.
  const SPEC_MIN = -7, SPEC_MAX = -2;

  function drawSpectrum(rec, model, live, watching) {
    const { ctx, w, h } = prepCanvas($("spectrum-canvas"));
    const rem = remPx();
    const ink = css("--ink");
    const muted = css("--muted");
    const line = css("--line");
    const padL = 3.2 * rem, padR = 1 * rem, padT = 0.6 * rem, padB = 2.4 * rem;
    const pw = w - padL - padR, ph = h - padT - padB;
    const edges = model && Array.isArray(model.spectrum_edges_hz) && model.spectrum_edges_hz.length >= 2 &&
      model.spectrum_edges_hz.every((x) => typeof x === "number" && x > 0) ? model.spectrum_edges_hz : null;
    const spec = watching && rec && Array.isArray(rec.spectrum) ? rec.spectrum.map(Number) : null;
    const usable = spec && edges && edges.length === spec.length + 1;

    let lo = SPEC_MIN, hi = SPEC_MAX;
    if (spec) {
      for (const v of spec) {
        if (!isFinite(v)) continue;
        lo = Math.min(lo, Math.floor(v));
        hi = Math.max(hi, Math.ceil(v));
      }
    }
    const yOf = (v) => padT + ph * (1 - (v - lo) / (hi - lo));

    ctx.font = "600 " + 0.95 * rem + "px " + css("--mono");
    ctx.textBaseline = "middle";
    ctx.textAlign = "right";
    for (let v = lo; usable && v <= hi; v++) {
      const y = yOf(v);
      ctx.strokeStyle = line;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + pw, y);
      ctx.stroke();
      ctx.fillStyle = muted;
      ctx.fillText(String(v).replace("-", "−"), padL - 0.4 * rem, y);
    }

    if (usable) {
      const f0 = Math.log(edges[0]), f1 = Math.log(edges[edges.length - 1]);
      const xOf = (hz) => padL + ((Math.log(hz) - f0) / (f1 - f0)) * pw;
      ctx.fillStyle = live ? css("--spectrum") : css("--stale");
      for (let i = 0; i < spec.length; i++) {
        if (!isFinite(spec[i])) continue;
        const x0 = xOf(edges[i]), x1 = xOf(edges[i + 1]);
        const gap = (x1 - x0) * 0.12;
        const y = yOf(spec[i]);
        ctx.fillRect(x0 + gap, y, x1 - x0 - 2 * gap, padT + ph - y);
      }
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillStyle = ink;
      for (const hz of [5, 10, 20, 50, 100, 200, 500, 1000, 2000]) {
        if (hz < edges[0] - 1e-9 || hz > edges[edges.length - 1] + 1e-9) continue;
        const x = xOf(hz);
        ctx.fillRect(x - 1, padT + ph, 2, 0.4 * rem);
        ctx.fillText(String(hz), x, padT + ph + 0.55 * rem);
      }
      ctx.textAlign = "right";
      ctx.fillText("Hz", padL + pw, padT + ph + 1.4 * rem);
    }

    if (usable) {
      ctx.strokeStyle = ink;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(padL, padT);
      ctx.lineTo(padL, padT + ph);
      ctx.lineTo(padL + pw, padT + ph);
      ctx.stroke();
    } else {
      let msg;
      if (!rec && watching) msg = "No readings yet";
      else if (!watching) msg = "No spectrum outside watching";
      else if (!spec) msg = "This reading has no spectrum";
      else msg = "Band edges unknown until the model is announced";
      ctx.fillStyle = muted;
      ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      ctx.font = "600 " + 1.4 * rem + "px " + css("--sans");
      ctx.fillText(msg, padL + pw / 2, padT + ph / 2);
    }

    $("spectrum-note").textContent = usable
      ? (live ? "latest reading" : "last reading, not live") + " · " + spec.length + " bands · log10 band power"
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

  // History: what the model said, whether a problem was on, and the unknown
  // score against its alarm line, for the last records of the machine.
  const HISTORY_KINDS = [
    ["normal", "Normal"],
    ["fault", "Fault type"],
    ["unrecognised", "Unrecognised"],
    ["stopped", "Stopped"],
    ["warming_up", "Warming up"],
  ];

  function renderHistory(machine) {
    const recs = (machine && state.history[machine]) || [];
    $("history-note").textContent = machine
      ? machine + " · last " + recs.length + " readings" +
        (recs.length ? " · " + fmtTime(recs[0].at) + " to " + fmtTime(recs[recs.length - 1].at) : "")
      : "";

    const legend = $("history-legend");
    if (!legend.dataset.done) {
      legend.dataset.done = "1";
      for (const [kind, label] of HISTORY_KINDS) {
        const it = el("span", "legend-item");
        const s = svgEl("svg", { viewBox: "0 0 100 100" });
        s.style.color = kindColor(kind);
        for (const n of (GLYPHS[kind] || GLYPHS.none)()) s.appendChild(n);
        it.append(s, label);
        legend.appendChild(it);
      }
      const pit = el("span", "legend-item");
      const ps = svgEl("svg", { viewBox: "0 0 100 100" });
      ps.style.color = kindColor("problem");
      for (const n of GLYPHS.problem()) ps.appendChild(n);
      pit.append(ps, "Problem on");
      legend.appendChild(pit);
      const sit = el("span", "legend-item");
      sit.append(el("span", "legend-line"), "unknown score");
      legend.appendChild(sit);
      const ait = el("span", "legend-item");
      ait.append(el("span", "legend-line dashed"), "alarm line");
      legend.appendChild(ait);
    }

    const { ctx, w, h } = prepCanvas($("history-canvas"));
    const rem = remPx();
    const muted = css("--muted");
    const ink = css("--ink");
    const padL = 4.2 * rem, padR = 1 * rem;
    const stripT = 0, stripH = Math.max(1.5 * rem, h * 0.22);
    const probT = stripH + 0.25 * rem, probH = Math.max(0.8 * rem, h * 0.1);
    const lineT = probT + probH + 0.6 * rem, lineB = h - 0.2 * rem;
    const pw = w - padL - padR, lh = lineB - lineT;

    ctx.font = "600 " + 0.85 * rem + "px " + css("--mono");
    ctx.fillStyle = muted;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText("model", padL - 0.4 * rem, stripT + stripH / 2);
    ctx.fillText("problem", padL - 0.4 * rem, probT + probH / 2);

    if (!recs.length) {
      ctx.textAlign = "center";
      ctx.font = "600 " + 1.2 * rem + "px " + css("--sans");
      ctx.fillText(machine ? "No readings yet" : "No machine yet", padL + pw / 2, (lineT + lineB) / 2);
      return;
    }

    const n = recs.length;
    const cw = pw / Math.max(n, 1);
    const xOf = (i) => padL + (i + 0.5) * cw;
    const kindOf = (r) => L.diagnosisKind(r.diagnosis);

    // model strip, with names written in each long run
    let start = 0;
    for (let i = 1; i <= n; i++) {
      if (i === n || recs[i].diagnosis !== recs[start].diagnosis) {
        const kind = kindOf(recs[start]);
        const x0 = padL + start * cw, x1 = padL + i * cw;
        ctx.fillStyle = kindColor(kind);
        ctx.fillRect(x0, stripT, x1 - x0 + 0.5, stripH);
        if (kind === "unrecognised") {
          ctx.save();
          ctx.beginPath();
          ctx.rect(x0, stripT, x1 - x0, stripH * 0.22);
          ctx.clip();
          ctx.fillStyle = css("--hazard");
          ctx.fillRect(x0, stripT, x1 - x0, stripH);
          ctx.restore();
        }
        const label = L.displayName(recs[start].diagnosis);
        ctx.font = "800 " + 0.95 * rem + "px " + css("--sans");
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = "#fff";
        // Only whole names: a first letter alone would not tell Unbalance-like from Unrecognised.
        if (ctx.measureText(label).width + 0.8 * rem < x1 - x0) {
          ctx.fillText(label, (x0 + x1) / 2, stripT + stripH / 2);
        }
        start = i;
      }
    }
    // separators between runs, so neighbouring runs read as distinct without colour
    ctx.fillStyle = css("--panel");
    for (let i = 1; i < n; i++) {
      if (recs[i].diagnosis !== recs[i - 1].diagnosis) ctx.fillRect(padL + i * cw - 1, stripT, 2, stripH);
    }

    // problem strip: on while the record listed any problem
    ctx.strokeStyle = css("--line");
    ctx.lineWidth = 1;
    ctx.strokeRect(padL + 0.5, probT + 0.5, pw - 1, probH - 1);
    ctx.fillStyle = kindColor("problem");
    const hasProblem = (r) => Array.isArray(r.problems) && r.problems.length > 0;
    for (let i = 0; i < n; i++) {
      if (!hasProblem(recs[i]) || (i > 0 && hasProblem(recs[i - 1]))) continue;
      let j = i;
      while (j < n && hasProblem(recs[j])) j++;
      ctx.fillRect(padL + i * cw, probT, (j - i) * cw, probH); // one rectangle per run: no seams
    }

    // unknown score and its alarm line
    let top = 3;
    for (const r of recs) {
      if (typeof r.unknown_score === "number") top = Math.max(top, r.unknown_score * 1.1);
      if (typeof r.alarm_line === "number") top = Math.max(top, r.alarm_line * 1.2);
    }
    const yOf = (v) => lineB - Math.max(0, Math.min(1, v / top)) * lh;
    ctx.fillStyle = muted;
    ctx.font = "600 " + 0.85 * rem + "px " + css("--mono");
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.fillText(top.toFixed(1), padL - 0.4 * rem, lineT + 0.4 * rem);
    ctx.fillText("0", padL - 0.4 * rem, lineB - 0.4 * rem);
    ctx.strokeStyle = css("--line");
    ctx.beginPath();
    ctx.moveTo(padL, lineB);
    ctx.lineTo(padL + pw, lineB);
    ctx.moveTo(padL, lineT);
    ctx.lineTo(padL + pw, lineT);
    ctx.stroke();

    ctx.strokeStyle = ink;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 5]);
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
      const a = typeof recs[i].alarm_line === "number" ? recs[i].alarm_line : 1;
      const y = yOf(a);
      if (i === 0) ctx.moveTo(padL, y);
      ctx.lineTo(padL + (i + 1) * cw, y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.lineWidth = 2.5;
    ctx.beginPath();
    let pen = false;
    for (let i = 0; i < n; i++) {
      const v = recs[i].unknown_score;
      if (typeof v !== "number") {
        pen = false;
        continue;
      }
      if (pen) ctx.lineTo(xOf(i), yOf(v));
      else ctx.moveTo(xOf(i), yOf(v));
      pen = true;
    }
    ctx.stroke();
  }

  // ---------------- agent ----------------
  function compact(v) {
    try {
      return typeof v === "string" ? v : JSON.stringify(v);
    } catch (e) {
      return "";
    }
  }

  function renderAgent(machine, run, texts) {
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
      const cited = Array.isArray(r.cited_features) ? r.cited_features : [];
      box.appendChild(reportRow("Cited readings", cited.length
        ? cited.map((n) => (texts[n] && texts[n] !== n ? texts[n] + " (" + n + ")" : n)).join(", ")
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
