"""Where a part's model score is heading (platform/CONTRACT.md section 9.5).

One plain function, `project`: a part's readings and its newest repair in, the
projection out. A straight least-squares line through the readings'
`unknown_score` over the last 14 days, and when that line would meet the alarm
line. No model call, no clock, no file: the same input always gives the same
output. `trend_sentence` turns a projection into the words the app and the part
review show (contract 9.5 and 9.7).

Standard library only, so the receiver can run it.

Every number below that the contract calls a starting value is one: picked by
reasoning, not checked on a real machine. Unverified.
"""

from __future__ import annotations

import datetime as dt
import math

WINDOW_DAYS = 14  # starting value
MIN_DAYS = 3  # starting value: the fewest points for which a range can be worked out at all
RANGE = 0.80  # starting value: the slope range is an 80% range
MAX_DAYS = 365  # an end of `reaches` past this is null

# Whether the app may show days and dates (contract 9.5, "The check on
# run-to-failure data"). Fixed in code, never worked out from a part's readings.
#
# False until the check on run-to-failure data (platform/partlog/tests/
# check_projection_ims.py) has run and come out well: the app then says
# "Trend" and shows no days figure and no date.
SHOW_DAYS = False

OVER_LINE, RISING, NO_CLEAR_RISE, NOT_RISING = "over_line", "rising", "no_clear_rise", "not_rising"
NOT_ENOUGH_HISTORY = "not_enough_history"
STATUSES = (OVER_LINE, RISING, NO_CLEAR_RISE, NOT_RISING, NOT_ENOUGH_HISTORY)

DAY_S = 86400.0


def project(readings, last_repair_at=None, *, now, show_days=SHOW_DAYS) -> dict:
    """The projection of one part (contract 9.5).

    `readings`: the part's readings (contract 9.3), any order. Only `start`,
    `unknown_score`, `alarm_line`, `model_version` and `simulated` are read.
    `last_repair_at`: the `at` of the part's newest `repair` entry, or None.
    `now`: the time the projection is for, as ISO text or a datetime. The
    window is the 14 days up to it. Passed in, so the function has no clock.
    `show_days`: the switch above. Tests pass it; the receiver does not.
    """
    now_s = _seconds(now)
    if now_s is None:
        raise ValueError(f"now is not a time: {now!r}")
    window = _window(readings, _seconds(last_repair_at), now_s)
    out = {
        "status": NOT_ENOUGH_HISTORY,
        "fits": "unknown_score",
        "line": None,
        "window": _window_summary(window),
        "now_score": None,
        "slope_per_day": None,
        "slope_range": None,
        "show_days": bool(show_days),
        "reaches": None,
        "simulated": any(r.get("simulated") is True for _, r in window),
    }
    if not window:
        return out
    newest_s, newest = window[-1]
    line = newest.get("alarm_line")
    if not _is_number(line):
        return out  # a device older than version 3.1 reports no line: nothing to project to
    out["line"] = {"name": "alarm_line", "unknown_score": float(line)}
    if out["window"]["days"] < MIN_DAYS:
        return out

    days = [(s - newest_s) / DAY_S for s, _ in window]  # 0 at the newest reading
    scores = [float(r["unknown_score"]) for _, r in window]
    slope, now_score, low, high = _fit(days, scores)
    out.update(now_score=round(now_score, 2), slope_per_day=_sig(slope), slope_range=[_sig(low), _sig(high)])

    if scores[-1] >= line:
        status = OVER_LINE
    elif low > 0:
        status = RISING
    elif high > 0:  # the range holds 0
        status = NO_CLEAR_RISE
    else:
        status = NOT_RISING
    out["status"] = status
    if status == RISING and show_days:
        out["reaches"] = _reaches(now_score, float(line), low, high, newest_s)
    return out


def trend_sentence(projection) -> str:
    """The sentence for a projection's status, from the table in contract 9.5.

    "Simulated: " goes in front when the projection is simulated (contract 9.6).
    Empty for no projection. Used by the part review's `trend` (contract 9.7),
    which a model never writes.
    """
    if not isinstance(projection, dict) or projection.get("status") not in STATUSES:
        return ""
    status = projection["status"]
    if status == NOT_ENOUGH_HISTORY:
        text = ("Not enough history to say where this is heading: "
                f"a trend needs readings on at least {MIN_DAYS} different days.")
    elif status == OVER_LINE:
        text = "At or above the model's alarm line now"
    elif status == NO_CLEAR_RISE:
        text = "No clear rise yet: the change is inside the day-to-day spread"
    elif status == NOT_RISING:
        days = (projection.get("window") or {}).get("days")
        text = f"Level or falling over the last {days} days"
    elif not projection.get("show_days") or not projection.get("reaches"):
        text = "Rising toward the model's alarm line"
    else:
        text = "On this trend the model's score reaches its alarm line " + _when(projection["reaches"])
    return f"Simulated: {text}" if projection.get("simulated") is True else text


# ---- the window ------------------------------------------------------------


