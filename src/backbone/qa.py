"""QA overlays and an alignment check for a fetched area.

    python -m backbone.qa --area utrecht_centrum

Writes to reports/phase1/<area>/:
- overview.jpg: 25 cm imagery with BGT wegdeel (by functie) and OSM drivable ways.
- crop_<label>.jpg: 8 cm crops, imagery alone next to imagery + overlays.
- alignment.json/.png: best-fit shift of BGT boundaries against image edges.
"""

import argparse
import json
import logging
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402
from scipy import ndimage  # noqa: E402

from backbone.areas import AREAS, BBox, bbox_name, parse_bbox  # noqa: E402
from backbone.fetch import DATA_DIR  # noqa: E402

log = logging.getLogger("backbone.qa")

REPORTS_DIR = Path("reports/phase1")
# Windows with fewer BGT edge pixels (e.g. only fields) give no reliable offset.
MIN_EDGE_PIXELS = 5000

# BGT styling: drivable classes filled, others outlined.
DRIVABLE_STYLE = {"facecolor": "#ffb000", "alpha": 0.35}
SERVICE_PAVED_STYLE = {"facecolor": "#3ddc84", "alpha": 0.35}
OUTLINE_COLORS = {
    "fietspad": "#e0201b",
    "parkeervlak": "#1f6fff",
    "voetpad": "#d0d0d0",
    "voetpad op trap": "#d0d0d0",
    "voetgangersgebied": "#d0d0d0",
    "spoorbaan": "#7a3db8",
}
OSM_COLOR = "#00e5ff"
OSM_EXCLUDED_COLOR = "#ff2bd6"


def load(area_dir: Path):
    bgt = gpd.read_file(area_dir / "bgt_wegdeel.gpkg", layer="wegdeel")
    osm = gpd.read_file(area_dir / "osm_drivable.gpkg", layer="ways")
    excluded = gpd.read_file(area_dir / "osm_drivable.gpkg", layer="excluded")
    terrain = gpd.read_file(area_dir / "bgt_onbegroeidterreindeel.gpkg")
    # Paved terrain under a service way is drivable; draw it with wegdeel.
    bgt = pd.concat(
        [bgt, terrain[terrain["service_paved"]].assign(functie="service_paved", drivable=False)],
        ignore_index=True,
    )
    return bgt, osm, excluded


def read_rgb(path: Path, bbox: BBox, max_px: int | None = None):
    """Read an RGB window; optionally decimate so the long side is <= max_px."""
    with rasterio.open(path) as ds:
        win = from_bounds(*bbox.as_tuple(), transform=ds.transform).round_offsets().round_lengths()
        shape = (3, win.height, win.width)
        if max_px and max(win.height, win.width) > max_px:
            f = max(win.height, win.width) / max_px
            shape = (3, round(win.height / f), round(win.width / f))
        arr = ds.read(window=win, out_shape=shape, resampling=rasterio.enums.Resampling.average)
        bounds = ds.window_bounds(win)
    return arr.transpose(1, 2, 0), bounds


def draw_layers(ax, bgt, osm, excluded, lw_scale=1.0):
    drv = bgt[bgt["drivable"]]
    drv.plot(ax=ax, linewidth=0.6 * lw_scale, edgecolor="#ffb000", **DRIVABLE_STYLE)
    sp = bgt[bgt["functie"] == "service_paved"]
    if len(sp):
        sp.plot(ax=ax, linewidth=0.6 * lw_scale, edgecolor="#3ddc84", **SERVICE_PAVED_STYLE)
    for functie, color in OUTLINE_COLORS.items():
        sub = bgt[bgt["functie"] == functie]
        if len(sub):
            sub.boundary.plot(ax=ax, color=color, linewidth=0.6 * lw_scale)
    osm.plot(ax=ax, color=OSM_COLOR, linewidth=1.2 * lw_scale)
    if len(excluded):
        excluded.plot(ax=ax, color=OSM_EXCLUDED_COLOR, linewidth=1.2 * lw_scale, linestyle=(0, (2, 1)))


