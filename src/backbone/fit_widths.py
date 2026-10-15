"""Phase 5: segment widths from the Phase 4 probability maps, scored in metres.

    python -m backbone.fit_widths --run experiments/2026-10-14_full_r18 --tune     # validation areas only
    python -m backbone.fit_widths --run experiments/2026-10-14_full_r18            # val + test, all sources

Cross-section fit (main method). The profiles are exactly those of Phase 3
(`widths.area_profiles`: same spacing, junction exclusion and cell stop). The
model's 7 class probabilities are summed into the 5 profile classes
(background, footpath and other_paved -> OTHER) and sampled along each profile
every PROFILE_STEP_M. They are then averaged over SMOOTH_PROFILES neighbouring
profiles of the same segment (junction profiles do not contribute), so that a
blob on one profile is outvoted by its neighbours. The argmax class gives
runs, and `profiles.measure` applies the Phase 3 rules, with MIN_RUN_M as the
skip length: a foreign run shorter than that does not stop the walk. The
segment width is the median over usable profiles (`widths.summarize_segments`).

Render-and-score (validation method), on a sample of segments: every 25 cm
pixel of the segment's cell within PROFILE_HALF_LENGTH_M of the line (junction
zones removed) gets a signed offset t. For each side separately, the
half-width w maximises sum(log p) inside [0, w] plus sum(log(1 - p)) beyond it,
with p = P(carriageway). This is one constant width per side for the whole
segment, fitted to all pixels directly, with no runs and no medians.

Confidence per segment (higher = more trustworthy), from the share of usable
profiles, the p10-p90 spread of the profile widths, and the mean
P(carriageway) inside the fitted width.

Writes data/<area>/ml/widths_<run>_<source>.gpkg and reports/phase5/.
"""

import argparse
import itertools
import json
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from backbone import config
from backbone.areas import BBox
from backbone.fetch import DATA_DIR
from backbone.profiles import CARRIAGEWAY, CYCLE, INRIT, OTHER, PARKING, junction_distances
from backbone.qa import read_rgb
from backbone.surface_data import TO_PROFILE_CLASS, area_grid, areas_in, ml_dir
from backbone.widths import area_profiles, profile_rows, summarize_segments

log = logging.getLogger("backbone.fit_widths")
REPORT_DIR = Path("reports/phase5")
PROFILE_STEP_M = config.ML_RES_M / 2
N_PCLS = 5  # profile classes OTHER..CARRIAGEWAY
assert (OTHER, PARKING, CYCLE, INRIT, CARRIAGEWAY) == tuple(range(N_PCLS))

# Grid searched on the validation areas (8 cm source) with --tune.
SMOOTH_GRID = (1, 3, 5, 9)
MIN_RUN_GRID = (0.05, 0.25, 0.5, 1.0)
SOURCES = ("8cm", "8cm_x2", "8cm_x4", "25cm")
RENDER_SAMPLE = 300  # segments per split for render-and-score


# --- sampling -------------------------------------------------------------------