def _window(readings, repair_s, now_s) -> list[tuple[float, dict]]:
    """Rule 1 of contract 9.5: the readings that count, oldest first, with their times.

    The last 14 days up to `now`, with a score, after the newest repair, and of
    the newest such reading's `model_version`.
    """
    kept = []
    for reading in readings or []:
        if not isinstance(reading, dict) or not _is_number(reading.get("unknown_score")):
            continue
        start = _seconds(reading.get("start"))
        if start is None or start > now_s or start < now_s - WINDOW_DAYS * DAY_S:
            continue
        if repair_s is not None and start <= repair_s:
            continue  # a repair starts the trend again
        kept.append((start, reading))
    kept.sort(key=lambda item: item[0])
    if not kept:
        return []
    version = kept[-1][1].get("model_version")
    return [(s, r) for s, r in kept if r.get("model_version") == version]


def _window_summary(window) -> dict:
    if not window:
        return {"from": None, "to": None, "readings": 0, "days": 0}
    return {
        "from": window[0][1].get("start"),
        "to": window[-1][1].get("start"),
        "readings": len(window),
        "days": len({_utc_day(s) for s, _ in window}),
    }


# ---- the fit ---------------------------------------------------------------


def _fit(x: list[float], y: list[float]) -> tuple[float, float, float, float]:
    """Least squares y = a + b x. Returns (b, a, b - t*se, b + t*se).

    x is days with 0 at the newest reading, so `a` is the line's value there:
    `now_score`. The range is Student's t for an 80% two-sided range with
    n - 2 degrees of freedom, times the slope's standard error. The caller
    makes sure x spans at least 3 different days, so n >= 3 and Sxx > 0.
    """
    n = len(x)
    mean_x, mean_y = sum(x) / n, sum(y) / n
    sxx = sum((xi - mean_x) ** 2 for xi in x)
    sxy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    residual = sum((yi - intercept - slope * xi) ** 2 for xi, yi in zip(x, y))
    se = math.sqrt(residual / (n - 2) / sxx)
    spread = t_quantile(0.5 + RANGE / 2, n - 2) * se
    return slope, intercept, slope - spread, slope + spread


def _reaches(now_score: float, line: float, low: float, high: float, newest_s: float) -> dict:
    """Days from the newest reading until the line meets the alarm line, at the
    steepest (`high`) and gentlest (`low`) slope of the range. Whole days,
    rounded up. An end past 365 days is null."""

    def end(slope: float) -> tuple[int | None, str | None]:
        days = math.ceil((line - now_score) / slope)
        days = max(days, 0)
        if days > MAX_DAYS:
            return None, None
        return days, _utc_day(newest_s + days * DAY_S)

    earliest_days, earliest = end(high)
    latest_days, latest = end(low)
    return {"days": [earliest_days, latest_days], "earliest": earliest, "latest": latest}


def t_quantile(p: float, df: int) -> float:
    """Student's t quantile: the x with P(T <= x) = p, for p in (0.5, 1).

    Found by bisection on the distribution function, which is the regularized
    incomplete beta function (Numerical Recipes' continued fraction). Standard
    library only: the receiver does not depend on scipy.
    """
    if not 0.5 < p < 1 or df < 1:
        raise ValueError(f"t quantile needs 0.5 < p < 1 and df >= 1: p={p}, df={df}")
    low, high = 0.0, 1.0
    while _t_cdf(high, df) < p:
        high *= 2
    for _ in range(200):
        mid = (low + high) / 2
        if _t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _t_cdf(x: float, df: int) -> float:
    """P(T <= x) for x >= 0."""
    return 1 - 0.5 * _betainc(df / 2, 0.5, df / (df + x * x))


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1 - front * _betacf(b, a, 1 - x) / b


def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    c, d = 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1 + numerator * d
            d = 1 / (d if abs(d) > tiny else tiny)
            c = 1 + numerator / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return h


# ---- small helpers ---------------------------------------------------------


def _when(reaches: dict) -> str:
    """"in 51 to 106 days (4 Jan to 28 Feb)", "in 51 days (4 Jan)", "in 51 days
    to more than a year (from 4 Jan)", or "in more than a year"."""
    (first, last), earliest, latest = reaches["days"], reaches["earliest"], reaches["latest"]
    if first is None:
        return "in more than a year"
    if last is None:
        return f"in {first} days to more than a year (from {_short(earliest)})"
    if first == last:
        return f"in {first} days ({_short(earliest)})"
    return f"in {first} to {last} days ({_short(earliest)} to {_short(latest)})"


def _short(day: str) -> str:
    """"2027-01-04" -> "4 Jan"."""
    date = dt.date.fromisoformat(day)
    return f"{date.day} {date.strftime('%b')}"


def _sig(value: float, digits: int = 3) -> float:
    """Round to significant digits, so a small slope does not round to 0."""
    if value == 0 or not math.isfinite(value):
        return value
    return round(value, digits - 1 - math.floor(math.log10(abs(value))))


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _seconds(value) -> float | None:
    """ISO 8601 text or a datetime to UTC seconds. No zone means UTC. None for anything else."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        at = value
    else:
        try:
            at = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.timezone.utc)
    return at.timestamp()


def _utc_day(seconds: float) -> str:
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).date().isoformat()
