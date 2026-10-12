import json

import geopandas as gpd
import pytest
from shapely.geometry import LineString, box

from backbone.segments import parse_lanes, parse_width, split_ways, tag_width


def _ways(*ways):
    """ways: (osm_id, [(node_id, x, y), ...])"""
    rows = [
        {"osm_id": oid, "highway": "residential", "nodes": json.dumps([n for n, _, _ in pts]),
         "geometry": LineString([(x, y) for _, x, y in pts])}
        for oid, pts in ways
    ]  # fmt: skip
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=28992)


def test_split_at_shared_nodes_only():
    # Way 1 runs 1-2-3-4; way 2 crosses it at node 3. Node 2 is not shared.
    ways = _ways(
        (1, [(1, 0, 0), (2, 10, 0), (3, 20, 0), (4, 30, 0)]),
        (2, [(5, 20, -10), (3, 20, 0), (6, 20, 10)]),
    )
    segs = split_ways(ways)
    assert sorted(segs["segment_id"]) == ["1:1-3", "1:3-4", "2:3-6", "2:5-3"]
    s = segs.set_index("segment_id")
    assert s.loc["1:1-3", "nodes"] == "[1, 2, 3]"
    assert s.geometry["1:1-3"].length == pytest.approx(20)
    assert s.loc["1:1-3", "start_node"] == 1 and s.loc["1:1-3", "end_node"] == 3


def test_endpoint_junction_does_not_split_twice():
    # T-junction where way 2 starts at the end of way 1.
    ways = _ways((1, [(1, 0, 0), (2, 10, 0)]), (2, [(2, 10, 0), (3, 20, 0)]))
    assert sorted(split_ways(ways)["segment_id"]) == ["1:1-2", "2:2-3"]


def test_excluded_way_does_not_split():
    # A parking aisle touching node 2 is not in the input, so no split.
    ways = _ways((1, [(1, 0, 0), (2, 10, 0), (3, 20, 0)]))
    assert list(split_ways(ways)["segment_id"]) == ["1:1-3"]


def test_loop_splits_at_revisited_node():
    # Lollipop: 1-2-3-4-2, revisits node 2.
    ways = _ways((1, [(1, 0, 0), (2, 10, 0), (3, 20, 0), (4, 15, 10), (2, 10, 0)]))
    segs = split_ways(ways)
    assert sorted(segs["segment_id"]) == ["1:1-2", "1:2-2"]


def test_closed_way_without_junctions_is_one_segment():
    ways = _ways((1, [(1, 0, 0), (2, 10, 0), (3, 10, 10), (1, 0, 0)]))
    segs = split_ways(ways)
    assert list(segs["segment_id"]) == ["1:1-1"]
    assert segs.geometry.iloc[0].is_ring


@pytest.mark.parametrize(
    "value,expected",
    [
        ("3.5", 3.5), ("3,5", 3.5), ("3.5 m", 3.5), ("4m", 4.0), ("12'", 3.6576),
        ("12'6\"", 3.81), ("10 ft", 3.048), ("3-4", None), ("3;4", None), ("wide", None),
        ("0", None), (None, None), (float("nan"), None),
    ],
)  # fmt: skip
def test_parse_width(value, expected):
    got = parse_width(value)
    assert got == (pytest.approx(expected, abs=1e-3) if expected is not None else None)


@pytest.mark.parametrize("value,expected", [("2", 2), ("1", 1), ("2.0", 2), ("2;3", None), ("1.5", None), ("0", None), (None, None)])
def test_parse_lanes(value, expected):
    assert parse_lanes(value) == expected


@pytest.mark.parametrize(
    "tags,expected",
    [
        ({"highway": "residential", "width": "5.5", "lanes": "1"}, (5.5, "width")),
        ({"highway": "residential", "width": "wide", "lanes": "1"}, (3.0, "lanes")),
        ({"highway": "secondary", "lanes": "4"}, (13.0, "lanes")),
        ({"highway": "primary_link", "lanes": "1"}, (3.5, "lanes")),
        ({"highway": "residential"}, (6.0, "default")),
        ({"highway": "residential", "oneway": "yes"}, (3.0, "default")),
        ({"highway": "tertiary", "junction": "roundabout"}, (3.25, "default")),
        ({"highway": "service"}, (3.0, "default")),
        ({"highway": "motorway"}, (7.0, "default")),
    ],
)
def test_tag_width(tags, expected):
    w, src = tag_width(tags)
    assert (round(w, 3), src) == expected


def test_score_straight_road_known_width():
    from backbone.baseline import score

    surface = gpd.GeoDataFrame(geometry=[box(-100, -3, 100, 3)], crs=28992)  # 6 m wide road
    line = LineString([(0, 0), (50, 0)])
    cell = box(-1000, -1000, 1000, 1000)
    exact = score(line, 6.0, cell, surface)
    assert exact["iou"] == pytest.approx(1.0)
    assert exact["ref_width"] == pytest.approx(6.0)
    narrow = score(line, 3.0, cell, surface)
    assert narrow["iou"] == pytest.approx(0.5)
    # Off-centre centreline: 1 m north of the road axis, tag width correct.
    off = score(LineString([(0, 1), (50, 1)]), 6.0, cell, surface)
    assert off["iou"] == pytest.approx(5 / 7)


def test_cells_split_parallel_roads_at_midline():
    from backbone.baseline import nearest_centreline_cells, score

    lines = gpd.GeoSeries(
        {"a": LineString([(0, 0), (100, 0)]), "b": LineString([(0, 10), (100, 10)])}, crs=28992
    )
    cells = nearest_centreline_cells(lines, box(-50, -50, 150, 60), 1.0)
    # Away from the line ends, the boundary between the cells is y = 5.
    probe = box(20, -40, 80, 50)
    a = cells["a"].intersection(probe)
    assert a.bounds[3] == pytest.approx(5, abs=0.05)
    # A surface covering both roads (y in [-3, 13]) only counts up to y = 5 for road a.
    surface = gpd.GeoDataFrame(geometry=[box(-100, -3, 200, 13)], crs=28992)
    r = score(LineString([(20, 0), (80, 0)]), 6.0, cells["a"], surface)
    assert r["ref_width"] == pytest.approx(8.0, abs=0.1)
