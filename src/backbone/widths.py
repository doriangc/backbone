"""Phase 3: ground-truth segment widths from BGT, and baseline errors in metres.

    python -m backbone.widths --area utrecht_centrum --area houten --area benschop

Per area, writes data/<area>/widths.gpkg with layers `profiles` (one point per
profile) and `segments` (per-segment summary). Writes reports/phase3/.

Baselines:
- B0: tag width from Phase 2 (width tag, lanes x lane width, class default).
- B1: per-class median true carriageway width from the *other* areas
  (leave-one-area-out), so no area is scored with values fitted on itself.
- B2: the tag width where a `width` or `lanes` tag exists, else B1. This is
  the strongest no-imagery baseline and the bar for Phase 5.
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
import shapely  # noqa: E402
from shapely.geometry import LineString  # noqa: E402

from backbone import config  # noqa: E402
from backbone.areas import AREAS, BBox  # noqa: E402
from backbone.baseline import eval_box, load_segments, segment_cells  # noqa: E402
from backbone.fetch import DATA_DIR  # noqa: E402
from backbone.profiles import (  # noqa: E402
    CARRIAGEWAY,
    CYCLE,
    DEFINITIONS,
    INRIT,
    PARKING,
    build_surface,
    cell_interval,
    junction_distances,
    make_profiles,
    measure,
    vector_runs,
)
from backbone.qa import read_rgb  # noqa: E402

log = logging.getLogger("backbone.widths")
REPORTS_DIR = Path("reports/phase3")


def segment_levels(segs: gpd.GeoDataFrame, surface: gpd.GeoDataFrame) -> pd.Series:
    """BGT level to measure each segment on: its OSM `layer` if BGT has surface
    at that level within 5 m of the segment, else ground level (0)."""
    layer = pd.to_numeric(segs["layer"], errors="coerce").fillna(0).astype(int)
    level = pd.Series(0, index=segs.index)
    for i in layer[layer != 0].index:
        near = surface.iloc[surface.sindex.query(segs.geometry[i].buffer(5), predicate="intersects")]
        if (near["level"] == layer[i]).any():
            level[i] = layer[i]
    return level


def run_area(name: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    area_dir = DATA_DIR / name
    segs = load_segments(name)
    cells = segment_cells(name, segs)
    surface = build_surface(area_dir)
    levels = segment_levels(segs, surface)
    jd = junction_distances(segs)

    prof = make_profiles(segs.geometry, config.PROFILE_SPACING_M, config.PROFILE_HALF_LENGTH_M)
    inside = shapely.contains_xy(eval_box(name), prof.point[:, 0], prof.point[:, 1])
    seg_len = segs.geometry.length.loc[prof.seg].to_numpy()
    junction = (prof.s < jd["start"].loc[prof.seg].to_numpy()) | (
        prof.s > seg_len - jd["end"].loc[prof.seg].to_numpy()
    )
    runs = vector_runs(prof, surface, levels.loc[prof.seg].to_numpy())
    stops = cell_interval(prof, cells)

    rows = []
    for k in range(len(prof.s)):
        if not inside[k]:
            continue
        row = {"seg": prof.seg[k], "s": prof.s[k], "junction": bool(junction[k])}
        for d, definition in DEFINITIONS.items():
            m = measure(runs[k], definition, config.OFFSET_SEARCH_M, config.GAP_TOL_M, stop=tuple(stops[k]))
            row[f"{d}_left"], row[f"{d}_right"] = m["left"], m["right"]
            row[f"{d}_cell_stop"] = m["cell_stop"]
            if d == "carriageway":
                row["offset"] = m["offset"]
        row["usable"] = not row["junction"] and not np.isnan(row["carriageway_left"])
        row.update(nx=prof.normal[k, 0], ny=prof.normal[k, 1])
        rows.append((row, prof.point[k]))
    p = gpd.GeoDataFrame(
        [r for r, _ in rows], geometry=gpd.points_from_xy(*np.array([pt for _, pt in rows]).T), crs=config.RD_EPSG
    )
    for d in DEFINITIONS:
        p[f"{d}_total"] = p[f"{d}_left"] + p[f"{d}_right"]
    p["segment_id"] = segs["segment_id"].loc[p["seg"]].to_numpy()

    summ = summarize_segments(p, segs, name)
    path = area_dir / "widths.gpkg"
    path.unlink(missing_ok=True)
    p.drop(columns="seg").to_file(path, layer="profiles", driver="GPKG")
    summ.to_file(path, layer="segments", driver="GPKG")
    log.info("%s: %d profiles in box, %d usable, %d segments", name, len(p), p["usable"].sum(), len(summ))
    return p, summ


def summarize_segments(p: pd.DataFrame, segs: gpd.GeoDataFrame, name: str) -> gpd.GeoDataFrame:
    out = []
    for seg, g in p.groupby("seg"):
        u = g[g["usable"]]
        nonj = g[~g["junction"]]
        r = {
            "segment_id": segs.at[seg, "segment_id"],
            "osm_id": segs.at[seg, "osm_id"],
            "area": name,
            "area_type": AREAS[name].area_type,
            "highway": segs.at[seg, "highway"],
            "width_source": segs.at[seg, "width_source"],
            "tag_width": segs.at[seg, "tag_width"],
            "length": segs.geometry[seg].length,
            "n_profiles": len(g),
            "n_nonjunction": len(nonj),
            "n_usable": len(u),
            "share_usable": len(u) / len(nonj) if len(nonj) else np.nan,
            "offset_median": u["offset"].median(),
            "share_offset": (u["offset"] > 0).mean() if len(u) else np.nan,
            "share_cell_stop": u["carriageway_cell_stop"].mean() if len(u) else np.nan,
        }
        for d in DEFINITIONS:
            for side in ("left", "right", "total"):
                v = u[f"{d}_{side}"]
                r[f"{d}_{side}"] = v.median()
            v = u[f"{d}_total"]
            r[f"{d}_p10"], r[f"{d}_p90"] = v.quantile(0.1), v.quantile(0.9)
        r["spread"] = r["carriageway_p90"] - r["carriageway_p10"]
        flags = []
        if len(nonj) == 0:
            flags.append("junction_only")
        elif len(u) < config.MIN_USABLE_PROFILES:
            flags.append("few_profiles")
        if len(u) and r["offset_median"] > 0:
            flags.append("offset")
        if len(u) >= config.MIN_USABLE_PROFILES and r["spread"] > config.SPREAD_FLAG_M:
            flags.append("spread")
        if len(u) and r["share_cell_stop"] > 0.5:
            flags.append("cell_limited")
        r["flags"] = ",".join(flags)
        r["scored"] = len(u) >= config.MIN_USABLE_PROFILES
        r["geometry"] = segs.geometry[seg]
        out.append(r)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=config.RD_EPSG)


def add_baselines(s: pd.DataFrame) -> pd.DataFrame:
    s = s.copy()
    s["base_class"] = s["highway"].str.removesuffix("_link")
    sc = s[s["scored"]]
    b1 = pd.Series(np.nan, index=s.index)
    for area in s["area"].unique():
        other = sc[sc["area"] != area]
        med = other.groupby("base_class")["carriageway_total"].median()
        here = s["area"] == area
        b1[here] = s.loc[here, "base_class"].map(med).fillna(other["carriageway_total"].median())
    s["b1_width"] = b1
    s["b2_width"] = s["tag_width"].where(s["width_source"] != "default", s["b1_width"])
    for b, col in (("b0", "tag_width"), ("b1", "b1_width"), ("b2", "b2_width")):
        for d in DEFINITIONS:
            s[f"{b}_err_{d}"] = s[col] - s[f"{d}_total"]
    return s


def error_table(s: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    def agg(g):
        r = {"segments": len(g), "km": g["length"].sum() / 1000,
             "true_cw_median": g["carriageway_total"].median(), "true_kerb_median": g["kerb_total"].median(),
             "b0_width_median": g["tag_width"].median()}  # fmt: skip
        for b in ("b0", "b1", "b2"):
            for d in DEFINITIONS:
                e = g[f"{b}_err_{d}"]
                r[f"{b}_{d}_mae"] = e.abs().mean()
                r[f"{b}_{d}_within_0.5"] = (e.abs() <= 0.5).mean()
                r[f"{b}_{d}_bias"] = e.median()
        return pd.Series(r)

    return s.groupby(by).apply(agg, include_groups=False) if by else agg(s).to_frame("all").T


PANEL_COLORS = {"cw": "#3ddc84", "kerb": "#1f6fff", "excluded": "#bbbbbb"}


def render_segments(sel: pd.DataFrame, profiles: dict, prefix: str, title: str, per_page: int = 10):
    surfaces = {}
    for page in range(0, len(sel), per_page):
        chunk = sel.iloc[page : page + per_page]
        fig, axes = plt.subplots(2, 5, figsize=(25, 10.8))
        for ax, (_, r) in zip(axes.flat, chunk.iterrows()):
            area = r["area"]
            if area not in surfaces:
                surfaces[area] = build_surface(DATA_DIR / area)
            surf = surfaces[area]
            g = r.geometry
            cx, cy = g.centroid.x, g.centroid.y
            half = min(max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) / 2 + 12, 90)
            bb = BBox(cx - half, cy - half, cx + half, cy + half)
            img, (l, b, rr, t) = read_rgb(DATA_DIR / area / "luchtfoto_8cm.tif", bb, max_px=800)
            ax.imshow(img, extent=(l, rr, b, t))
            local = surf.cx[l:rr, b:t]
            local[local["cls"] == CARRIAGEWAY].plot(ax=ax, color="#ffb000", alpha=0.25)
            local[local["cls"] == INRIT].plot(ax=ax, color="#b04cff", alpha=0.35)
            local[local["cls"].isin([PARKING, CYCLE])].boundary.plot(ax=ax, color="#ff5050", lw=0.6)
            gpd.GeoSeries([g]).plot(ax=ax, color="#00e5ff", lw=1.5)
            pr = profiles[area]
            pr = pr[pr["segment_id"] == r["segment_id"]]
            for _, q in pr.iterrows():
                x, y, n = q.geometry.x, q.geometry.y, np.array([q["nx"], q["ny"]])
                if not q["usable"]:
                    ax.plot(*np.array([[x, y] - n, [x, y] + n]).T, color=PANEL_COLORS["excluded"], lw=0.8)
                    continue
                kl, kr = q["kerb_left"], q["kerb_right"]
                ax.plot(*np.array([[x, y] - n * kr, [x, y] + n * kl]).T, color=PANEL_COLORS["kerb"], lw=0.8)
                cl, cr = q["carriageway_left"], q["carriageway_right"]
                ax.plot(*np.array([[x, y] - n * cr, [x, y] + n * cl]).T, color=PANEL_COLORS["cw"], lw=1.2)
            ax.set_xlim(l, rr)
            ax.set_ylim(b, t)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(
                f"{r['highway']} | cw {r['carriageway_total']:.1f} m [{r['carriageway_p10']:.1f}-"
                f"{r['carriageway_p90']:.1f}] | kerb {r['kerb_total']:.1f} | tag {r['tag_width']:.1f}\n"
                f"{area} {r['segment_id'][:30]} | {r['length']:.0f} m | {r['flags'] or 'no flags'}",
                fontsize=8.5,
            )
        for ax in axes.flat[len(chunk):]:
            ax.axis("off")
        fig.suptitle(
            f"{title}. Green: carriageway width per profile; blue: kerb-to-kerb; grey: excluded (junction "
            "or no carriageway). Orange fill: carriageway; purple: inrit; red outline: parking / cycle path",
            fontsize=10,
        )
        fig.tight_layout()
        fig.savefig(REPORTS_DIR / f"{prefix}_{page // per_page + 1:02d}.jpg", dpi=80, pil_kwargs={"quality": 85})
        plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", action="append", choices=sorted(AREAS), required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for old in REPORTS_DIR.glob("*.jpg"):
        old.unlink()

    profiles, summaries = {}, []
    for a in args.area:
        p, s = run_area(a)
        profiles[a] = p
        summaries.append(s)
    s = gpd.GeoDataFrame(pd.concat(summaries, ignore_index=True), geometry="geometry", crs=config.RD_EPSG)
    s = add_baselines(s)
    sc = s[s["scored"]]

    tables = {
        "all": error_table(sc, []),
        "by_area_type": error_table(sc, ["area_type"]),
        "by_highway": error_table(sc, ["highway"]),
        "by_width_source": error_table(sc, ["width_source"]),
    }
    flag_counts = s["flags"].str.split(",").explode().replace("", "none").value_counts()
    with open(REPORTS_DIR / "metrics.md", "w") as f:
        f.write("# Phase 3 metrics (generated by backbone.widths)\n\n")
        f.write(
            f"Segments with profiles: {len(s)}; scored (>= {config.MIN_USABLE_PROFILES} usable profiles): "
            f"{len(sc)} ({sc['length'].sum() / 1000:.1f} km). Errors = baseline - true total width (m); "
            "MAE is a mean, bias a median.\n\n"
        )
        f.write(f"## Flags (all segments with profiles)\n\n{flag_counts.to_markdown()}\n\n")
        for name, t in tables.items():
            f.write(f"## {name}\n\n{t.round(2).T.to_markdown()}\n\n")
    s.drop(columns="geometry").to_csv(REPORTS_DIR / "segments.csv", index=False, float_format="%.3f")

    rng = np.random.default_rng(0)
    sample = sc.iloc[rng.choice(len(sc), size=min(50, len(sc)), replace=False)]
    render_segments(sample, profiles, "random", "50 random scored segments (seed 0)")
    spread = sc[sc["flags"].str.contains("spread")]
    if len(spread):
        pick = spread.iloc[rng.choice(len(spread), size=min(20, len(spread)), replace=False)]
        render_segments(pick, profiles, "spread", "20 random spread-flagged segments")
    print(tables["all"].round(2).T.to_string())
    print(tables["by_area_type"].round(2).T.to_string())
    print(flag_counts.to_string())


if __name__ == "__main__":
    main()
