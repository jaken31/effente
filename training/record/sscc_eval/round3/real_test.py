"""hyb60 over all inputs on the board's own recordings: learn from a recording, judge later readings.
  python real_test.py <round3 dir> <repo dir> <recordings dir> <live dir>"""
import glob, json, sys, time
import numpy as np
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2]); sys.path.insert(0, ".")
from effente import features as F
import all_inputs as A
recs, live = sys.argv[3], sys.argv[4]
names = F.FEATURE_NAMES


def rec(r):
    fs = sorted(glob.glob(f"{recs}/{r}/features/*.npz"))
    X = np.concatenate([np.load(f)["X"] for f in fs]); t = np.concatenate([np.load(f)["t0"] for f in fs])
    o = np.argsort(t); return t[o], X[o].astype(float)


def cap(f):
    R = [json.loads(l) for l in open(f"{live}/{f}.jsonl", encoding="utf-8")]
    rows = [(r["t"], r["values"]) for r in R if r["kind"] == "row" and r.get("values")]
    return np.array([t for t, _ in rows]), np.array([[np.nan if v is None else v for v in x] for _, x in rows], float)


def run(m, t, X, label):
    m.reset()
    J = [m.judge(x) for x in X]
    al = np.array([j["alarm"] for j in J]); lv = np.array([j["level"] for j in J])
    which = {}
    for j in J:
        if j["alarm"]:
            which[j["which"]] = which.get(j["which"], 0) + 1
    g = np.array([[j["groups"][n] for n in m.group_names] for j in J])
    print(f"  {label}: {len(X)} s, alarm seconds {int(al.sum())} ({100 * al.mean():.1f} %), level median {np.median(lv):.2f} max {lv.max():.2f}, "
          f"alarms by group {which}, group level medians {dict(zip(m.group_names, [float(x) for x in np.round(np.median(g, 0), 2)]))}")
    return al, lv


sets = {"rec 22:59 (10 min)": rec("rec-20261113-225937"), "rec 00:10 (10 min)": rec("rec-20261114-001034")}
caps = {k: cap(k) for k in ("moved_15deg", "rest_0335", "after_restart")}
for learn_name in sets:
    t, X = sets[learn_name]
    t0 = time.perf_counter(); m = A.AllInputs(names).learn(X); dt = time.perf_counter() - t0
    print(f"learned from {learn_name}: groups {dict(zip(m.group_names, [len(c) for c in m.cols]))}, lines over each group's normal level {({g: round(v, 1) for g, v in m.lines.items()})}, {dt:.2f} s")
    for other in sets:
        if other != learn_name:
            run(m, *sets[other], "judging " + other)
    for k, (tc, Xc) in caps.items():
        if k == "rest_0335":
            cut = 1794645420.0  # 03:37:00 EST, when the ESP32 lost power and the accelerometer went out of step
            gz = np.sqrt(sum(Xc[:, names.index(f"vib_gravity_{a}")] ** 2 for a in "xyz"))
            bad = gz > 2.5
            print(f"  (rest_0335: seconds with impossible gravity {int(bad.sum())}, first at +{int(tc[bad][0] - tc[0]) if bad.any() else None} s)")
            run(m, tc[~bad], Xc[~bad], "rest 03:3x, sensor good")
            if bad.any():
                run(m, tc[bad], Xc[bad], "rest 03:3x, accelerometer out of step (a real fault of the sensor)")
        else:
            run(m, tc, Xc, k)
