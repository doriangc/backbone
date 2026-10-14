import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, box

from backbone import profiles
from backbone.areas import BBox
from backbone.grid import Grid
from backbone.surface_data import (
    CLS,
    TO_PROFILE_CLASS,
    augment,
    label_polygons,
    rasterize_labels,
    resample_image,
    tile_positions,
    wegdeel_classes,
)

RES = 0.25
GRID = Grid.for_bbox(BBox(1000, 2000, 1016, 2016), RES)  # 64 x 64 px


def _write_area(tmp_path, wegdeel, terrain):
    """Minimal area dir with the two BGT layers label_polygons reads."""
    wegdeel.to_file(tmp_path / "bgt_wegdeel.gpkg")
    terrain.to_file(tmp_path / "bgt_onbegroeidterreindeel.gpkg")
    return tmp_path


def _wegdeel(rows):
    """rows: (geometry, functie, drivable, level)"""
    return gpd.GeoDataFrame(
        {
            "functie": [r[1] for r in rows],
            "drivable": [r[2] for r in rows],
            "relatieve_hoogteligging": [r[3] for r in rows],
        },
        geometry=[r[0] for r in rows],
        crs=28992,
    )


def _terrain(rows):
    """rows: (geometry, paved, service_paved)"""
    return gpd.GeoDataFrame(
        {"paved": [r[1] for r in rows], "service_paved": [r[2] for r in rows]},
        geometry=[r[0] for r in rows],
        crs=28992,
    )


def test_wegdeel_class_mapping():
    wd = _wegdeel(
        [
            (box(0, 0, 1, 1), "rijbaan lokale weg", True, 0),
            (box(0, 0, 1, 1), "inrit", True, 0),
            (box(0, 0, 1, 1), "parkeervlak", False, 0),
            (box(0, 0, 1, 1), "fietspad", False, 0),
            (box(0, 0, 1, 1), "fietspad", True, 0),  # bicycle street under a drivable way
            (box(0, 0, 1, 1), "voetpad op trap", False, 0),
            (box(0, 0, 1, 1), "voetgangersgebied", False, 0),
            (box(0, 0, 1, 1), "spoorbaan", False, 0),  # railway track bed
            (box(0, 0, 1, 1), "ruiterpad", False, 0),
        ]
    )
    names = ["carriageway", "inrit", "parking", "cycle", "carriageway", "footpath", "footpath", "background",
             "other_paved"]  # fmt: skip
    assert wegdeel_classes(wd).tolist() == [CLS[n] for n in names]


def test_rasterize_known_pixels_north_up():
    # A 1 x 0.5 m box in the north-west corner covers pixel centres of rows 0-1, cols 0-3.
    polys = gpd.GeoDataFrame({"cls": [CLS["parking"]]}, geometry=[box(1000, 2015.5, 1001, 2016)], crs=28992)
    lab = rasterize_labels(polys, GRID)
    assert lab.shape == (64, 64)
    expect = np.zeros_like(lab)
    expect[0:2, 0:4] = CLS["parking"]
    np.testing.assert_array_equal(lab, expect)
    # Pixel-centre rule: an edge at x = 1002.1 takes cols 0-7 (centres up to
    # 1001.875 m), not col 8 (centre 1002.125 m).
    polys = gpd.GeoDataFrame({"cls": [CLS["cycle"]]}, geometry=[box(1000, 2000, 1002.1, 2016)], crs=28992)
    assert rasterize_labels(polys, GRID)[0].tolist().count(CLS["cycle"]) == 8  # centres 1000.125..1001.875


def test_draw_order_and_levels(tmp_path):
    wd = _wegdeel(
        [
            (box(1000, 2000, 1016, 2008), "rijbaan lokale weg", True, 0),  # south half
            (box(1000, 2004, 1016, 2012), "parkeervlak", False, 0),  # overlaps carriageway by 4 m
            (box(1004, 2000, 1008, 2016), "voetpad", False, 1),  # footbridge over both
            (box(1012, 2000, 1016, 2016), "rijbaan lokale weg", True, -1),  # tunnel, invisible
        ]
    )
    terrain = _terrain(
        [
            (box(1000, 2012, 1002, 2016), True, True),  # under a service way -> carriageway
            (box(1002, 2012, 1004, 2016), True, False),  # other paved
            (box(1008, 2012, 1012, 2016), False, False),  # unpaved -> background
        ]
    )
    lab = rasterize_labels(label_polygons(_write_area(tmp_path, wd, terrain)), GRID)

    def at(x, y):
        c, r = ~GRID.transform @ (x, y)
        return lab[int(r), int(c)]

    assert at(1001, 2006) == CLS["carriageway"]  # carriageway beats parking on the same level
    assert at(1001, 2010) == CLS["parking"]
    assert at(1006, 2006) == CLS["footpath"]  # level 1 drawn over level 0
    assert at(1014, 2014) == CLS["background"]  # tunnel at level -1 dropped
    assert at(1014, 2006) == CLS["carriageway"]  # ground carriageway there still visible
    assert at(1001, 2014) == CLS["carriageway"]
    assert at(1003, 2014) == CLS["other_paved"]
    assert at(1010, 2014) == CLS["background"]


