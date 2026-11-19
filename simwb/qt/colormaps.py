"""Colour maps of the results views: the same ones as the web view (turbo plus ParaView / matplotlib style presets), as
256-entry tables of RGB values (0-1). No Qt or VTK in here, so it can be tested on its own."""
from __future__ import annotations

import numpy as np

# stops of the presets (hex), interpolated linearly into a table; turbo is a polynomial fit
STOPS = {
    "viridis": ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"],
    "plasma": ["#0d0887", "#47039f", "#7301a8", "#9c179e", "#bd3786", "#d8576b", "#ed7953", "#fa9e3b", "#fdc328", "#f0f921"],
    "inferno": ["#000004", "#1b0c41", "#4a0c6b", "#781c6d", "#a52c60", "#cf4446", "#ed6925", "#fb9b06", "#f7d13d", "#fcffa4"],
    "cool-warm": ["#3b4cc0", "#6788ee", "#9abbff", "#c9d7f0", "#dddddd", "#f2cbb7", "#f7a889", "#e26952", "#b40426"],
    "rainbow": ["#0000ff", "#00a0ff", "#00ffff", "#00ff80", "#00ff00", "#ffff00", "#ff8000", "#ff0000"],
    "grayscale": ["#101010", "#f4f4f4"],
}
NAMES = ["turbo", *STOPS]


def turbo(t):
    """Google's Turbo (polynomial approximation), t in 0..1 (array or number) -> (..., 3)."""
    t = np.clip(np.asarray(t, float), 0.0, 1.0)
    r = 0.13572138 + t * (4.6153926 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))))
    g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))))
    b = 0.1066733 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))))
    return np.clip(np.stack([r, g, b], axis=-1), 0.0, 1.0)


def _hex(h: str):
    return [int(h[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]


def table(name: str = "turbo", n: int = 256, reverse: bool = False, bands: int = 0) -> np.ndarray:
    """(n, 3) RGB table of a colour map. bands > 1: quantised to that many flat colours (contour bands); reverse flips it."""
    if name != "turbo" and name not in STOPS:
        raise ValueError(f"Unknown colour map '{name}'. Available: {', '.join(NAMES)}")
    t = np.linspace(0.0, 1.0, n)
    if bands and bands > 1:
        t = np.minimum(np.floor(t * bands), bands - 1) / (bands - 1)
    if reverse:
        t = 1.0 - t
    if name == "turbo":
        return turbo(t)
    st = np.array([_hex(h) for h in STOPS[name]])
    x = t * (len(st) - 1)
    k = np.minimum(np.floor(x).astype(int), len(st) - 2)
    f = (x - k)[:, None]
    return st[k] + (st[k + 1] - st[k]) * f


def nice_ticks(lo: float, hi: float, n: int = 5) -> list:
    """Round tick values (1, 2, 5 x 10^k) inside [lo, hi], about n of them."""
    span = hi - lo
    if not (span > 0) or not np.isfinite(span):
        return [lo]
    raw = span / n
    mag = 10.0 ** np.floor(np.log10(raw))
    f = raw / mag
    step = (1 if f < 1.5 else 2 if f < 3.5 else 5 if f < 7.5 else 10) * mag
    out, v = [], np.ceil(lo / step - 1e-9) * step
    while v <= hi + step * 1e-9 and len(out) < 12:
        out.append(float(f"{v:.12g}"))
        v += step
    return out
