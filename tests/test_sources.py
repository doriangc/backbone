import re

import geopandas as gpd
import pytest
from shapely.geometry import box

from backbone.areas import AREAS, BBox
from backbone.fetch import bgt_current, is_drivable_highway, osm_to_gdf, overpass_query


def test_wgs84_envelope_contains_rd_bbox():
    bb = AREAS["utrecht_centrum"].bbox
    w, s, e, n = bb.to_wgs84()
    rd = gpd.GeoSeries([box(*bb.as_tuple())], crs=28992).to_crs(4326).iloc[0]
    assert box(w, s, e, n).contains(rd)
    # Sanity: Utrecht is around 5.12E, 52.09N.
    assert 5.0 < w < e < 5.2 and 52.0 < s < n < 52.2


@pytest.mark.parametrize(
    "value,ok",
    [
        ("residential", True),
        ("service", True),
        ("primary_link", True),
        ("motorway_link", True),
        ("track", False),
        ("cycleway", False),
        ("footway", False),
        ("pedestrian", False),
        ("path", False),
        ("residential_link", False),
        ("primary_linkx", False),
        (None, False),
    ],
)
def test_drivable_highway_filter(value, ok):
    assert is_drivable_highway(value) is ok


def test_overpass_query_has_lat_lon_order():
    q = overpass_query(BBox(135900, 454900, 137900, 456900))
    s, w, n, e = (float(v) for v in re.search(r"\]\(([-\d.,]+)\);out", q).group(1).split(","))
    assert 52 < s < n < 53 and 5 < w < e < 6


def _way(id_, nodes, **tags):
    return {"type": "way", "id": id_, "nodes": nodes, "tags": tags}


def test_osm_to_gdf_filters_and_keeps_nodes():
    # Nodes around the Domtoren, in lon/lat.
    nodes = [
        {"type": "node", "id": 1, "lon": 5.1210, "lat": 52.0905},
        {"type": "node", "id": 2, "lon": 5.1220, "lat": 52.0905},
        {"type": "node", "id": 3, "lon": 5.1230, "lat": 52.0910},
        {"type": "node", "id": 9, "lon": 4.0, "lat": 51.0},  # far away
        {"type": "node", "id": 10, "lon": 4.001, "lat": 51.0},
    ]
    ways = [
        _way(100, [1, 2, 3], highway="residential", lanes="2"),
        _way(101, [1, 2], highway="cycleway"),
        _way(102, [1, 2, 3, 1], highway="service", area="yes"),
        _way(103, [9, 10], highway="primary"),  # outside the RD bbox
    ]
    gdf = osm_to_gdf({"elements": nodes + ways}, AREAS["utrecht_centrum"].bbox)
    assert list(gdf["osm_id"]) == [100]
    assert gdf.crs.to_epsg() == 28992
    assert gdf.iloc[0]["nodes"] == "[1, 2, 3]"
    assert gdf.iloc[0]["lanes"] == "2"
    # Roughly 140 m in RD metres (0.002 deg lon at 52N ~ 137 m, plus the diagonal).
    assert 100 < gdf.length.iloc[0] < 200


def test_bgt_current_drops_history():
    g = gpd.GeoDataFrame(
        {
            "lokaal_id": ["a", "a", "b", "c"],
            "eind_registratie": ["2017-01-01", None, None, None],
            "termination_date": [None, None, None, "2020-01-01"],
        },
        geometry=[box(0, 0, 1, 1)] * 4,
        crs=28992,
    )
    cur = bgt_current(g)
    assert list(cur["lokaal_id"]) == ["a", "b"]


def test_bgt_current_rejects_two_current_versions():
    g = gpd.GeoDataFrame(
        {"lokaal_id": ["a", "a"], "eind_registratie": [None, None], "termination_date": [None, None]},
        geometry=[box(0, 0, 1, 1)] * 2,
        crs=28992,
    )
    with pytest.raises(ValueError):
        bgt_current(g)


def test_write_json_gz_is_byte_reproducible(tmp_path):
    import time

    from backbone.fetch import sha256, write_json_gz

    a, b = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    write_json_gz({"x": [1, 2, 3]}, a)
    time.sleep(1.1)  # gzip mtime has 1 s resolution
    write_json_gz({"x": [1, 2, 3]}, b)
    assert sha256(a) == sha256(b)
