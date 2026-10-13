import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from backbone.profiles import (
    CARRIAGEWAY,
    DEFINITIONS,
    INRIT,
    CYCLE,
    PARKING,
    cell_interval,
    junction_distances,
    make_profiles,
    measure,
    raster_runs,
    vector_runs,
)

L = 25.0


def _surface(*polys):
    """polys: (geometry, cls[, level])"""
    return gpd.GeoDataFrame(
        {"cls": [p[1] for p in polys], "level": [p[2] if len(p) > 2 else 0 for p in polys]},
        geometry=[p[0] for p in polys],
        crs=28992,
    )


def _measure_all(lines, surface, definition="carriageway", **kw):
    prof = make_profiles(gpd.GeoSeries(lines), 2.0, L)
    runs = vector_runs(prof, surface, np.zeros(len(prof.s), dtype=int))
    return prof, [measure(r, DEFINITIONS[definition], search=5.0, gap_tol=0.05, **kw) for r in runs]


def test_straight_road_left_right():
    # Road from y = -2 to y = 4 (6 m), centreline along y = 0 heading east:
    # left (north) 4 m, right (south) 2 m.
    surf = _surface((box(-100, -2, 200, 4), CARRIAGEWAY))
    prof, res = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf)
    assert len(res) == 50
    assert all(r["left"] == pytest.approx(4) and r["right"] == pytest.approx(2) for r in res)
    assert all(r["offset"] == 0 for r in res)


def test_direction_flips_left_and_right():
    surf = _surface((box(-100, -2, 200, 4), CARRIAGEWAY))
    _, res = _measure_all({"a": LineString([(100, 0), (0, 0)])}, surf)
    assert res[0]["left"] == pytest.approx(2) and res[0]["right"] == pytest.approx(4)


def test_line_off_the_road():
    # Road y in [2, 8]; centreline at y = 0 is 2 m south of it (right edge is north-left side).
    surf = _surface((box(-100, 2, 200, 8), CARRIAGEWAY))
    _, res = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf)
    assert res[0]["left"] == pytest.approx(8) and res[0]["right"] == pytest.approx(-2)
    assert res[0]["offset"] == pytest.approx(2)
    # Too far away: no measurement.
    surf = _surface((box(-100, 6, 200, 12), CARRIAGEWAY))
    _, res = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf)
    assert np.isnan(res[0]["left"])


def test_first_edge_stops_at_footpath_and_inrit():
    # Carriageway [-3, 3]; footpath (not in BGT surface) [3, 5]; another carriageway
    # [5, 11] beyond it (e.g. a parallel road) must not be reached.
    surf = _surface(
        (box(-100, -3, 200, 3), CARRIAGEWAY),
        (box(-100, 5, 200, 11), CARRIAGEWAY),
        (box(40, -6, 60, -3), INRIT),  # driveway entrance on the right, x 40-60
        (box(60, -6, 80, -3), CARRIAGEWAY),  # drivable beyond the kerb
    )
    prof, res = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf)
    assert all(r["left"] == pytest.approx(3) for r in res)
    right = pd.Series([r["right"] for r in res], index=prof.s)
    assert right[(right.index > 40) & (right.index < 60)].eq(3).all()  # inrit stops
    # Directly adjacent carriageway (x 60-80) continues the road, by design.
    assert right[(right.index > 60) & (right.index < 80)].eq(6).all()


def test_kerb_to_kerb_walks_through_parking_and_cycle_path():
    surf = _surface(
        (box(-100, -3, 200, 3), CARRIAGEWAY),
        (box(-100, 3, 200, 5), PARKING),  # parallel parking lane, 2 m
        (box(-100, 5, 200, 7), CYCLE),  # cycle path, adjacent
        (box(-100, -5, 200, -4), CYCLE),  # cycle path behind a 1 m verge
    )
    _, cw = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf, "carriageway")
    _, kb = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf, "kerb")
    assert (cw[0]["left"], cw[0]["right"]) == pytest.approx((3, 3))
    assert (kb[0]["left"], kb[0]["right"]) == pytest.approx((7, 3))


def test_sliver_between_polygons_is_ignored():
    surf = _surface((box(-100, -3, 200, 0.99), CARRIAGEWAY), (box(-100, 1.01, 200, 3), CARRIAGEWAY))
    _, res = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf)
    assert res[0]["left"] == pytest.approx(3)


def test_curve_profiles_are_perpendicular():
    # Ring road of radius 50 around the origin, 6 m wide, centred on the line.
    centre = Point(0, 0)
    ring = centre.buffer(53, quad_segs=64).difference(centre.buffer(47, quad_segs=64))
    arc = LineString([(50 * np.cos(a), 50 * np.sin(a)) for a in np.linspace(0, np.pi / 2, 200)])
    _, res = _measure_all({"a": arc}, _surface((ring, CARRIAGEWAY)))
    tot = np.array([r["left"] + r["right"] for r in res])
    assert np.allclose(tot, 6, atol=0.05)


