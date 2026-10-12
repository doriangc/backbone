"""Phase 2: tag-based width baseline, scored against BGT drivable surface.

    python -m backbone.baseline --area utrecht_centrum --area houten --area benschop

For each segment, the prediction is the centreline buffered by half the tag
width (flat ends, assumed centred). The reference is the BGT drivable surface
(wegdeel drivable classes plus paved terrain under service ways, at or above
ground level) inside a corridor of CORRIDOR_HALF_WIDTH_M around the segment,
restricted to the segment's nearest-centreline cell so that parallel and
crossing roads are not counted against it. Prediction and reference are both
clipped to that cell. Only the part of each segment inside the area box shrunk
by EVAL_MARGIN_M is scored, because BGT is cut off at the box.

Writes data/<area>/baseline_segments.gpkg (per segment) and reports/phase2/.
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
from shapely.geometry import MultiPoint, box  # noqa: E402

from backbone import config  # noqa: E402
from backbone.areas import AREAS, BBox  # noqa: E402
from backbone.fetch import DATA_DIR  # noqa: E402
from backbone.qa import read_rgb  # noqa: E402
from backbone.segments import split_ways, tag_width  # noqa: E402

log = logging.getLogger("backbone.baseline")

REPORTS_DIR = Path("reports/phase2")
MIN_REF_WIDTH_M = 1.5  # below this the segment's cell holds no real carriageway


def drivable_surface(area_dir: Path) -> gpd.GeoDataFrame:
    """BGT drivable polygons (one row each) at or above ground level."""
    wd = gpd.read_file(area_dir / "bgt_wegdeel.gpkg")
    wd = wd[wd["drivable"] & (wd["relatieve_hoogteligging"] >= 0)]
    terrain = gpd.read_file(area_dir / "bgt_onbegroeidterreindeel.gpkg")
    terrain = terrain[terrain["service_paved"]]
    return gpd.GeoDataFrame(
        pd.concat([wd[["geometry"]], terrain[["geometry"]]], ignore_index=True),
        geometry="geometry",
        crs=config.RD_EPSG,
    )


def nearest_centreline_cells(lines: gpd.GeoSeries, extent, spacing: float) -> gpd.GeoSeries:
    """Approximate the region closer to each line than to any other line, by a
    Voronoi diagram of points sampled every `spacing` m along all lines."""
    pts, labels = [], []
    for idx, line in lines.items():
        coords = shapely.get_coordinates(shapely.segmentize(line, spacing))
        # The midpoint guarantees a point of its own to short lines whose
        # endpoints are junction nodes shared with (and claimed by) other lines.
        mid = shapely.get_coordinates(line.interpolate(0.5, normalized=True))
        coords = np.vstack([mid, coords])
        pts.append(coords)
        labels.append(np.full(len(coords), idx, dtype=object))
    pts = np.vstack(pts)
    labels = np.concatenate(labels)
    # Shared junction nodes appear in several lines; keep one copy of each point.
    _, first = np.unique(np.round(pts, 2), axis=0, return_index=True)
    first.sort()
    pts, labels = pts[first], labels[first]
    vor = shapely.voronoi_polygons(MultiPoint(pts), extend_to=extent, ordered=True)
    # Element-wise intersection keeps one row per point, so labels stay aligned
    # (GeoSeries.clip may drop rows).
    polys = shapely.intersection(np.array(list(vor.geoms), dtype=object), extent)
    cells = pd.Series(polys).groupby(labels).agg(lambda g: shapely.union_all(g.values))
    return gpd.GeoSeries(cells, crs=lines.crs).reindex(lines.index)


def score(line, width: float, cell, surface: gpd.GeoDataFrame) -> dict:
    """IoU of the buffered line against the reference surface, both within `cell`."""
    corridor = line.buffer(config.CORRIDOR_HALF_WIDTH_M, cap_style="flat").intersection(cell)
    cand = surface.iloc[surface.sindex.query(corridor, predicate="intersects")]
    ref = shapely.union_all(cand.geometry.values).intersection(corridor) if len(cand) else shapely.Polygon()
    pred = line.buffer(width / 2, cap_style="flat").intersection(cell)
    inter = pred.intersection(ref).area
    union = pred.union(ref).area
    return {
        "iou": inter / union if union > 0 else np.nan,
        "ref_area": ref.area,
        "pred_area": pred.area,
        # Rough mean reference width; includes junction wedges inside the cell.
        "ref_width": ref.area / line.length,
        "ref": ref,
        "pred": pred,
    }


def load_segments(name: str) -> gpd.GeoDataFrame:
    """Segments of an area's kept OSM ways, with tag widths."""
    ways = gpd.read_file(DATA_DIR / name / "osm_drivable.gpkg", layer="ways")
    segs = split_ways(ways)
    tw = segs["tags"].map(json.loads).map(tag_width)
    segs["tag_width"] = tw.map(lambda t: t[0])
    segs["width_source"] = tw.map(lambda t: t[1])
    log.info("%s: %d ways -> %d segments", name, len(ways), len(segs))
    return segs