def sample_profiles(prof, prob: np.ndarray, transform) -> np.ndarray:
    """(n_profiles, n_t, 5) float32 profile-class probabilities, nearest pixel.
    Samples outside the raster are OTHER with probability 1."""
    L = prof.half_length
    t = np.arange(-L, L + 1e-9, PROFILE_STEP_M)
    xy = prof.point[:, None, :] + prof.normal[:, None, :] * t[None, :, None]
    inv = ~transform
    col, row = inv @ (xy[..., 0].ravel(), xy[..., 1].ravel())
    col, row = np.floor(col).astype(int), np.floor(row).astype(int)
    ok = (row >= 0) & (row < prob.shape[0]) & (col >= 0) & (col < prob.shape[1])
    # Model class -> profile class as a (7, 5) summing matrix.
    to_p = np.zeros((prob.shape[2], N_PCLS), np.float32)
    to_p[np.arange(prob.shape[2]), TO_PROFILE_CLASS] = 1.0
    out = np.zeros((row.size, N_PCLS), np.float32)
    out[:, OTHER] = 1.0
    for chunk in np.array_split(np.flatnonzero(ok), max(1, ok.sum() // 2_000_000)):
        out[chunk] = (prob[row[chunk], col[chunk]].astype(np.float32) / 255.0) @ to_p
    return out.reshape(len(prof.s), len(t), N_PCLS)


def smooth_along(p: np.ndarray, seg: np.ndarray, junction: np.ndarray, k: int) -> np.ndarray:
    """Mean over up to k consecutive profiles of the same segment, centred.
    Junction profiles are smoothed but do not contribute to their neighbours."""
    if k <= 1:
        return p
    out = np.empty_like(p)
    h = k // 2
    # Profiles of one segment are contiguous and ordered by s (make_profiles).
    bounds = np.flatnonzero(np.r_[True, seg[1:] != seg[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        w = (~junction[a:b]).astype(np.float32)
        cs = np.cumsum(np.concatenate([np.zeros((1, *p.shape[1:]), np.float32), p[a:b] * w[:, None, None]]), 0)
        cw = np.concatenate([[0.0], np.cumsum(w)])
        for i in range(b - a):
            lo, hi = max(0, i - h), min(b - a, i + h + 1)
            n = cw[hi] - cw[lo]
            out[a + i] = (cs[hi] - cs[lo]) / n if n > 0 else p[a + i]
    return out


def runs_from_classes(cls: np.ndarray, half_length: float, step: float) -> list[list[tuple]]:
    """Runs (t0, t1, cls) per profile from (n, n_t) class samples; a run
    boundary lies halfway between samples, as in profiles.raster_runs."""
    L = half_length
    n_t = cls.shape[1]
    t = -L + step * np.arange(n_t)
    bounds = np.concatenate([[-L], (t[1:] + t[:-1]) / 2, [L]])
    out = []
    for c in cls:
        starts = np.flatnonzero(np.r_[True, c[1:] != c[:-1]])
        ends = np.r_[starts[1:], n_t]
        out.append([(bounds[a], bounds[b], int(c[a])) for a, b in zip(starts, ends)])
    return out


def mean_p_inside(p_cw: np.ndarray, rows: pd.DataFrame, half_length: float) -> np.ndarray:
    """Mean P(carriageway) between -right and +left on each measured profile."""
    t = np.arange(-half_length, half_length + 1e-9, PROFILE_STEP_M)
    out = np.full(len(rows), np.nan)
    for i, (k, l, r) in enumerate(rows[["k", "carriageway_left", "carriageway_right"]].to_numpy()):
        if np.isfinite(l):
            m = (t >= -r) & (t <= l)
            out[i] = p_cw[int(k), m].mean() if m.any() else np.nan
    return out


# --- one area ----------------------------------------------------------------------


class AreaProfiles:
    """Profiles of one area plus their sampled probabilities per source."""

    def __init__(self, name: str, run_tag: str):
        self.name, self.run_tag = name, run_tag
        self.segs, self.cells, self.prof, self.inside, self.junction, self.stops = area_profiles(name)
        self.grid = area_grid(name)
        self._samples = {}

    def samples(self, source: str) -> np.ndarray:
        if source not in self._samples:
            prob = np.load(prob_path(self.name, self.run_tag, source), mmap_mode="r")
            self._samples = {source: sample_profiles(self.prof, prob, self.grid.transform)}  # one source in memory
        return self._samples[source]

    def fit(self, source: str, smooth: int, min_run: float):
        """Per-profile rows and per-segment summary for one source and setting."""
        p = smooth_along(self.samples(source), self.prof.seg, self.junction, smooth)
        runs = runs_from_classes(p.argmax(-1), self.prof.half_length, PROFILE_STEP_M)
        rows = profile_rows(self.prof, self.inside, self.junction, self.stops, runs, min_run)
        rows["segment_id"] = self.segs["segment_id"].loc[rows["seg"]].to_numpy()
        rows["p_in"] = mean_p_inside(p[..., CARRIAGEWAY], rows, self.prof.half_length)
        summ = summarize_segments(rows, self.segs, self.name)
        # summarize_segments emits one row per segment in groupby("seg") order.
        p_in = rows[rows["usable"]].groupby("seg")["p_in"].median()
        summ["p_in"] = p_in.reindex(rows.groupby("seg").size().index).to_numpy()
        return rows, summ


def prob_path(name: str, run_tag: str, source: str) -> Path:
    return ml_dir(name) / f"prob_{run_tag}_{source}.npy"


def ensure_probabilities(run: Path, names, sources):
    """Predict and cache any missing probability maps (the degraded sources
    are not saved by eval_surface)."""
    missing = [(n, s) for n in names for s in sources if not prob_path(n, run.name, s).exists()]
    if not missing:
        return
    import torch

    from backbone.eval_surface import load_model, predict_area, source_image

    torch.set_num_threads(12)
    model = load_model(run / "best.pt")
    for n, s in missing:
        log.info("predicting %s %s", n, s)
        np.save(prob_path(n, run.name, s), predict_area(model, source_image(n, s)))


# --- render-and-score -------------------------------------------------------------


def render_and_score(ap: AreaProfiles, prob: np.ndarray, seg_ids, jd_profiles: pd.DataFrame) -> pd.DataFrame:
    """Constant left/right half-width per segment fitted to all corridor pixels."""
    g = ap.grid
    L = config.PROFILE_HALF_LENGTH_M
    cw = config.SURFACE_CLASSES.index("carriageway")
    out = []
    for seg in seg_ids:
        line = ap.segs.geometry[seg]
        js, je = jd_profiles.loc[seg]
        region = shapely.intersection(line.buffer(L, cap_style="flat"), ap.cells[seg])
        if region.is_empty or line.length <= js + je:
            continue
        x0, y0, x1, y1 = region.bounds
        c0, r0 = (~g.transform) @ (x0, y1)
        c1, r1 = (~g.transform) @ (x1, y0)
        c0, r0 = max(0, int(c0)), max(0, int(r0))
        c1, r1 = min(prob.shape[1], int(np.ceil(c1))), min(prob.shape[0], int(np.ceil(r1)))
        if c1 <= c0 or r1 <= r0:
            continue
        cc, rr = np.meshgrid(np.arange(c0, c1) + 0.5, np.arange(r0, r1) + 0.5)
        x, y = g.transform @ (cc.ravel(), rr.ravel())
        keep = shapely.contains_xy(region, x, y)
        x, y = x[keep], y[keep]
        pts = shapely.points(x, y)
        s = shapely.line_locate_point(line, pts)
        mid = (s > js) & (s < line.length - je)
        x, y, s = x[mid], y[mid], s[mid]
        if len(s) < 50:
            continue
        foot = shapely.get_coordinates(shapely.line_interpolate_point(line, s))
        a = shapely.get_coordinates(shapely.line_interpolate_point(line, np.clip(s - 0.5, 0, None)))
        b = shapely.get_coordinates(shapely.line_interpolate_point(line, np.clip(s + 0.5, None, line.length)))
        tan = b - a
        cross = tan[:, 0] * (y - foot[:, 1]) - tan[:, 1] * (x - foot[:, 0])  # > 0: left
        dist = np.hypot(x - foot[:, 0], y - foot[:, 1])
        col, row = (~g.transform) @ (x, y)
        p = np.clip(prob[row.astype(int), col.astype(int), cw] / 255.0, 0.01, 0.99)
        logit = np.log(p) - np.log1p(-p)
        width = {}
        for side, m in (("left", cross > 0), ("right", cross <= 0)):
            d, lg = dist[m], logit[m]
            o = np.argsort(d)
            cs = np.cumsum(lg[o])
            width[side] = float(d[o][np.argmax(cs)]) if len(cs) and cs.max() > 0 else 0.0
        out.append({"seg": seg, "render_left": width["left"], "render_right": width["right"]})
    return pd.DataFrame(out).set_index("seg") if out else pd.DataFrame(columns=["render_left", "render_right"])


# --- evaluation --------------------------------------------------------------------


def truth_table() -> pd.DataFrame:
    """Phase 3 per-segment truth and baselines (B0 = Phase 2 tag width, B2)."""
    t = pd.read_csv("reports/phase3/segments.csv")
    t = t[t["scored"]]
    cols = ["area", "segment_id", "carriageway_total", "kerb_total", "tag_width", "b1_width", "b2_width",
            "flags", "length", "highway", "area_type", "width_source"]  # fmt: skip
    return t[cols].rename(columns={"carriageway_total": "true_cw", "kerb_total": "true_kerb", "flags": "true_flags"})


def confidence(s: pd.DataFrame) -> pd.Series:
    """Heuristic confidence in [0, 1]; fixed before looking at test errors."""
    spread = s["carriageway_p90"] - s["carriageway_p10"]
    return (s["share_usable"].fillna(0) * s["p_in"].fillna(0) / (1 + spread.fillna(10) / 2)).clip(0, 1)


def joined(summ: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    keep = ["area", "segment_id", "carriageway_total", "kerb_total", "carriageway_left", "carriageway_right",
            "carriageway_p10", "carriageway_p90", "share_usable", "p_in", "scored", "flags", "n_usable"]  # fmt: skip
    m = truth.merge(summ[keep].rename(columns={"scored": "pred_scored", "flags": "pred_flags"}),
                    on=["area", "segment_id"], how="left")  # fmt: skip
    m["pred_scored"] = m["pred_scored"].fillna(False).astype(bool)
    m["conf"] = confidence(m).where(m["pred_scored"], 0.0)
    m["err_cw"] = m["carriageway_total"] - m["true_cw"]
    m["err_kerb"] = m["kerb_total"] - m["true_kerb"]
    # Where the image gives no width, fall back to B2.
    m["final_cw"] = m["carriageway_total"].where(m["pred_scored"], m["b2_width"])
    m["err_final"] = m["final_cw"] - m["true_cw"]
    m["err_b2"] = m["b2_width"] - m["true_cw"]
    m["err_b0"] = m["tag_width"] - m["true_cw"]
    m["err_b2_kerb"] = m["b2_width"] - m["true_kerb"]
    return m


def err_stats(e: pd.Series, prefix: str) -> dict:
    e = e.dropna()
    return {f"{prefix}_mae": e.abs().mean(), f"{prefix}_within_0.5": (e.abs() <= 0.5).mean(),
            f"{prefix}_bias": e.median()}  # fmt: skip


def error_table(m: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    def agg(g):
        cov = g[g["pred_scored"]]
        r = {"segments": len(g), "km": g["length"].sum() / 1000, "coverage": len(cov) / len(g)}
        r |= err_stats(cov["err_cw"], "image_cw")
        r |= err_stats(cov["err_b2"], "b2_same_cw")
        r |= err_stats(g["err_final"], "image+b2_cw")
        r |= err_stats(g["err_b2"], "b2_cw")
        r |= err_stats(g["err_b0"], "b0_cw")
        r |= err_stats(cov["err_kerb"], "image_kerb")
        r |= err_stats(cov["err_b2_kerb"], "b2_same_kerb")
        return pd.Series(r)

    return m.groupby(by).apply(agg, include_groups=False) if by else agg(m).to_frame("all").T


# --- gallery ---------------------------------------------------------------------


def render_worst(m: pd.DataFrame, rows_by_area: dict, run_tag: str, n: int = 50, per_page: int = 10):
    """The n test segments with the largest |carriageway error| (image scored)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for old in REPORT_DIR.glob("worst_*.jpg"):
        old.unlink()
    worst = m[m["pred_scored"]].assign(abs_err=lambda d: d["err_cw"].abs()).nlargest(n, "abs_err")
    truth_prof = {a: gpd.read_file(DATA_DIR / a / "widths.gpkg", layer="profiles") for a in worst["area"].unique()}
    seg_geom = {a: gpd.read_file(ml_dir(a) / f"widths_{run_tag}_8cm.gpkg", layer="segments").set_index("segment_id").geometry
                for a in worst["area"].unique()}  # fmt: skip
    cw = config.SURFACE_CLASSES.index("carriageway")
    for page in range(0, len(worst), per_page):
        chunk = worst.iloc[page : page + per_page]
        fig, axes = plt.subplots(2, 5, figsize=(25, 10.8))
        for ax, (_, r) in zip(axes.flat, chunk.iterrows()):
            area, g = r["area"], seg_geom[r["area"]][r["segment_id"]]
            cx, cy = g.centroid.x, g.centroid.y
            half = min(max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) / 2 + 12, 90)
            img, (l, b, rr, t) = read_rgb(DATA_DIR / area / "luchtfoto_8cm.tif", BBox(cx - half, cy - half, cx + half, cy + half), max_px=800)
            ax.imshow(img, extent=(l, rr, b, t))
            grid = area_grid(area)
            c0, r0 = (~grid.transform) @ (l, t)
            c1, r1 = (~grid.transform) @ (rr, b)
            prob = np.load(prob_path(area, run_tag, "8cm"), mmap_mode="r")
            r0, c0 = max(0, int(r0)), max(0, int(c0))
            crop = np.asarray(prob[r0 : int(np.ceil(r1)), c0 : int(np.ceil(c1)), cw]) / 255.0
            x0, y0 = grid.transform @ (c0, r0)
            x1, y1 = grid.transform @ (c0 + crop.shape[1], r0 + crop.shape[0])
            rgba = np.zeros((*crop.shape, 4))
            rgba[..., :3] = (1.0, 0.69, 0.0)
            rgba[..., 3] = crop * 0.45
            ax.imshow(rgba, extent=(x0, x1, y1, y0))
            gpd.GeoSeries([g]).plot(ax=ax, color="#00e5ff", lw=1.5)
            for prof, colour, lw, off in ((truth_prof[area], "#3ddc84", 1.6, -0.4), (rows_by_area[area], "#ff3df5", 1.0, 0.4)):
                q = prof[(prof["segment_id"] == r["segment_id"]) & prof["usable"]]
                for _, p in q.iterrows():
                    nrm = np.array([p["nx"], p["ny"]])
                    along = np.array([nrm[1], -nrm[0]]) * off
                    xy = np.array([p.geometry.x, p.geometry.y]) + along
                    ax.plot(*np.array([xy - nrm * p["carriageway_right"], xy + nrm * p["carriageway_left"]]).T, color=colour, lw=lw)
            ax.set_xlim(l, rr)
            ax.set_ylim(b, t)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(f"{r['highway']} | true {r['true_cw']:.1f} | image {r['carriageway_total']:.1f} | B2 {r['b2_width']:.1f} | "
                         f"conf {r['conf']:.2f}\n{area} {r['segment_id'][:30]} | {r['length']:.0f} m | truth flags: {r['true_flags'] if isinstance(r['true_flags'], str) else 'none'}",
                         fontsize=8.5)  # fmt: skip
        for ax in axes.flat[len(chunk):]:
            ax.axis("off")
        fig.suptitle("Worst test segments by |image - true| carriageway width (8 cm source). Orange: model P(carriageway); "
                     "green: BGT truth width per profile; magenta: image width per profile; cyan: OSM segment", fontsize=10)  # fmt: skip
        fig.tight_layout()
        fig.savefig(REPORT_DIR / f"worst_{page // per_page + 1:02d}.jpg", dpi=80, pil_kwargs={"quality": 85})
        plt.close(fig)


def gated_error(m: pd.DataFrame, threshold: float) -> pd.Series:
    """Image width where its confidence >= threshold and the segment has no
    `width` tag (tagged widths are hard to beat), else B2."""
    use = m["pred_scored"] & (m["conf"] >= threshold) & (m["width_source"] != "width")
    return pd.Series(np.where(use, m["carriageway_total"], m["b2_width"]), index=m.index) - m["true_cw"]


def gate_table(merged: dict, splits) -> tuple[float, pd.DataFrame]:
    """Pick the gate threshold on the validation areas (8cm), apply it everywhere."""
    val = merged[("val", "8cm")]
    cands = np.quantile(val.loc[val["pred_scored"], "conf"], np.linspace(0, 0.9, 19))
    thr = float(min(cands, key=lambda c: gated_error(val, c).abs().mean()))
    rows = {}
    for (split, source), m in merged.items():
        e = gated_error(m, thr)
        use = (e != m["b2_width"] - m["true_cw"]).mean()
        rows[(split, source)] = {"segments": len(m), "share_image": use, **err_stats(e, "gated_cw"),
                                 **err_stats(m["err_b2"], "b2_cw")}  # fmt: skip
    return thr, pd.DataFrame(rows).T


# --- main -----------------------------------------------------------------------------


def tune(run: Path, truth: pd.DataFrame):
    names = areas_in("val")
    ensure_probabilities(run, names, ["8cm"])
    aps = [AreaProfiles(n, run.name) for n in names]
    rows = []
    for smooth, min_run in itertools.product(SMOOTH_GRID, MIN_RUN_GRID):
        summ = pd.concat([ap.fit("8cm", smooth, min_run)[1] for ap in aps], ignore_index=True)
        m = joined(summ, truth[truth["area"].isin(names)])
        cov = m[m["pred_scored"]]
        r = {"smooth": smooth, "min_run": min_run, "coverage": m["pred_scored"].mean()}
        r |= err_stats(cov["err_cw"], "cw") | err_stats(m["err_final"], "final") | err_stats(cov["err_kerb"], "kerb")
        log.info("tune %s", {k: round(v, 3) for k, v in r.items()})
        rows.append(r)
    t = pd.DataFrame(rows)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "tuning_val.md").write_text(
        "# Phase 5 tuning on the validation areas (8 cm source)\n\n"
        "Grid over profile smoothing (profiles averaged) and minimum run length (m). "
        "`final` = image width with B2 where the image gives none.\n\n" + t.round(3).to_markdown(index=False) + "\n"
    )
    print(t.round(3).to_string(index=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--tune", action="store_true", help="grid search on the validation areas, then stop")
    ap.add_argument("--smooth", type=int, default=None)
    ap.add_argument("--min-run", type=float, default=None)
    ap.add_argument("--split", action="append", choices=["val", "test"])
    ap.add_argument("--source", action="append", choices=SOURCES)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    truth = truth_table()
    if args.tune:
        return tune(args.run, truth)
    if args.smooth is None or args.min_run is None:
        ap.error("--smooth and --min-run are required (pick them with --tune on the validation areas)")

    splits = args.split or ["val", "test"]
    sources = args.source or list(SOURCES)
    names = [n for sp in splits for n in areas_in(sp)]
    ensure_probabilities(args.run, names, sources)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)

    results, renders, profiles_8cm = [], [], {}
    for n in names:
        a = AreaProfiles(n, args.run.name)
        split = "val" if n in config.VAL_AREAS else "test"
        for source in sources:
            rows, summ = a.fit(source, args.smooth, args.min_run)
            summ["split"], summ["source"] = split, source
            out = ml_dir(n) / f"widths_{args.run.name}_{source}.gpkg"
            out.unlink(missing_ok=True)
            rows.drop(columns="seg").to_file(out, layer="profiles", driver="GPKG")
            summ.to_file(out, layer="segments", driver="GPKG")
            results.append(summ.drop(columns="geometry"))
            if source == "8cm":
                profiles_8cm[n] = rows
                scored = summ[summ["scored"]]
                pick = scored.iloc[rng.choice(len(scored), size=min(RENDER_SAMPLE // 3 + 1, len(scored)), replace=False)]
                seg_idx = a.segs.index[a.segs["segment_id"].isin(pick["segment_id"])]
                jd = junction_distances(a.segs)
                prob = np.load(prob_path(n, args.run.name, "8cm"), mmap_mode="r")
                r = render_and_score(a, prob, seg_idx, jd)
                r["segment_id"] = a.segs.loc[r.index, "segment_id"].to_numpy()
                r["area"], r["split"] = n, split
                renders.append(r.reset_index(drop=True))
            log.info("%s %s: %d segments scored", n, source, int(summ["scored"].sum()))

    allres = pd.concat(results, ignore_index=True)
    tables, merged = {}, {}
    for (split, source), g in allres.groupby(["split", "source"]):
        merged[(split, source)] = joined(g, truth[truth["area"].isin(g["area"].unique())])
    by_source = pd.concat({k: error_table(v, []).iloc[0] for k, v in merged.items()}, axis=1).T
    tables["by_split_source"] = by_source
    for split in splits:
        m = merged[(split, "8cm")]
        tables[f"{split}_8cm_by_area_type"] = error_table(m, ["area_type"])
        tables[f"{split}_8cm_by_highway"] = error_table(m, ["highway"])
        tables[f"{split}_8cm_by_area"] = error_table(m, ["area"])
        tables[f"{split}_8cm_by_width_source"] = error_table(m, ["width_source"])
        cov = m[m["pred_scored"]].copy()
        cov["conf_bin"] = pd.qcut(cov["conf"], 5, labels=["q1 (low)", "q2", "q3", "q4", "q5 (high)"])
        tables[f"{split}_8cm_by_confidence"] = cov.groupby("conf_bin", observed=True).apply(
            lambda g: pd.Series({"segments": len(g), "conf_min": g["conf"].min(), **err_stats(g["err_cw"], "image_cw"),
                                 **err_stats(g["err_b2"], "b2_cw")}), include_groups=False)  # fmt: skip
        tables[f"{split}_8cm_by_flag"] = error_table(
            m.assign(true_spread=m["true_flags"].fillna("").str.contains("spread")), ["true_spread"]
        )

    if "val" in splits:
        thr, gt = gate_table(merged, splits)
        tables[f"gated_image_or_b2 (threshold {thr:.3f}, chosen on val 8cm)"] = gt
    rend = pd.concat(renders, ignore_index=True)
    rm = pd.concat([rend.merge(merged[(sp, "8cm")], on=["area", "segment_id"], how="inner") for sp in splits])
    rm["render_total"] = rm["render_left"] + rm["render_right"]
    rm["err_render"] = rm["render_total"] - rm["true_cw"]
    rcov = rm[rm["pred_scored"]]
    tables["render_vs_cross_section"] = pd.DataFrame(
        {
            sp: {"segments": len(g), **err_stats(g["err_render"], "render"), **err_stats(g["err_cw"], "cross"),
                 "median_abs_render_minus_cross": (g["render_total"] - g["carriageway_total"]).abs().median(),
                 "corr_render_cross": g[["render_total", "carriageway_total"]].corr().iloc[0, 1]}  # fmt: skip
            for sp, g in rcov.groupby("split")
        }
    ).T
    from scipy.stats import spearmanr

    conf_rho = {sp: spearmanr(merged[(sp, "8cm")].query("pred_scored")["conf"],
                              merged[(sp, "8cm")].query("pred_scored")["err_cw"].abs()).statistic for sp in splits}  # fmt: skip

    lines = ["# Phase 5 metrics (generated by backbone.fit_widths)", "",
             f"Run `{args.run}`, smoothing {args.smooth} profiles, minimum run {args.min_run} m. "
             "Errors = predicted - true total carriageway width (m), on Phase 3 scored segments; MAE is a mean, "
             "bias a median. `image_*` and `b2_same_*`: segments where the image gives a width (coverage). "
             "`image+b2`: image width, B2 where none. `b2`, `b0`: all segments.", "",
             f"Spearman correlation of confidence with |error| (8cm): {json.dumps({k: round(v, 3) for k, v in conf_rho.items()})}", ""]  # fmt: skip
    for k, t in tables.items():
        lines += [f"## {k}", "", t.round(3).to_markdown(), ""]
    (REPORT_DIR / "metrics.md").write_text("\n".join(lines))
    js = {k: json.loads(t.round(4).set_axis(t.index.map(str)).to_json(orient="index")) for k, t in tables.items()}
    (REPORT_DIR / "metrics.json").write_text(json.dumps({"confidence_spearman": conf_rho, **js}, indent=1))
    allm = pd.concat([v.assign(split=k[0], source=k[1]) for k, v in merged.items()], ignore_index=True)
    allm.to_csv(REPORT_DIR / "segments.csv", index=False, float_format="%.3f")
    rm.to_csv(REPORT_DIR / "render_sample.csv", index=False, float_format="%.3f")
    if "test" in splits:
        render_worst(merged[("test", "8cm")], profiles_8cm, args.run.name)
    print(tables["by_split_source"].round(2).T.to_string())
    log.info("wrote %s", REPORT_DIR)


if __name__ == "__main__":
    main()
