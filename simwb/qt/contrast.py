"""WCAG contrast arithmetic (pure Python, no Qt): used by the tests that keep both themes readable, and to tune the colour tokens."""
from __future__ import annotations


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    """Relative luminance of (r, g, b) in 0-255."""
    r, g, b = (_lin(v / 255.0) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def hex_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ratio(a, b) -> float:
    """Contrast ratio (1 to 21) of two colours given as '#rrggbb' or (r, g, b)."""
    ra = hex_rgb(a) if isinstance(a, str) else a
    rb = hex_rgb(b) if isinstance(b, str) else b
    la, lb = luminance(ra), luminance(rb)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def over(fg, bg, alpha: float):
    """fg drawn with opacity alpha over bg -> the (r, g, b) seen."""
    f = hex_rgb(fg) if isinstance(fg, str) else fg
    b = hex_rgb(bg) if isinstance(bg, str) else bg
    return tuple(round(alpha * x + (1 - alpha) * y) for x, y in zip(f, b))
