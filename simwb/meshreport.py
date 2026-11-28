"""The mesh report: one page (HTML, or PDF made from the same page with Qt's own document printer, no extra package) with what the mesh is made of:
the settings, the statistics and the verdict, the cell budget (which cell sizes use the cells), the findings, a histogram of each metric and cuts through the
mesh coloured by cell size and by quality.

It also holds the slice renderer: `render_section` draws a cut through a tetrahedral or hex-dominant mesh into an image with Qt's painter (no OpenGL, so it works
without a window), which is what the pictures of the report and the benchmarks use.

Needs a Qt application (QGuiApplication) for the pictures and the PDF: the desktop app has one; a script must make one first."""
from __future__ import annotations

import base64
import html
import time
from pathlib import Path

import numpy as np

from . import meshviz

HIST_METRICS = ("size", "aspect", "growth", "volume", "quality")
CUTS = (("x", "size"), ("y", "size"), ("z", "size"), ("y", "quality"))


# ------------------------------------------------------------------------------------------------------------------------ pictures
def _png(img) -> str:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    ba, buf = QByteArray(), QBuffer()
    buf.setBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(ba)).decode()


def _decode(p: dict):
    pos = np.frombuffer(base64.b64decode(p["positions"]), np.float32).reshape(-1, 3).astype(np.float64)
    idx = np.frombuffer(base64.b64decode(p["indices"]), np.uint32).reshape(-1, 3).astype(np.int64)
    val = np.frombuffer(base64.b64decode(p["values"]), np.float32).astype(np.float64)
    return pos, idx, val


def _colorbar(p, x, y, w, h, metric, rng, unit, label):
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont

    from .qt.metrichist import bin_color, fmt_value

    n = 128
    for i in range(n):
        v = rng[0] + (rng[1] - rng[0]) * i / (n - 1)
        if metric == "size" and rng[0] > 0:
            v = rng[0] * (rng[1] / rng[0]) ** (i / (n - 1))
        p.fillRect(QRectF(x + w * i / n, y, w / n + 1, h), QColor.fromRgbF(*bin_color(metric, v, rng)))
    p.setPen(QColor("#1c2330"))
    f = QFont("Segoe UI", 8)
    p.setFont(f)
    u = f" {unit}" if unit and unit != "-" else ""
    p.drawText(QRectF(x, y + h + 1, w, 14), Qt.AlignLeft, f"{fmt_value(rng[0])}{u}")
    p.drawText(QRectF(x, y + h + 1, w, 14), Qt.AlignRight, f"{fmt_value(rng[1])}{u}")
    p.drawText(QRectF(x, y + h + 1, w, 14), Qt.AlignHCenter, label)


