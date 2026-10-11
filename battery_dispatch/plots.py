"""Figures. One colour per strategy, the same in every figure."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Colour-blind-checked categorical colours (blue, orange, green).
COLOURS = {
    "perfect foresight": "#2a78d6",
    "persistence": "#eb6834",
    "threshold rule": "#1baf7a",
}
INK = "#0b0b0b"
MUTED = "#6b6a66"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def cumulative_revenue(daily: pd.DataFrame, path, title: str):
    fig, ax = plt.subplots(figsize=(9, 4.8), facecolor=SURFACE)
    _style(ax)
    ends = []
    for name, colour in COLOURS.items():
        d = daily[daily["strategy"] == name].sort_values("date")
        if d.empty:
            continue
        cum = d["net_revenue"].cumsum() / 1000
        ax.plot(d["date"], cum, color=colour, linewidth=2, label=name)
        ends.append((float(cum.iloc[-1]), d["date"].iloc[-1], name))
    # direct labels at the line ends, nudged apart if they would overlap
    ends.sort()
    span = (ax.get_ylim()[1] - ax.get_ylim()[0]) or 1.0
    last_y = -np.inf
    for value, date, name in ends:
        y = max(value, last_y + 0.05 * span)
        ax.annotate(f"{name}  ${value:,.1f}k", (date, value), xytext=(8, (y - value) / span * 300),
                    textcoords="offset points", va="center", fontsize=9, color=INK)
        last_y = y
    ax.axhline(0, color=MUTED, linewidth=0.8)
    ax.set_ylabel("Cumulative net revenue (thousand $)", color=MUTED, fontsize=9)
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.margins(x=0.02)
    fig.subplots_adjust(right=0.74)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def sample_week(prices: pd.DataFrame, schedules: dict, start, path, title: str):
    """Price on top, then one panel of net power (discharge minus charge) per strategy."""
    days = prices.loc[start: pd.Timestamp(start) + pd.Timedelta(days=6)]
    idx = [prices.index.get_loc(d) for d in days.index]
    hours = np.arange(days.size)
    names = [n for n in COLOURS if n in schedules]
    fig, axes = plt.subplots(1 + len(names), 1, figsize=(9, 2.2 + 1.8 * len(names)), sharex=True,
                             facecolor=SURFACE, gridspec_kw={"height_ratios": [1.4] + [1] * len(names)})
    ax = axes[0]
    _style(ax)
    ax.step(hours, days.to_numpy().ravel(), where="post", color=INK, linewidth=1.5)
    ax.set_ylabel("Price ($/MWh)", color=MUTED, fontsize=9)
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    for ax, name in zip(axes[1:], names):
        _style(ax)
        net = np.concatenate([schedules[name][i].discharge - schedules[name][i].charge for i in idx])
        ax.bar(hours + 0.5, net, width=0.8, color=COLOURS[name])
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.set_ylabel("MW", color=MUTED, fontsize=9)
        ax.set_title(f"{name}: discharge (+) / charge (-)", loc="left", fontsize=9, color=INK, pad=3)
    ticks = np.arange(0, days.size + 1, 24)
    axes[-1].set_xticks(ticks[:-1])
    axes[-1].set_xticklabels([d.strftime("%a %d %b") for d in days.index], ha="left")
    for ax in axes:
        for t in ticks:
            ax.axvline(t, color=GRID, linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def price_heatmap(prices: pd.DataFrame, path, title: str):
    """Hour of day against day of year. Colour is clipped at the 1st and 99th percentiles."""
    values = prices.to_numpy().T
    lo, hi = np.percentile(values, [1, 99])
    fig, ax = plt.subplots(figsize=(9, 3.6), facecolor=SURFACE)
    im = ax.imshow(values, aspect="auto", origin="lower", cmap="Blues", vmin=lo, vmax=hi,
                   extent=[0, values.shape[1], 0, 24], interpolation="nearest")
    month_starts = [i for i, d in enumerate(prices.index) if d.day == 1]
    ax.set_xticks(month_starts)
    ax.set_xticklabels([prices.index[i].strftime("%b") for i in month_starts], ha="left")
    ax.set_yticks([0, 6, 12, 18, 24])
    ax.set_ylabel("Hour of day (EST)", color=MUTED, fontsize=9)
    ax.tick_params(colors=MUTED, labelsize=9)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    bar = fig.colorbar(im, ax=ax, pad=0.02, extend="both")
    bar.set_label("$/MWh", color=MUTED, fontsize=9)
    bar.ax.tick_params(colors=MUTED, labelsize=9)
    bar.outline.set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