def legend(ax):
    handles = [
        Patch(facecolor=DRIVABLE_STYLE["facecolor"], alpha=0.6, label="BGT drivable (fill)"),
        Patch(facecolor=SERVICE_PAVED_STYLE["facecolor"], alpha=0.6, label="BGT paved terrain under service way"),
        Line2D([], [], color=OUTLINE_COLORS["fietspad"], label="BGT fietspad"),
        Line2D([], [], color=OUTLINE_COLORS["parkeervlak"], label="BGT parkeervlak"),
        Line2D([], [], color=OUTLINE_COLORS["voetpad"], label="BGT voetpad/-gebied"),
        Line2D([], [], color=OUTLINE_COLORS["spoorbaan"], label="BGT spoorbaan"),
        Line2D([], [], color=OSM_COLOR, lw=2, label="OSM drivable way"),
        Line2D([], [], color=OSM_EXCLUDED_COLOR, lw=2, ls="--", label="OSM excluded (parking / hidden / private yard)"),
    ]
    ax.legend(handles=handles, loc="lower left", fontsize=8, framealpha=0.85)


def render_overview(area_dir: Path, out: Path, bbox: BBox, bgt, osm, excluded):
    img, (l, b, r, t) = read_rgb(area_dir / "luchtfoto_25cm.tif", bbox, max_px=4000)
    fig, ax = plt.subplots(figsize=(20, 20), dpi=200)
    ax.imshow(img, extent=(l, r, b, t))
    draw_layers(ax, bgt, osm, excluded, lw_scale=0.7)
    ax.set_xlim(l, r)
    ax.set_ylim(b, t)
    ax.set_title(f"{area_dir.name}: Luchtfoto 2025 25 cm + BGT wegdeel + OSM (EPSG:28992)")
    legend(ax)
    fig.tight_layout()
    fig.savefig(out, pil_kwargs={"quality": 88})
    plt.close(fig)


def render_crop(area_dir: Path, out: Path, label: str, x: float, y: float, bgt, osm, excluded, half=80.0):
    bb = BBox(x - half, y - half, x + half, y + half)
    img, (l, b, r, t) = read_rgb(area_dir / "luchtfoto_8cm.tif", bb)
    clip = bgt.cx[l:r, b:t]
    osm_clip = osm.cx[l:r, b:t]
    excl_clip = excluded.cx[l:r, b:t]
    fig, axes = plt.subplots(1, 2, figsize=(20, 10), dpi=150)
    for ax in axes:
        ax.imshow(img, extent=(l, r, b, t))
        ax.set_xlim(l, r)
        ax.set_ylim(b, t)
    draw_layers(axes[1], clip, osm_clip, excl_clip, lw_scale=1.5)
    axes[0].set_title(f"{label}: Luchtfoto 2025 8 cm")
    axes[1].set_title(f"{label}: + BGT + OSM  (centre {x:.0f}, {y:.0f})")
    legend(axes[1])
    fig.tight_layout()
    fig.savefig(out, pil_kwargs={"quality": 88})
    plt.close(fig)


def edge_offset(
    tif: Path, bbox: BBox, bgt, res: float, max_shift_m: float = 2.0, min_edge_pixels: int = MIN_EDGE_PIXELS
):
    """Shift BGT ground-level boundaries over the image gradient magnitude and
    return the (dx, dy) in metres with the strongest mean edge response.

    A shift of (0, 0) means the BGT edges line up with visible edges (kerbs,
    paving changes). Positive dx moves BGT east, positive dy moves BGT north.
    """
    img, (l, b, r, t) = read_rgb(tif, bbox, max_px=round(bbox.width / res))
    gray = img.astype(np.float32).mean(axis=2)
    gray = ndimage.gaussian_filter(gray, 1.0)
    grad = np.hypot(ndimage.sobel(gray, 0), ndimage.sobel(gray, 1))
    h, w = gray.shape
    transform = rasterio.transform.from_bounds(l, b, r, t, w, h)
    ground = bgt[(bgt["relatieve_hoogteligging"] == 0)].cx[l:r, b:t]
    # All boundaries are used. Outer edges along facades can be pulled off by
    # leaning roofs (relief displacement); scoring only internal boundaries was
    # tried and was noisier, because many kerbs separate identical paving.
    lines = [g for g in ground.boundary if not g.is_empty]
    mask = rasterize(lines, out_shape=(h, w), transform=transform, all_touched=True).astype(bool)
    # Ignore the window border, where shifted masks would wrap around.
    n = int(round(max_shift_m / (bbox.width / w)))
    mask[:n + 1, :] = mask[-n - 1 :, :] = mask[:, : n + 1] = mask[:, -n - 1 :] = False
    px = bbox.width / w
    rows, cols = np.nonzero(mask)
    if len(rows) < max(min_edge_pixels, 1):
        return {"dx_m": None, "dy_m": None, "pixel_m": round(px, 3), "peak_over_zero": None,
                "edge_pixels": int(len(rows))}, None  # fmt: skip
    scores = np.zeros((2 * n + 1, 2 * n + 1))
    for i, dy in enumerate(range(-n, n + 1)):
        for j, dx in enumerate(range(-n, n + 1)):
            # Moving BGT north by dy pixels is a shift of -dy rows.
            scores[i, j] = grad[rows - dy, cols + dx].mean()
    i, j = np.unravel_index(scores.argmax(), scores.shape)
    return {
        "dx_m": round((j - n) * px, 3),
        "dy_m": round((i - n) * px, 3),
        "pixel_m": round(px, 3),
        "peak_over_zero": round(float(scores.max() / scores[n, n]), 4),
        "edge_pixels": int(mask.sum()),
    }, scores


