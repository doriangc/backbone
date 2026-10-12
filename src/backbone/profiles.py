"""Perpendicular width profiles along segments.

A profile is a straight line through a centreline point, perpendicular to the
segment, from t = -PROFILE_HALF_LENGTH_M (right) to +PROFILE_HALF_LENGTH_M
(left of the segment's direction). A surface source turns each profile into
*runs*: consecutive intervals (t0, t1, cls) covering the whole profile. All
width rules (first edge, offset search, cell stop) work on runs only, so the
same `measure` scores BGT polygons now (vector_runs) and a model's class
raster later (raster_runs).

Surface classes:
- CARRIAGEWAY: BGT drivable surface, except `inrit`.
- INRIT: driveway entrances. Drivable, but they lie beyond the kerb, so they
  stop width profiles (decided 2026-10-12).
- KERB_EXT: `parkeervlak` and non-drivable `fietspad`; kerb-to-kerb width
  continues through them.
- OTHER: everything else, including no polygon.
"""

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from backbone import config

OTHER, KERB_EXT, INRIT, CARRIAGEWAY = 0, 1, 2, 3
CLASS_NAMES = {OTHER: "other", KERB_EXT: "kerb_ext", INRIT: "inrit", CARRIAGEWAY: "carriageway"}
# Width definitions: classes a profile may walk through, starting on carriageway.
DEFINITIONS = {
    "carriageway": frozenset({CARRIAGEWAY}),
    "kerb": frozenset({CARRIAGEWAY, KERB_EXT}),
}


def build_surface(area_dir) -> gpd.GeoDataFrame:
    """BGT polygons with a profile class and level (relatieve_hoogteligging)."""
    wd = gpd.read_file(area_dir / "bgt_wegdeel.gpkg")
    cls = np.full(len(wd), OTHER)
    cls[wd["functie"].isin(["parkeervlak", "fietspad"]).to_numpy()] = KERB_EXT
    cls[wd["drivable"].to_numpy()] = CARRIAGEWAY
    cls[(wd["functie"] == "inrit").to_numpy()] = INRIT
    wd = gpd.GeoDataFrame({"cls": cls, "level": wd["relatieve_hoogteligging"]}, geometry=wd.geometry)
    terrain = gpd.read_file(area_dir / "bgt_onbegroeidterreindeel.gpkg")
    terrain = terrain[terrain["service_paved"]]
    terrain = gpd.GeoDataFrame({"cls": CARRIAGEWAY, "level": 0}, index=terrain.index, geometry=terrain.geometry)
    surf = pd.concat([wd[wd["cls"] != OTHER], terrain], ignore_index=True)
    return gpd.GeoDataFrame(surf, geometry="geometry", crs=config.RD_EPSG)


@dataclass
class Profiles:
    """Profiles of many segments, as parallel arrays."""

    seg: np.ndarray  # index label of the segment
    s: np.ndarray  # distance along the segment (m)
    point: np.ndarray  # (n, 2) centreline point
    normal: np.ndarray  # (n, 2) unit vector to the left of the segment direction
    half_length: float

    @property
    def lines(self) -> np.ndarray:
        a = self.point - self.normal * self.half_length
        b = self.point + self.normal * self.half_length
        return shapely.linestrings(np.stack([a, b], axis=1))

    def at(self, t: np.ndarray) -> np.ndarray:
        """Map coordinates of offsets t (left positive) along each profile."""
        return self.point + self.normal * np.asarray(t)[:, None]


