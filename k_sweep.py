"""
K sweep: how the band width K trades false alarms against missed incidents.

Runs the same simulation as simulate.py for K = 1.0 ... 8.0, under two scenarios:
  - obvious incidents: 55% traffic drop, slow decline to 60%
  - subtle incidents:  20% traffic drop, slow decline to 85%
and charts false alarms vs incidents caught.

Run:  python3 k_sweep.py   -> prints a table, writes k_sweep.png
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

SEED, DAYS, STEP_MIN, LOOKBACK_DAYS, MIN_CONSECUTIVE = 7, 21, 5, 14, 2
K_VALUES = np.round(np.arange(1.0, 8.01, 0.5), 1)
SCENARIOS = {"Obvious incidents": (0.45, 0.60), "Subtle incidents": (0.80, 0.85)}

idx = pd.date_range("2026-10-23", periods=DAYS * 24 * 60 // STEP_MIN, freq=f"{STEP_MIN}min")
hour = np.asarray(idx.hour + idx.minute / 60)
dow = np.asarray(idx.dayofweek)
per_day = 24 * 60 // STEP_MIN
last_day = idx[-1].normalize()
drop = np.asarray((idx >= last_day + pd.Timedelta(hours=14)) & (idx < last_day + pd.Timedelta(hours=15)))
degrade = np.asarray((idx >= last_day + pd.Timedelta(hours=10)) & (idx < last_day + pd.Timedelta(hours=12)))


def make_traffic(drop_factor, degrade_floor):
    rng = np.random.default_rng(SEED)
    t = (450 + 350 * np.sin((hour - 9) / 24 * 2 * np.pi)) * np.where(dow >= 5, 0.6, 1.0)
    t = t + ((hour >= 2) & (hour < 2.75)) * 900
    t = t * rng.normal(1.0, 0.05, len(t))
    t[drop] *= drop_factor
    t[degrade] *= np.linspace(1.0, degrade_floor, degrade.sum())
    return t


def baseline_and_spread(vals):
    weekend = dow >= 5
    base = np.full(len(vals), np.nan)
    spread = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        hist = [vals[i - d * per_day] for d in range(1, LOOKBACK_DAYS + 1)
                if i - d * per_day >= 0 and weekend[i - d * per_day] == weekend[i]]
        if len(hist) >= 3:
            h = np.array(hist)
            med = np.median(h)
            base[i] = med
            spread[i] = max(np.median(np.abs(h - med)) * 1.4826, 0.03 * med)
    return base, spread


def score(vals, base, spread, K):
    ok = ~np.isnan(base)
    out = (vals > base + K * spread) | (vals < np.clip(base - K * spread, 0, None))
    alert = pd.Series(out).rolling(MIN_CONSECUTIVE).sum().fillna(0).to_numpy() >= MIN_CONSECUTIVE
    alert &= ok
    starts = alert & ~np.r_[False, alert[:-1]]
    incident = drop | degrade
    false_alarms = int((starts & ~incident).sum())
    caught = int(alert[drop].any()) + int(alert[degrade].any())
    return false_alarms, caught


rows = []
for name, (df_, dg_) in SCENARIOS.items():
    vals = make_traffic(df_, dg_)
    base, spread = baseline_and_spread(vals)
    for K in K_VALUES:
        fa, caught = score(vals, base, spread, K)
        rows.append({"scenario": name, "K": K, "false_alarms": fa, "caught": caught})
res = pd.DataFrame(rows)
print(res.pivot(index="K", columns="scenario", values=["false_alarms", "caught"]).to_string())

# ---- chart ----
INK, MUTED, RED, BLUE, GREEN = "#1f2328", "#6b7280", "#d1242f", "#1f6feb", "#1a7f37"
plt.rcParams.update({"font.size": 14, "axes.edgecolor": MUTED, "text.color": INK,
                     "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
fig, (a0, a1) = plt.subplots(2, 1, figsize=(10, 11), sharex=True,
                             gridspec_kw={"height_ratios": [1.4, 1]})
fig.subplots_adjust(top=0.86, bottom=0.08, left=0.11, right=0.96, hspace=0.3)

fa = res[res.scenario == "Obvious incidents"]  # false alarms identical in both scenarios
a0.plot(fa.K, fa.false_alarms, color=RED, lw=2.5, marker="o")
a0.set_ylabel("false alarms (16 days)")
a0.set_title("False alarms: noise that pages someone", loc="left", weight="bold", fontsize=17)

for name, color in [("Obvious incidents", BLUE), ("Subtle incidents", GREEN)]:
    s = res[res.scenario == name]
    a1.plot(s.K, s.caught, color=color, lw=2.5, marker="o", label=name)
a1.set_ylim(-0.2, 2.3)
a1.set_yticks([0, 1, 2])
a1.set_yticklabels(["0 of 2", "1 of 2", "2 of 2"])
a1.set_xlabel("K  (band width)")
a1.set_title("Incidents caught", loc="left", weight="bold", fontsize=17)
a1.legend(frameon=False, loc="lower left")

for ax in (a0, a1):
    ax.spines[["top", "right"]].set_visible(False)
    ax.axvspan(1, 2.5, color=RED, alpha=0.05, lw=0)
    ax.axvspan(5.5, 8, color=MUTED, alpha=0.07, lw=0)
a0.text(1.55, a0.get_ylim()[1] * 0.97, "tight band:\nhigh recall,\nlow precision", color=RED, fontsize=12, va="top")
a0.text(5.6, a0.get_ylim()[1] * 0.97, "wide band:\nhigh precision,\nlow recall", color=MUTED, fontsize=12, va="top")

fig.text(0.04, 0.955, "Choosing K: precision vs. recall", fontsize=24, weight="bold")
fig.text(0.04, 0.915, "Same traffic, band width swept from 1 to 8", fontsize=14, color=MUTED)
fig.text(0.96, 0.02, "Simulated data", fontsize=11, color=MUTED, ha="right")
fig.savefig("k_sweep.png", dpi=120)
print("\nSaved k_sweep.png")
