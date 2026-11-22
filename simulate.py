"""
Static vs dynamic alert thresholds: a traffic simulation.

Generates 21 days of synthetic requests-per-minute (5-minute buckets) with:
  - a daily wave (quiet nights, busy afternoons)
  - lighter weekends
  - a nightly batch job at 02:00 (normal, expected behavior)
  - random noise
Then injects two real incidents on the last day:
  - 14:00  traffic drops ~55% for an hour (e.g., checkout broken upstream)
  - 10:00  slow degradation that a fixed line never crosses

It compares:
  - STATIC:  alert if rpm > HIGH or rpm < LOW (fixed numbers)
  - DYNAMIC: alert if rpm leaves a band built from the same time-of-day over
             the previous 14 days (same day type) (median +/- k * robust spread)

Run:  python3 simulate.py        -> writes threshold_comparison.png
Tweak the knobs below and re-run to see how alerts change.
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ---------------- knobs ----------------
SEED = 7
DAYS = 21
STEP_MIN = 5
STATIC_HIGH = 950      # rpm
STATIC_LOW = 100       # rpm
K = 4.0                # band width in robust std units
LOOKBACK_DAYS = 14   # weekends compare to weekends, weekdays to weekdays
MIN_CONSECUTIVE = 2    # buckets out of band before alerting (debounce)
# ---------------------------------------

rng = np.random.default_rng(SEED)
idx = pd.date_range("2026-10-23", periods=DAYS * 24 * 60 // STEP_MIN, freq=f"{STEP_MIN}min")
hour = np.asarray(idx.hour + idx.minute / 60)

# daily wave: trough ~04:00, peak ~15:00
wave = 450 + 350 * np.sin((hour - 9) / 24 * 2 * np.pi)
weekend = np.where(np.asarray(idx.dayofweek) >= 5, 0.6, 1.0)
traffic = wave * weekend

# nightly batch job 02:00-02:45 (normal!)
batch = ((hour >= 2) & (hour < 2.75)).astype(float) * 900
traffic = traffic + batch

traffic = traffic * rng.normal(1.0, 0.05, len(idx))

# ---- inject incidents on the last day ----
last_day = idx[-1].normalize()
drop = (idx >= last_day + pd.Timedelta(hours=14)) & (idx < last_day + pd.Timedelta(hours=15))
traffic[drop] *= 0.45
degrade = (idx >= last_day + pd.Timedelta(hours=10)) & (idx < last_day + pd.Timedelta(hours=12))
ramp = np.linspace(1.0, 0.6, degrade.sum())
traffic[degrade] *= ramp
incident = drop | degrade

df = pd.DataFrame({"rpm": traffic, "incident": incident, "drop": drop, "degrade": degrade}, index=idx)

# ---- static rule ----
df["static_alert"] = (df.rpm > STATIC_HIGH) | (df.rpm < STATIC_LOW)

# ---- dynamic rule: seasonal baseline from same slot on previous days ----
per_day = 24 * 60 // STEP_MIN
vals = df.rpm.to_numpy()
is_weekend = (idx.dayofweek >= 5)
base = np.full(len(vals), np.nan)
spread = np.full(len(vals), np.nan)
for i in range(len(vals)):
    hist = []
    for d in range(1, LOOKBACK_DAYS + 1):
        j = i - d * per_day
        if j >= 0 and is_weekend[j] == is_weekend[i]:
            hist.append(vals[j])
    if len(hist) >= 3:
        h = np.array(hist)
        med = np.median(h)
        mad = np.median(np.abs(h - med)) * 1.4826
        base[i] = med
        spread[i] = max(mad, 0.03 * med)   # floor so the band never collapses
df["baseline"] = base
df["upper"] = base + K * spread
df["lower"] = np.clip(base - K * spread, 0, None)
out = (df.rpm > df.upper) | (df.rpm < df.lower)
# debounce: require N consecutive out-of-band buckets
df["dynamic_alert"] = out.rolling(MIN_CONSECUTIVE).sum().fillna(0) >= MIN_CONSECUTIVE


# ---- scoring (on days with a full baseline) ----
def episodes(mask):
    """Group consecutive alert buckets into alert episodes."""
    m = mask.astype(int)
    starts = (m.diff().fillna(m.iloc[0]) == 1)
    return mask.index[starts & mask]

scored = df[df.baseline.notna()]
report = []
for name in ["static_alert", "dynamic_alert"]:
    eps = episodes(scored[name])
    true_eps = [t for t in eps if scored.loc[t, "incident"]]
    false_eps = [t for t in eps if not scored.loc[t, "incident"]]
    caught_drop = scored.loc[scored["drop"], name].any()
    caught_deg = scored.loc[scored["degrade"], name].any()
    report.append((name.replace("_alert", ""), len(false_eps), caught_drop, caught_deg))

print(f"Evaluated {scored.index[0]:%b %d} to {scored.index[-1]:%b %d}\n")
print(f"{'rule':<8} {'false alarms':>13} {'caught drop':>12} {'caught slow degrade':>20}")
for r in report:
    print(f"{r[0]:<8} {r[1]:>13} {str(r[2]):>12} {str(r[3]):>20}")

# ---- chart: last 3 days ----
view = df[df.index >= last_day - pd.Timedelta(days=2)]
fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
inc_color, static_c, dyn_c = "#d62728", "#7f7f7f", "#1f77b4"

for ax, title in zip(axes, ["Static threshold", "Dynamic threshold (seasonal baseline)"]):
    ax.plot(view.index, view.rpm, color="#333", lw=1, label="requests / min")
    for col in ["drop", "degrade"]:
        sub = view[view[col]]
        ax.axvspan(sub.index[0], sub.index[-1], color=inc_color, alpha=0.12)
    ax.set_title(title, loc="left", fontsize=13, weight="bold")
    ax.set_ylabel("rpm")
    ax.spines[["top", "right"]].set_visible(False)

a0 = axes[0]
a0.axhline(STATIC_HIGH, color=static_c, ls="--", lw=1.5, label=f"static high ({STATIC_HIGH})")
a0.axhline(STATIC_LOW, color=static_c, ls=":", lw=1.5, label=f"static low ({STATIC_LOW})")
sa = view[view.static_alert]
a0.scatter(sa.index, sa.rpm, color=inc_color, s=18, zorder=5, label="alert fired")
a0.legend(loc="upper left", fontsize=9, frameon=False, ncol=4)

a1 = axes[1]
a1.fill_between(view.index, view.lower, view.upper, color=dyn_c, alpha=0.18, label="expected band")
a1.plot(view.index, view.baseline, color=dyn_c, lw=1, ls="--", label="baseline")
da = view[view.dynamic_alert]
a1.scatter(da.index, da.rpm, color=inc_color, s=18, zorder=5, label="alert fired")
a1.legend(loc="upper left", fontsize=9, frameon=False, ncol=4)

a1.xaxis.set_major_formatter(mdates.DateFormatter("%a %H:%M"))
a1.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18]))
fig.suptitle("Same traffic, two alerting strategies  (shaded = real incidents)",
             fontsize=14, x=0.01, ha="left")
fig.tight_layout()
fig.savefig("threshold_comparison.png", dpi=150)
print("\nSaved threshold_comparison.png")