def segment_cells(name: str, segs: gpd.GeoDataFrame) -> gpd.GeoSeries:
    """Nearest-centreline cell of each segment (indexed like segs).

    Excluded ways (parking aisles, hidden roads, private yards) are not scored,
    but they still claim the surface nearest to them, so e.g. parking-lot
    aisles are not counted against the street next to the lot.
    """
    bb = AREAS[name].bbox
    extent = box(*bb.as_tuple()).buffer(500, join_style="mitre")
    excluded = gpd.read_file(DATA_DIR / name / "osm_drivable.gpkg", layer="excluded")
    claimers = pd.concat(
        [segs.geometry, gpd.GeoSeries(excluded.geometry.values, index=[f"excluded:{i}" for i in range(len(excluded))])]
    )
    cells = nearest_centreline_cells(gpd.GeoSeries(claimers, crs=segs.crs), extent, config.VORONOI_SPACING_M)
    return cells.loc[segs.index]


def eval_box(name: str):
    """Area box shrunk by EVAL_MARGIN_M; BGT is cut off at the area box."""
    return box(*AREAS[name].bbox.as_tuple()).buffer(-config.EVAL_MARGIN_M, join_style="mitre")


def run_area(name: str) -> gpd.GeoDataFrame:
    area = AREAS[name]
    area_dir = DATA_DIR / name
    segs = load_segments(name)
    cells = segment_cells(name, segs)
    surface = drivable_surface(area_dir)
    eval_box_ = eval_box(name)

    rows, shapes = [], []
    for i, seg in segs.iterrows():
        part = seg.geometry.intersection(eval_box_)
        if part.is_empty or part.length < 1.0:
            continue
        parts = list(part.geoms) if part.geom_type == "MultiLineString" else [part]
        for k, line in enumerate(parts):
            if line.length < 1.0:
                continue
            r = score(line, seg["tag_width"], cells[i], surface)
            shapes.append({"ref": r.pop("ref"), "pred": r.pop("pred"), "cell": cells[i]})
            rows.append(
                {
                    "segment_id": seg["segment_id"] + (f"/{k}" if len(parts) > 1 else ""),
                    "osm_id": seg["osm_id"],
                    "area": name,
                    "area_type": area.area_type,
                    "highway": seg["highway"],
                    "width_source": seg["width_source"],
                    "tag_width": seg["tag_width"],
                    "length": line.length,
                    "clipped": line.length < seg.geometry.length - 0.01,
                    **r,
                    "geometry": line,
                }
            )
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=config.RD_EPSG)
    out["width_error"] = out["tag_width"] - out["ref_width"]
    path = area_dir / "baseline_segments.gpkg"
    path.unlink(missing_ok=True)
    out.to_file(path, layer="segments", driver="GPKG")
    for key in ("ref", "pred", "cell"):
        gpd.GeoDataFrame(
            {"segment_id": out["segment_id"]}, geometry=[s[key] for s in shapes], crs=config.RD_EPSG
        ).to_file(path, layer=key, driver="GPKG")
    return out