def test_resample_image_alignment(tmp_path):
    # 8 cm image, 16 x 16 m: a bright vertical band x in [1004, 1008), a bright
    # horizontal band y in [2012, 2014). At 25 cm the bands must land on
    # cols 16-31 and rows 8-15 (band edges fall on 25 cm pixel edges).
    res8 = 0.08
    n = 200
    xs = 1000 + (np.arange(n) + 0.5) * res8
    ys = 2016 - (np.arange(n) + 0.5) * res8
    img = np.zeros((3, n, n), np.uint8)
    img[0][:, (xs >= 1004) & (xs < 1008)] = 200
    img[1][(ys >= 2012) & (ys < 2014), :] = 200
    src = tmp_path / "img.tif"
    with rasterio.open(src, "w", driver="GTiff", width=n, height=n, count=3, dtype="uint8", crs="EPSG:28992",
                       transform=from_origin(1000, 2016, res8, res8)) as ds:  # fmt: skip
        ds.write(img)
    out = np.zeros((64, 64, 3), np.uint8)
    resample_image(src, GRID, out)
    red_cols = np.nonzero(out[32, :, 0] > 150)[0]
    green_rows = np.nonzero(out[:, 2, 1] > 150)[0]
    assert red_cols.min() == 16 and red_cols.max() == 31
    assert green_rows.min() == 8 and green_rows.max() == 15
    # Edge pixels mix both sides; nothing leaks further.
    assert out[32, 14, 0] == 0 and out[32, 33, 0] == 0


def test_resample_rejects_wrong_extent(tmp_path):
    src = tmp_path / "img.tif"
    with rasterio.open(src, "w", driver="GTiff", width=10, height=10, count=3, dtype="uint8", crs="EPSG:28992",
                       transform=from_origin(999, 2016, 1.6, 1.6)) as ds:  # fmt: skip
        ds.write(np.zeros((3, 10, 10), np.uint8))
    with pytest.raises(ValueError):
        resample_image(src, GRID, np.zeros((64, 64, 3), np.uint8))


@pytest.mark.parametrize("seed", range(20))
def test_augment_keeps_image_and_label_aligned(seed):
    # Label quadrants; the red channel is bright exactly where label == 1.
    lab = np.zeros((128, 128), np.uint8)
    lab[:64, 32:] = 1
    lab[96:, :48] = 1
    img = np.full((128, 128, 3), 60, np.uint8)
    img[..., 0][lab == 1] = 180
    a_img, a_lab = augment(img, lab, np.random.default_rng(seed))
    assert a_lab.shape == lab.shape and sorted(np.unique(a_lab)) == [0, 1]
    assert np.count_nonzero(a_lab) == np.count_nonzero(lab)  # dihedral transform only
    # Away from edges (blur up to 4 px), every label-1 pixel is brighter than every label-0 pixel.
    from scipy.ndimage import binary_erosion

    one = binary_erosion(a_lab == 1, iterations=5)
    zero = binary_erosion(a_lab == 0, iterations=5)
    assert a_img[..., 0][one].min() > a_img[..., 0][zero].max()


def test_tile_positions_stay_inside():
    pos = tile_positions((1000, 700), chip=256, stride=128)
    assert all(r + 256 <= 1000 and c + 256 <= 700 for r, c in pos)
    assert (0, 0) in pos and (640, 384) in pos


def test_label_raster_gives_phase3_widths(tmp_path):
    """Phase 5 reads predictions through profiles.raster_runs; on the BGT label
    raster that must reproduce the Phase 3 vector widths within a pixel."""
    from backbone.profiles import DEFINITIONS, make_profiles, measure, raster_runs, vector_runs

    wd = _wegdeel(
        [
            (box(1000, 2005, 1016, 2010.6), "rijbaan lokale weg", True, 0),
            (box(1000, 2010.6, 1016, 2012.4), "parkeervlak", False, 0),
            (box(1000, 2003.3, 1016, 2005), "fietspad", False, 0),
            (box(1000, 2012.4, 1016, 2014), "voetpad", False, 0),
        ]
    )
    area = _write_area(tmp_path, wd, _terrain([(box(0, 0, 1, 1), False, False)]))
    lab = TO_PROFILE_CLASS[rasterize_labels(label_polygons(area), GRID)]
    prof = make_profiles(gpd.GeoSeries({"a": LineString([(1002, 2008), (1014, 2008)])}), 2.0, 8.0)
    vec = vector_runs(prof, profiles.build_surface(area), np.zeros(len(prof.s), dtype=int))
    ras = raster_runs(prof, lab, GRID.transform, step=RES / 2)
    for d in ("carriageway", "kerb"):
        for v, r in zip(vec, ras):
            mv, mr = measure(v, DEFINITIONS[d], 5.0, 0.05), measure(r, DEFINITIONS[d], 5.0, 0.0)
            assert mr["left"] == pytest.approx(mv["left"], abs=RES)
            assert mr["right"] == pytest.approx(mv["right"], abs=RES)
    assert measure(vec[0], DEFINITIONS["kerb"], 5.0, 0.05)["left"] == pytest.approx(4.4)