def make_profiles(lines: gpd.GeoSeries, spacing: float, half_length: float) -> Profiles:
    """A profile every `spacing` m, centred within each line (so both ends get
    the same margin), with the normal from the local tangent over +-0.5 m."""
    seg, s_all, pts, nrm = [], [], [], []
    for idx, line in lines.items():
        n = int(line.length // spacing)
        if n < 1:
            s = np.array([line.length / 2])
        else:
            s = (line.length - (n - 1) * spacing) / 2 + spacing * np.arange(n)
        p = shapely.get_coordinates(shapely.line_interpolate_point(line, s))
        a = shapely.get_coordinates(shapely.line_interpolate_point(line, np.clip(s - 0.5, 0, None)))
        b = shapely.get_coordinates(shapely.line_interpolate_point(line, np.clip(s + 0.5, None, line.length)))
        tan = b - a
        tan /= np.linalg.norm(tan, axis=1, keepdims=True)
        seg.extend([idx] * len(s))
        s_all.append(s)
        pts.append(p)
        nrm.append(np.stack([-tan[:, 1], tan[:, 0]], axis=1))
    return Profiles(
        np.array(seg, dtype=object), np.concatenate(s_all), np.vstack(pts), np.vstack(nrm), half_length
    )


def _runs_from_pieces(pieces: list[tuple[float, float, int]], half_length: float) -> list[tuple]:
    """Elementary intervals over [-L, L]; where pieces overlap the highest class wins."""
    L = half_length
    cuts = sorted({-L, L, *[max(-L, min(L, v)) for p in pieces for v in p[:2]]})
    runs = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a <= 0:
            continue
        mid = (a + b) / 2
        cls = max((c for t0, t1, c in pieces if t0 <= mid <= t1), default=OTHER)
        if runs and runs[-1][2] == cls:
            runs[-1] = (runs[-1][0], b, cls)
        else:
            runs.append((a, b, cls))
    return runs


def vector_runs(profiles: Profiles, surface: gpd.GeoDataFrame, level: np.ndarray) -> list[list[tuple]]:
    """Runs for each profile from BGT polygons at the profile's level."""
    lines = profiles.lines
    L = profiles.half_length
    pi, si = surface.sindex.query(lines, predicate="intersects")
    keep = surface["level"].to_numpy()[si] == level[pi]
    pi, si = pi[keep], si[keep]
    inter = shapely.intersection(lines[pi], surface.geometry.to_numpy()[si])
    parts, owner = shapely.get_parts(inter, return_index=True)
    is_line = shapely.get_type_id(parts) == 1
    parts, owner = parts[is_line], owner[is_line]
    start = shapely.line_locate_point(lines[pi[owner]], shapely.get_point(parts, 0)) - L
    end = shapely.line_locate_point(lines[pi[owner]], shapely.get_point(parts, -1)) - L
    cls = surface["cls"].to_numpy()[si[owner]]
    pieces: list[list] = [[] for _ in range(len(lines))]
    for p, a, b, c in zip(pi[owner], np.minimum(start, end), np.maximum(start, end), cls):
        pieces[p].append((a, b, int(c)))
    return [_runs_from_pieces(pc, L) for pc in pieces]


def raster_runs(profiles: Profiles, labels: np.ndarray, transform, step: float) -> list[list[tuple]]:
    """Runs for each profile from a class raster (e.g. argmax of a model).

    Samples every `step` m; a run boundary lies halfway between samples.
    Pixels outside the raster are OTHER.
    """
    L = profiles.half_length
    t = np.arange(-L, L + 1e-9, step)
    inv = ~transform
    out = []
    for k in range(len(profiles.s)):
        xy = profiles.point[k] + profiles.normal[k] * t[:, None]
        col, row = inv @ (xy[:, 0], xy[:, 1])
        col, row = np.floor(col).astype(int), np.floor(row).astype(int)
        ok = (row >= 0) & (row < labels.shape[0]) & (col >= 0) & (col < labels.shape[1])
        cls = np.full(len(t), OTHER)
        cls[ok] = labels[row[ok], col[ok]]
        bounds = np.concatenate([[-L], (t[1:] + t[:-1]) / 2, [L]])
        runs = []
        for i, c in enumerate(cls):
            if runs and runs[-1][2] == c:
                runs[-1] = (runs[-1][0], bounds[i + 1], c)
            else:
                runs.append((bounds[i], bounds[i + 1], int(c)))
        out.append(runs)
    return out


def measure(runs: list[tuple], allowed: frozenset, search: float, gap_tol: float,
            stop: tuple[float, float] = (-np.inf, np.inf)) -> dict:  # fmt: skip
    """Left and right width of one profile under one definition.

    Starts on the CARRIAGEWAY run at t = 0, or the nearest one within `search` m
    (offset > 0 then; one side can be negative if the line is off the road).
    Walks outward through `allowed` classes and stops at the first other class
    (runs shorter than `gap_tol` are skipped, for polygon slivers) or at `stop`
    (the segment's cell). Returns NaN widths if no carriageway is found.
    """
    nan = {"left": np.nan, "right": np.nan, "offset": np.nan, "cell_stop": False}
    cw = [i for i, r in enumerate(runs) if r[2] == CARRIAGEWAY and r[1] - r[0] > gap_tol]
    if not cw:
        return nan
    dist = [0.0 if runs[i][0] <= 0 <= runs[i][1] else min(abs(runs[i][0]), abs(runs[i][1])) for i in cw]
    k = int(np.argmin(dist))
    if dist[k] > search:
        return nan
    i0 = cw[k]

    def walk(i, direction):
        j = i
        while 0 <= j + direction < len(runs):
            nxt = runs[j + direction]
            if nxt[2] in allowed or nxt[1] - nxt[0] <= gap_tol:
                j += direction
            else:
                break
        # Trailing slivers do not extend the edge.
        while j != i and runs[j][2] not in allowed:
            j -= direction
        return j

    lo = runs[walk(i0, -1)][0]
    hi = runs[walk(i0, +1)][1]
    cell_stop = lo < stop[0] or hi > stop[1]
    lo, hi = max(lo, stop[0]), min(hi, stop[1])
    return {"left": hi, "right": -lo, "offset": dist[k], "cell_stop": cell_stop}


def cell_interval(profiles: Profiles, cells: gpd.GeoSeries) -> np.ndarray:
    """(n, 2) interval of t around 0 that lies inside each profile's segment cell."""
    L = profiles.half_length
    lines = profiles.lines
    out = np.tile([-L, L], (len(lines), 1)).astype(float)
    geoms = cells.loc[profiles.seg].to_numpy()
    inter = shapely.intersection(lines, geoms)
    for k, g in enumerate(inter):
        if g.is_empty:
            out[k] = (0.0, 0.0)
            continue
        best = None
        for part in shapely.get_parts(g):
            if shapely.get_type_id(part) != 1:
                continue
            a = shapely.line_locate_point(lines[k], shapely.get_point(part, 0)) - L
            b = shapely.line_locate_point(lines[k], shapely.get_point(part, -1)) - L
            a, b = min(a, b), max(a, b)
            d = 0.0 if a <= 0 <= b else min(abs(a), abs(b))
            if best is None or d < best[0]:
                best = (d, a, b)
        if best is not None:
            out[k] = best[1:]
    return out


def junction_distances(segs: gpd.GeoDataFrame) -> pd.DataFrame:
    """Exclusion distance (m) at each segment's start and end node.

    At a node shared with other segments: half the tag width of the widest
    other segment + JUNCTION_MARGIN_M; if every other segment there is a minor
    service way (driveway etc.), MINOR_JUNCTION_M. Dead ends: 0.
    """
    ends = pd.concat(
        [
            pd.DataFrame({"seg": segs.index, "node": segs["start_node"], "end": "start"}),
            pd.DataFrame({"seg": segs.index, "node": segs["end_node"], "end": "end"}),
        ]
    )
    minor = (segs["highway"] == "service") & segs["service"].isin(config.MINOR_SERVICE)
    half = segs["tag_width"] / 2
    by_node = ends.groupby("node")["seg"].agg(lambda x: sorted(set(x)))
    dist = {}
    for _, r in ends.iterrows():
        others = [o for o in by_node[r["node"]] if o != r["seg"]]
        if not others:
            d = 0.0
        elif all(minor[o] for o in others):
            d = config.MINOR_JUNCTION_M
        else:
            d = max(half[o] for o in others) + config.JUNCTION_MARGIN_M
        dist[(r["seg"], r["end"])] = d
    return pd.DataFrame(
        {"start": [dist[(i, "start")] for i in segs.index], "end": [dist[(i, "end")] for i in segs.index]},
        index=segs.index,
    )