def summarize(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    def agg(g):
        w = g["length"]
        ok = g["iou"].notna()
        return pd.Series(
            {
                "segments": len(g),
                "km": w.sum() / 1000,
                "iou_lenweighted": np.average(g.loc[ok, "iou"], weights=w[ok]) if ok.any() else np.nan,
                "iou_median": g["iou"].median(),
                "share_iou_below_0.5": (g["iou"] < 0.5).mean(),
                "tag_width_median": g["tag_width"].median(),
                "ref_width_median": g["ref_width"].median(),
                "width_mae": g["width_error"].abs().median(),
                "width_bias": g["width_error"].median(),
            }
        )

    return df.groupby(by).apply(agg, include_groups=False)


def render_gallery(worst: gpd.GeoDataFrame, out_dir: Path, prefix: str, title: str, per_page: int = 10):
    layers = {}
    for area in worst["area"].unique():
        p = DATA_DIR / area / "baseline_segments.gpkg"
        layers[area] = {k: gpd.read_file(p, layer=k).set_index("segment_id") for k in ("ref", "pred", "cell")}
    for page in range(0, len(worst), per_page):
        chunk = worst.iloc[page : page + per_page]
        fig, axes = plt.subplots(2, 5, figsize=(25, 10.5))
        for ax, (_, r) in zip(axes.flat, chunk.iterrows()):
            g = r.geometry
            cx, cy = g.centroid.x, g.centroid.y
            half = max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) / 2 + 15
            bb = BBox(cx - half, cy - half, cx + half, cy + half)
            img, (l, b, rr, t) = read_rgb(DATA_DIR / r["area"] / "luchtfoto_8cm.tif", bb, max_px=800)
            ax.imshow(img, extent=(l, rr, b, t))
            L = layers[r["area"]]
            sid = r["segment_id"]
            gpd.GeoSeries([L["cell"].geometry[sid]]).boundary.plot(ax=ax, color="white", lw=0.6, ls=":")
            if not L["ref"].geometry[sid].is_empty:
                gpd.GeoSeries([L["ref"].geometry[sid]]).plot(ax=ax, color="#ffb000", alpha=0.45)
            gpd.GeoSeries([L["pred"].geometry[sid]]).boundary.plot(ax=ax, color="#ff2bd6", lw=1.5)
            gpd.GeoSeries([g]).plot(ax=ax, color="#00e5ff", lw=2)
            ax.set_xlim(l, rr)
            ax.set_ylim(b, t)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(
                f"IoU {r['iou']:.2f} | {r['highway']} | {r['width_source']} {r['tag_width']:.1f} m"
                f" vs ref {r['ref_width']:.1f} m\n{r['area']} {sid[:32]} | {r['length']:.0f} m",
                fontsize=9,
            )
        for ax in axes.flat[len(chunk):]:
            ax.axis("off")
        fig.suptitle(
            f"{title}. Orange: BGT reference in the segment's cell; magenta: "
            "tag-width buffer; cyan: centreline; dotted: nearest-centreline cell",
            fontsize=11,
        )
        fig.tight_layout()
        fig.savefig(out_dir / f"{prefix}_{page // per_page + 1:02d}.jpg", dpi=80, pil_kwargs={"quality": 85})
        plt.close(fig)
    return worst


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", action="append", choices=sorted(AREAS), required=True)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.concat([run_area(a) for a in args.area], ignore_index=True)
    df = gpd.GeoDataFrame(df, geometry="geometry", crs=config.RD_EPSG)

    # Segments with (almost) no BGT drivable surface in their cell are a
    # reference problem, not a width problem; they are reported separately.
    df["has_reference"] = df["ref_width"] >= MIN_REF_WIDTH_M
    scored = df[df["has_reference"]]
    noref = df[~df["has_reference"]]
    tables = {
        "by_area_type": summarize(scored, ["area_type"]),
        "by_highway": summarize(scored, ["highway"]),
        "by_area_type_highway": summarize(scored, ["area_type", "highway"]),
        "by_width_source": summarize(scored, ["width_source"]),
        "by_width_source_highway": summarize(scored, ["width_source", "highway"]),
        "no_reference_by_highway": noref.groupby(["area_type", "highway"]).agg(
            segments=("length", "size"), km=("length", lambda x: x.sum() / 1000)
        ),
    }
    with open(REPORTS_DIR / "metrics.md", "w") as f:
        f.write("# Phase 2 baseline metrics (generated by backbone.baseline)\n\n")
        f.write(
            f"Areas: {', '.join(args.area)}. Segments: {len(df)} ({df['length'].sum() / 1000:.1f} km); "
            f"with a usable reference (ref width >= {MIN_REF_WIDTH_M} m): {len(scored)} "
            f"({scored['length'].sum() / 1000:.1f} km). Tables except no_reference use those.\n"
        )
        f.write("Widths in m. width_mae and width_bias are medians of |tag - ref| and tag - ref, ")
        f.write("where ref = reference area / scored length (rough; includes junction wedges).\n\n")
        for name, t in tables.items():
            f.write(f"## {name}\n\n{t.round(2).to_markdown()}\n\n")
    df.drop(columns="geometry").to_csv(REPORTS_DIR / "segments.csv", index=False, float_format="%.3f")
    for old in REPORTS_DIR.glob("*.jpg"):
        old.unlink()
    long_enough = scored[scored["length"] >= config.GALLERY_MIN_LENGTH_M]
    worst = long_enough.nsmallest(50, "iou")
    render_gallery(worst, REPORTS_DIR, "worst", "50 worst baseline segments (usable reference, >= 10 m)")
    worst.drop(columns="geometry").to_csv(REPORTS_DIR / "worst50.csv", index=False, float_format="%.3f")
    noref_long = noref.nlargest(20, "length")
    render_gallery(noref_long, REPORTS_DIR, "noref", "20 longest segments without usable BGT reference")
    noref.drop(columns="geometry").to_csv(REPORTS_DIR / "no_reference.csv", index=False, float_format="%.3f")
    print(f"no usable reference: {len(noref)} segments, {noref['length'].sum() / 1000:.1f} km")
    print(tables["by_area_type"].round(2).to_string())
    print(tables["by_width_source"].round(2).to_string())


if __name__ == "__main__":
    main()