def test_cell_stops_profiles_between_parallel_roads():
    from backbone.baseline import nearest_centreline_cells

    # One 30 m wide paved area holding two centrelines 10 m apart.
    lines = gpd.GeoSeries(
        {"a": LineString([(0, 0), (100, 0)]), "b": LineString([(0, 10), (100, 10)])}, crs=28992
    )
    cells = nearest_centreline_cells(lines, box(-50, -50, 150, 60), 1.0)
    prof = make_profiles(lines[["a"]], 2.0, L)
    runs = vector_runs(prof, _surface((box(-100, -10, 200, 20), CARRIAGEWAY)), np.zeros(len(prof.s), dtype=int))
    stops = cell_interval(prof, cells)
    mid = len(prof.s) // 2
    r = measure(runs[mid], DEFINITIONS["carriageway"], 5.0, 0.05, stop=tuple(stops[mid]))
    assert r["left"] == pytest.approx(5, abs=0.05) and r["right"] == pytest.approx(10)
    assert r["cell_stop"]


def test_level_filter():
    surf = _surface((box(-100, -3, 200, 3), CARRIAGEWAY, 0), (box(-100, -10, 200, 10), CARRIAGEWAY, 1))
    prof = make_profiles(gpd.GeoSeries({"a": LineString([(0, 0), (100, 0)])}), 2.0, L)
    for level, width in [(0, 6), (1, 20)]:
        runs = vector_runs(prof, surf, np.full(len(prof.s), level))
        r = measure(runs[0], DEFINITIONS["carriageway"], 5.0, 0.05)
        assert r["left"] + r["right"] == pytest.approx(width)


def test_junction_distances():
    segs = pd.DataFrame(
        {
            "start_node": [1, 2, 2, 2, 7],
            "end_node": [2, 3, 4, 5, 2],
            "highway": ["residential", "residential", "secondary", "service", "service"],
            "service": [None, None, None, "driveway", "driveway"],
            "tag_width": [6.0, 6.0, 13.0, 3.0, 3.0],
        },
        index=["a", "b", "c", "d", "e"],
    )
    jd = junction_distances(segs)
    assert jd.loc["a", "start"] == 0  # dead end
    assert jd.loc["a", "end"] == pytest.approx(6.5 + 3)  # widest other at node 2 is c (13 m)
    assert jd.loc["c", "start"] == pytest.approx(3 + 3)
    # Only-minor case: a node where a residential meets nothing but driveways.
    segs2 = segs.loc[["d", "e"]].assign(end_node=[9, 9], start_node=[8, 10])
    segs2 = pd.concat([segs2, pd.DataFrame({"start_node": [9], "end_node": [11], "highway": ["residential"],
                                            "service": [None], "tag_width": [6.0]}, index=["f"])])  # fmt: skip
    assert junction_distances(segs2).loc["f", "start"] == pytest.approx(2.0)


def test_raster_and_vector_agree_within_a_pixel():
    """The same road measured from polygons and from a rasterized class map."""
    res = 0.25
    polys = [
        (box(-50, -3.3, 150, 2.7), CARRIAGEWAY),
        (box(-50, 2.7, 150, 4.9), PARKING),
        (box(30, -6.1, 45, -3.3), INRIT),
    ]
    centre = Point(50, 0)
    curve = centre.buffer(40, quad_segs=64).difference(centre.buffer(34, quad_segs=64))
    polys.append((shapely.intersection(curve, box(50, -60, 100, 0)), CARRIAGEWAY))
    surf = _surface(*polys)
    transform = from_origin(-60, 60, res, res)
    labels = rasterize(
        [(g, c) for g, c in zip(surf.geometry, surf["cls"])], out_shape=(480, 880), transform=transform, fill=0
    )
    line = LineString([(0, 0), (60, 0)])
    prof = make_profiles(gpd.GeoSeries({"a": line}), 2.0, L)
    vec = vector_runs(prof, surf, np.zeros(len(prof.s), dtype=int))
    ras = raster_runs(prof, labels, transform, step=res / 2)
    for d in ("carriageway", "kerb"):
        for v, r in zip(vec, ras):
            mv = measure(v, DEFINITIONS[d], 5.0, 0.05)
            mr = measure(r, DEFINITIONS[d], 5.0, 0.0)
            assert mr["left"] == pytest.approx(mv["left"], abs=res)
            assert mr["right"] == pytest.approx(mv["right"], abs=res)


def test_kerb_stops_at_deep_parking_bays():
    # Perpendicular bays 5 m deep on the left: kerb-to-kerb stops at the carriageway edge.
    surf = _surface((box(-100, -3, 200, 3), CARRIAGEWAY), (box(-100, 3, 200, 8), PARKING))
    _, kb = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf, "kerb")
    assert kb[0]["left"] == pytest.approx(3)
    # Two shallow runs of different classes in a row are both kept.
    surf = _surface(
        (box(-100, -3, 200, 3), CARRIAGEWAY), (box(-100, 3, 200, 5.5), PARKING), (box(-100, 5.5, 200, 8), CYCLE)
    )
    _, kb = _measure_all({"a": LineString([(0, 0), (100, 0)])}, surf, "kerb")
    assert kb[0]["left"] == pytest.approx(8)