def alignment_check(area_dir: Path, out_dir: Path, bbox: BBox, bgt, n_side: int = 4):
    """Run edge_offset on an n_side x n_side grid of windows for each imagery product."""
    results = {}
    step_x, step_y = bbox.width / n_side, bbox.height / n_side
    fig, axes = plt.subplots(2, 1, figsize=(6, 11))
    for k, (key, res) in enumerate([("8cm", 0.16), ("25cm", 0.25)]):
        tif = area_dir / f"luchtfoto_{key}.tif"
        rows = []
        for iy in range(n_side):
            for ix in range(n_side):
                win = BBox(
                    bbox.xmin + ix * step_x,
                    bbox.ymin + iy * step_y,
                    bbox.xmin + (ix + 1) * step_x,
                    bbox.ymin + (iy + 1) * step_y,
                )
                r, _ = edge_offset(tif, win, bgt, res)
                r["window"] = win.as_tuple()
                rows.append(r)
                log.info("%s window %s: dx=%s dy=%s", key, (ix, iy), r["dx_m"], r["dy_m"])
        used = [r for r in rows if r["dx_m"] is not None]
        dx = np.array([r["dx_m"] for r in used])
        dy = np.array([r["dy_m"] for r in used])
        results[key] = {
            "analysis_pixel_m": rows[0]["pixel_m"],
            "median_dx_m": float(np.median(dx)),
            "median_dy_m": float(np.median(dy)),
            "max_abs_shift_m": float(np.max(np.hypot(dx, dy))),
            "windows_used": len(used),
            "windows": rows,
        }
        ax = axes[k]
        ax.scatter(dx, dy, s=40, alpha=0.7)
        ax.axhline(0, color="k", lw=0.5)
        ax.axvline(0, color="k", lw=0.5)
        lim = 2.1
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_aspect("equal")
        ax.set_xlabel("best-fit BGT shift east (m)")
        ax.set_ylabel("best-fit BGT shift north (m)")
        ax.set_title(f"{key}: {len(used)}/{len(rows)} windows of {step_x:.0f} m, analysed at {rows[0]['pixel_m']} m/px")
    fig.tight_layout()
    fig.savefig(out_dir / "alignment.png", dpi=120)
    plt.close(fig)
    (out_dir / "alignment.json").write_text(json.dumps(results, indent=2))
    return results


def run(name: str, bbox: BBox, crops) -> Path:
    area_dir = DATA_DIR / name
    out_dir = REPORTS_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    bgt, osm, excluded = load(area_dir)
    log.info("overview ...")
    render_overview(area_dir, out_dir / "overview.jpg", bbox, bgt, osm, excluded)
    for label, x, y in crops:
        log.info("crop %s ...", label)
        render_crop(area_dir, out_dir / f"crop_{label}.jpg", label, x, y, bgt, osm, excluded)
    log.info("alignment ...")
    res = alignment_check(area_dir, out_dir, bbox, bgt)
    for key, r in res.items():
        log.info(
            "%s: median shift (%.2f, %.2f) m, max |shift| %.2f m",
            key, r["median_dx_m"], r["median_dy_m"], r["max_abs_shift_m"],
        )  # fmt: skip
    return out_dir


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--area", choices=sorted(AREAS))
    g.add_argument("--bbox", type=parse_bbox, help="xmin,ymin,xmax,ymax in EPSG:28992")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if args.area:
        a = AREAS[args.area]
        run(a.name, a.bbox, a.qa_crops)
    else:
        bb = args.bbox
        cx, cy = (bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2
        run(bbox_name(bb), bb, [("centre", cx, cy)])


if __name__ == "__main__":
    main()