def render_section(pr, axis: str, frac: float = 0.5, metric: str = "size", size=(560, 380), zoom: float | None = None, at_body: bool = True):
    """A cut through the stored mesh across `axis` at `frac` (0..1 of the mesh along it) coloured by `metric` (quality, size, aspect, growth, volume), looked at
    from the side of the cut, as a QImage with a colour bar (at_body: in a flow domain the cut goes through the middle of the body, not of the air). zoom: show the box of that many body sizes round the body (None: the whole mesh; a flow domain
    many body lengths wide is shown round the body by default). The cells of the cut are drawn as the crinkle section the 3D view shows."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF

    from .qt.metrichist import bin_color

    m = pr.load_mesh()
    ax = "xyz".index(axis)
    lo, hi = float(m["nodes"][:, ax].min()), float(m["nodes"][:, ax].max())
    info0 = pr.data["geometry"].get("info") or {}
    if at_body and info0.get("parts_bbox") and (hi - lo) > 3 * (info0["parts_bbox"][3 + ax] - info0["parts_bbox"][ax]):
        frac = min(max((0.5 * (info0["parts_bbox"][ax] + info0["parts_bbox"][3 + ax]) - lo) / (hi - lo), 0.0), 1.0)      # (a domain many body lengths wide: the cut goes through the body, not the middle of the air)
    d = pr.mesh_view(axis, lo + (hi - lo) * frac, False, None, metric)
    img = QImage(size[0], size[1], QImage.Format_RGB32)
    img.fill(QColor("#ffffff"))
    if not d.get("n_tris"):
        return img
    pos, idx, val = _decode(d)
    tri = pos[idx]
    u, v = [a for a in range(3) if a != ax]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    top = np.abs(nrm[:, ax]) > 0.5 * np.linalg.norm(nrm, axis=1)                   # (the faces of the cut, not the steps between them)
    tri, val = tri[top], val[top]
    depth = tri[:, :, ax].mean(1)
    order = np.argsort(depth)
    tri, val = tri[order], val[order]
    info = pr.data["geometry"].get("info") or {}
    bb = np.asarray(info.get("parts_bbox") or [*pos.min(0), *pos.max(0)], float)
    body = float(max(bb[3:] - bb[:3])) or 1.0
    wlo, whi = pos.min(0), pos.max(0)
    if zoom is None:
        zoom = 3.0 if float((whi - wlo).max()) > 8 * body else None
    if zoom:
        c = (bb[:3] + bb[3:]) / 2
        wlo, whi = c - zoom * body, c + zoom * body
    w, h = size[0] - 16, size[1] - 44
    ru, rv = float(whi[u] - wlo[u]) or 1.0, float(whi[v] - wlo[v]) or 1.0
    s = min(w / ru, h / rv)
    ox, oy = 8 + (w - ru * s) / 2, 6 + (h - rv * s) / 2 + rv * s
    rng = d.get("range") or [float(val.min()), float(val.max())]
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, False)
    p.setClipRect(int(ox), int(oy - rv * s), int(ru * s) + 1, int(rv * s) + 1)
    p.setPen(Qt.NoPen)
    metric_key = d.get("metric", metric)
    for t, x in zip(tri, val):
        if not np.isfinite(x):
            continue
        poly = QPolygonF([QPointF(ox + (q[u] - wlo[u]) * s, oy - (q[v] - wlo[v]) * s) for q in t])
        p.setBrush(QColor.fromRgbF(*bin_color(metric_key, x, rng)))
        p.setPen(QPen(QColor(0, 0, 0, 70), 0.5))
        p.drawPolygon(poly)
    p.setClipping(False)
    p.setPen(QPen(QColor("#9aa4b2"), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRect(int(ox), int(oy - rv * s), int(ru * s), int(rv * s))
    _colorbar(p, 8, size[1] - 34, size[0] - 16, 8, metric_key, rng, d.get("unit") or "", d.get("label", ""))
    p.end()
    return img


def render_histogram(d: dict, metric: str, size=(520, 150)):
    """The histogram of a metric (the mesh metrics' dict) as a QImage in the colours of the 3D legend, with the dashed line where the findings warn."""
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

    from .qt.metrichist import MARKS, bin_centres, bin_color, fmt_value, mark_position

    img = QImage(size[0], size[1], QImage.Format_RGB32)
    img.fill(QColor("#ffffff"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    r = QRectF(4, 6, size[0] - 8, size[1] - 30)
    h, e = d["histogram"], d["edges"]
    n, mx = len(h), (max(d["histogram"]) or 1)
    rng = d.get("range") or [e[0], e[-1]]
    cen = bin_centres(e)
    w = r.width() / n
    p.setPen(Qt.NoPen)
    for i, c in enumerate(h):
        bh = np.sqrt(c / mx) * r.height() if c else 0
        p.setBrush(QColor.fromRgbF(*bin_color(metric, cen[i], rng)))
        p.drawRect(QRectF(r.left() + i * w + 0.5, r.bottom() - bh, max(w - 1, 1), bh))
    for val, _t in MARKS.get(metric, []):
        pos = mark_position(e, val)
        if pos is not None:
            p.setPen(QPen(QColor("#1c2330"), 1, Qt.DashLine))
            p.drawLine(QPointF(r.left() + pos * w, r.top()), QPointF(r.left() + pos * w, r.bottom()))
    p.setPen(QPen(QColor("#7a8598"), 1))
    p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.right(), r.bottom()))
    p.setFont(QFont("Segoe UI", 8))
    p.setPen(QColor("#4b5568"))
    unit = f" {d['unit']}" if d.get("unit") and d["unit"] != "-" else ""
    p.drawText(QRectF(r.left(), r.bottom() + 3, r.width(), 14), Qt.AlignLeft, fmt_value(e[0]) + unit)
    p.drawText(QRectF(r.left(), r.bottom() + 3, r.width(), 14), Qt.AlignHCenter, "fewer cells (square-root scale) - " + d["label"].lower())
    p.drawText(QRectF(r.left(), r.bottom() + 3, r.width(), 14), Qt.AlignRight, fmt_value(e[-1]) + unit)
    p.end()
    return img


# ------------------------------------------------------------------------------------------------------------------------ the page
def _e(x) -> str:
    return html.escape(str(x))


def _table(rows, head=None) -> str:
    out = "<table>"
    if head:
        out += "<tr>" + "".join(f"<th>{_e(h)}</th>" for h in head) + "</tr>"
    for r in rows:
        out += "<tr>" + "".join(f"<td>{c if str(c).startswith('<') else _e(c)}</td>" for c in r) + "</tr>"
    return out + "</table>"


def _bar(pct: float, colour: str = "#2f6fdb") -> str:
    pct = max(min(float(pct), 100.0), 0.5)
    return (f"<table width='100%' cellspacing='0' cellpadding='0' style='margin:0;border:0'><tr><td width='{pct:.1f}%' bgcolor='{colour}' style='border:0;padding:0'>&nbsp;</td>"
            f"<td style='border:0;padding:0'>&nbsp;</td></tr></table>")


def settings_rows(pr) -> list:
    """The settings the mesh was made with, in words: [(label, value)]."""
    m, d = pr.data["mesh"], pr.data
    rows = [("Mesh type", "Hex-dominant (OpenFOAM snappyHexMesh)" if m.get("method") == "hex" else "Tetrahedral (Gmsh)"),
            ("Resolution preset", str(m.get("preset", "medium")).replace("_", " "))]
    if m.get("smart"):
        rows.append(("Smart sizing", "on"))
    for k, lab in (("size_max", "Largest cell"), ("size_min", "Smallest cell"), ("defeature", "Defeaturing")):
        if m.get(k):
            rows.append((lab, f"{m[k] * 1000:.3g} mm"))
    ly = m.get("layers") or {}
    if ly.get("n"):
        rows.append(("Wall layers", f"{ly['n']} layers, growth {ly.get('growth', 1.2):g}" + (f", first {ly['first'] * 1000:.3g} mm" if ly.get("first") else "")
                     + (f", target y+ {ly['y_plus']:g}" if ly.get("y_plus") else "")))
    for r in m.get("refinements") or []:
        rows.append((f"Part sizing: {r.get('part') or r.get('name') or 'faces'}", f"{r['size'] * 1000:.3g} mm on {len(r.get('faces') or [])} faces"))
    for r in m.get("regions") or []:
        rows.append((f"Refinement region: {r.get('name', '')}", f"{r.get('shape', 'box')}, {r['size'] * 1000:.3g} mm inside"))
    if m.get("edge_refine"):
        rows.append(("Sharp-edge refinement", f"edge cell size = wall cell size / {m['edge_refine'].get('factor', 6):g}"))
    return rows


def build(pr, with_images: bool = True) -> dict:
    """The report of the stored mesh: {title, html (images as 'img:N' placeholders when with_images, data URIs through `inline`), images: [QImage], ...}.
    Use `write_html` / `write_pdf`."""
    from .qt.meshtools import mesh_notes, stats_rows
    from .qt.panels.mesh import verdict

    m = pr.data["mesh"]
    st = m.get("stats")
    if not st or m.get("status") not in ("ready", "stale"):
        raise ValueError("There is no mesh to report: generate the mesh first")
    state = pr.state()
    metrics = pr.mesh_metrics()
    images: list = []

    def img(qimg, alt, width=560) -> str:
        if not with_images or qimg is None:
            return ""
        images.append(qimg)
        return f"<img src='@IMG{len(images) - 1}@' alt='{_e(alt)}' width='{width}'>"

    name = pr.data.get("name", pr.id)
    tone, words = verdict((st.get("quality") or {}), st)
    h = [f"<h1>{_e(name)} - mesh report</h1>",
         f"<p class='sub'>{_e(pr.spec.get('label', pr.data.get('study')))} · {_e(settings_rows(pr)[0][1])} · {st['cells']:,} cells · made in {st.get('seconds', '?')} s · {time.strftime('%Y-%m-%d %H:%M')}</p>"]
    if m.get("status") == "stale":
        h.append("<div class='warn'>The setup changed after this mesh was made: the report describes the mesh as it is stored.</div>")
    h.append(f"<div class='{'ok' if tone == 'ok' else 'warn'}'>{'&#10003; ' if tone == 'ok' else '&#9888; '}{_e(words)}</div>")
    t2, warn, info = mesh_notes(state)
    for w in warn:
        h.append(f"<div class='warn'>&#9888; {w}</div>")
    h.append("<h2>Statistics</h2>" + _table(stats_rows(state)))
    h.append("<h2>Settings</h2>" + _table(settings_rows(pr)))
    h.append("<h2>Cell budget</h2><p class='sub'>Which cell sizes use the cells (and so the memory and the run time): the share of the cells and of the volume in each size band.</p>")
    rows = [(f"{b['size_from']:.3g} to {b['size_to']:.3g} mm", f"{b['cells']:,}", f"{b['cells_percent']:.1f} %", _bar(b["cells_percent"] / max(x["cells_percent"] for x in metrics["budget"]) * 100), f"{b['volume_percent']:.1f} %")
            for b in metrics["budget"]]
    h.append(_table(rows, ["Cell size", "Cells", "Share of the cells", "", "Share of volume"]).replace("<th></th>", "<th width='30%'></th>", 1))
    h.append("<h2>Findings</h2>")
    fnd = list(metrics.get("findings") or []) + [x for x in info]
    h.append("<ul>" + "".join(f"<li>{_e(f) if not f.startswith('<') else f}</li>" for f in fnd) + "</ul>" if fnd else "<p>Nothing stands out: no abrupt size changes, no slivers, no fine region that uses most of the cells.</p>")
    h.append("<h2>Histograms</h2><p class='sub'>How many cells have each value; the bars have the colours of the 3D view. A dashed line marks the value the findings warn about "
             "(a size jump of 2, an aspect ratio of 20).</p>")
    cells = []
    for k in HIST_METRICS:
        if k in metrics["metrics"]:
            d = metrics["metrics"][k]
            cells.append(f"<h3>{_e(d['label'])}</h3>" + img(render_histogram(d, k), f"{d['label']} histogram", 360)
                         + f"<br><span class='sub'>median {meshviz_fmt(d.get('median'))}, 1 % to 99 % {meshviz_fmt(d.get('p01'))} to {meshviz_fmt(d.get('p99'))}</span>")
    if cells:
        h.append("<table>" + "".join("<tr>" + "".join(f"<td width='50%' style='border:0'>{c}</td>" for c in cells[i:i + 2]) + "</tr>" for i in range(0, len(cells), 2)) + "</table>")
    if with_images:
        h.append("<h2>Cuts through the mesh</h2><p class='sub'>Through the middle of the mesh across each axis, coloured by cell size (small cells blue, large red; logarithmic), and by quality "
                 "(red is poor). A flow domain is shown round the body.</p>")
        cuts = []
        for ax, met in CUTS:
            try:
                q = render_section(pr, ax, 0.5, met)
            except Exception:  # noqa: BLE001 - a picture that cannot be made is left out, the rest of the report stands
                continue
            cuts.append(f"<h3>Across {ax.upper()}: {'cell size' if met == 'size' else 'cell quality'}</h3>" + img(q, f"cut across {ax}", 360))
        if cuts:
            h.append("<table>" + "".join("<tr>" + "".join(f"<td width='50%' style='border:0'>{c}</td>" for c in cuts[i:i + 2]) + "</tr>" for i in range(0, len(cuts), 2)) + "</table>")
    ly = (st.get("layers") or {})
    if ly.get("made") and ly.get("patches"):
        h.append("<h2>Wall layers</h2>" + _table([(r["name"], r.get("faces") or "", (f"{r['made']:.2f} of {r['target']}" if r.get("made") is not None else ""),
                                               f"{r['first_mm']:.3g} mm" if r.get("first_mm") else "", f"{r['total_mm']:.3g} mm" if r.get("total_mm") else "",
                                               f"{r['percent']:.0f} %" if r.get("percent") is not None else "") for r in ly["patches"]],
                                              ["Wall", "Faces", "Layers made of planned", "First layer", "All layers", "Of the planned thickness"]))
    from . import __version__
    h.append(f"<p class='foot'>Open Mechanical Simulation Workbench {_e(__version__)}. Sizes are the edge of the regular tetrahedron of the cell's volume (a hex-dominant mesh: "
             "of the cube of its volume).</p>")
    return {"title": f"{name} - mesh report", "body": "\n".join(h), "images": images}


def meshviz_fmt(v) -> str:
    return "-" if v is None else f"{v:.3g}"


CSS = ("body{font:13px 'Segoe UI',sans-serif;color:#1c2330;max-width:900px;margin:28px auto;padding:0 20px} h1{font-size:22px;margin:0 0 2px} "
       "h2{font-size:16px;margin:22px 0 6px;border-bottom:2px solid #d5dbe3} h3{font-size:13px;margin:10px 0 4px} .sub{color:#5b6675} "
       "table{border-collapse:collapse;width:100%;margin:4px 0 10px} td,th{border:1px solid #d5dbe3;padding:3px 7px;text-align:left;vertical-align:top} th{background:#eef1f5} "
       "img{border:1px solid #d5dbe3} .warn{background:#fff6e0;border-left:4px solid #d29922;padding:5px 9px;margin:6px 0} "
       ".ok{background:#e8f6ee;border-left:4px solid #2a9d5c;padding:5px 9px;margin:6px 0} ul{margin:4px 0 10px 20px;padding:0} .foot{color:#5b6675;font-size:11px;margin-top:24px}")


def html_page(rep: dict, inline: bool = True) -> str:
    body = rep["body"]
    for i, im in enumerate(rep["images"]):
        body = body.replace(f"@IMG{i}@", ("data:image/png;base64," + _png(im)) if inline else f"img{i}")
    return f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_e(rep['title'])}</title><style>{CSS}</style></head><body>{body}</body></html>"


def write_html(pr, path) -> str:
    """The report as one self-contained HTML page (the pictures are inside it)."""
    rep = build(pr)
    Path(path).write_text(html_page(rep), encoding="utf-8")
    return str(path)


def write_pdf(pr, path) -> str:
    """The same page as a PDF (Qt's document printer: A4, no extra package)."""
    from PySide6.QtCore import QMarginsF, QSizeF, QUrl
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    rep = build(pr)
    doc = QTextDocument()
    for i, im in enumerate(rep["images"]):
        doc.addResource(QTextDocument.ImageResource, QUrl(f"img{i}"), im)
    doc.setDefaultStyleSheet(CSS.replace("max-width:900px;margin:28px auto;padding:0 20px", ""))
    doc.setHtml(html_page(rep, inline=False).replace("<style>" + CSS + "</style>", ""))
    w = QPdfWriter(str(path))
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(14, 14, 14, 14)))
    w.setTitle(rep["title"])
    w.setResolution(96)                                  # (the page in the pixels the page's own sizes use: 794 x 1123 for A4, so the text and the pictures keep their sizes)
    doc.setPageSize(QSizeF(w.width(), w.height()))
    doc.print_(w)
    return str(path)
