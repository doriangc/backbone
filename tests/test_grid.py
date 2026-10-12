import numpy as np
import pytest
import rasterio
from PIL import Image
from shapely.geometry import box

from backbone.areas import BBox
from backbone.fetch import mosaic_tiles
from backbone.grid import Grid, snap_bbox


def test_snap_is_noop_on_aligned_bbox():
    bb = BBox(135900, 454900, 137900, 456900)
    for res in (0.08, 0.25):
        assert snap_bbox(bb, res) == bb


def test_snap_grows_outward():
    bb = snap_bbox(BBox(100.1, 200.9, 110.05, 210.0), 0.25)
    assert bb.as_tuple() == (100.0, 200.75, 110.25, 210.0)


@pytest.mark.parametrize("res,expected", [(0.08, 25000), (0.25, 8000)])
def test_pilot_grid_size(res, expected):
    g = Grid.for_bbox(BBox(135900, 454900, 137900, 456900), res)
    assert (g.width, g.height) == (expected, expected)


@pytest.mark.parametrize("res,tile_px", [(0.08, 2000), (0.25, 2000), (0.25, 3000), (1.0, 7)])
def test_tiles_partition_grid_exactly(res, tile_px):
    g = Grid.for_bbox(BBox(135900, 454900, 137900, 456900), res)
    tiles = g.tiles(tile_px)
    # Pixel accounting: every pixel exactly once.
    covered = np.zeros((g.height, g.width), dtype=np.uint8) if g.width < 10000 else None
    total = 0
    for t in tiles:
        assert t.width <= tile_px and t.height <= tile_px
        total += t.width * t.height
        if covered is not None:
            covered[t.row_off : t.row_off + t.height, t.col_off : t.col_off + t.width] += 1
        # Tile bbox matches its pixel window under the grid transform.
        x0, y0 = g.transform @ (t.col_off, t.row_off)
        x1, y1 = g.transform @ (t.col_off + t.width, t.row_off + t.height)
        assert t.bbox.as_tuple() == pytest.approx((x0, y1, x1, y0), abs=1e-9)
        assert t.bbox.width == pytest.approx(t.width * res, abs=1e-9)
    assert total == g.width * g.height
    if covered is not None:
        assert (covered == 1).all()
    # Union of tile footprints is the grid bbox, with no overlaps.
    union = sum(box(*t.bbox.as_tuple()).area for t in tiles)
    assert union == pytest.approx(g.bbox.width * g.bbox.height)


def test_mosaic_places_pixels_at_correct_coordinates(tmp_path):
    """Write tiles whose pixel values encode their own map coordinates, mosaic
    them, and check that sampling the GeoTIFF at a map point returns that point."""
    res = 1.0
    g = Grid.for_bbox(BBox(1000, 2000, 1050, 2030), res)  # 50 x 30 px
    tiles = g.tiles(16)  # uneven split on purpose
    tile_dir = tmp_path / "tiles"
    tile_dir.mkdir()
    for t in tiles:
        rows = np.arange(t.height)[:, None] + t.row_off
        cols = np.arange(t.width)[None, :] + t.col_off
        arr = np.zeros((t.height, t.width, 3), dtype=np.uint8)
        arr[..., 0] = cols * 4  # x-index encoded in red
        arr[..., 1] = rows * 4  # y-index encoded in green
        Image.fromarray(arr).save(tile_dir / f"r{t.row:03d}_c{t.col:03d}.jpg", quality=100, subsampling=0)
    out = tmp_path / "m.tif"
    mosaic_tiles(g, tiles, tile_dir, out)
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 28992
        assert ds.bounds == (1000, 2000, 1050, 2030)
        # Centre of pixel col 37, row 5 is at x=1037.5, y=2030-5.5.
        for col, row in [(0, 0), (37, 5), (49, 29), (15, 16), (16, 15)]:
            x, y = 1000 + col + 0.5, 2030 - row - 0.5
            r, gch, _ = next(ds.sample([(x, y)], indexes=[1, 2, 3]))
            # JPEG is lossy; values differ by 4 per pixel, so ±2 still identifies the pixel.
            assert abs(int(r) - col * 4) <= 2, (col, row, r)
            assert abs(int(gch) - row * 4) <= 2, (col, row, gch)


@pytest.mark.parametrize("true_dx,true_dy", [(0.0, 0.0), (0.75, 0.0), (0.0, -1.0), (-0.5, 1.25)])
def test_edge_offset_recovers_known_shift(tmp_path, true_dx, true_dy):
    """Image has a bright square; the 'BGT' polygon is the same square displaced
    by (-true_dx, -true_dy), so moving BGT by (true_dx, true_dy) realigns it."""
    import geopandas as gpd

    from backbone.qa import edge_offset

    res = 0.25
    g = Grid.for_bbox(BBox(0, 0, 50, 50), res)
    img = np.full((g.height, g.width, 3), 40, dtype=np.uint8)
    # Square [15, 35] x [15, 35] m; rows count from the north edge.
    img[round(15 / res) : round(35 / res), round(15 / res) : round(35 / res)] = 220
    tif = tmp_path / "img.tif"
    with rasterio.open(
        tif, "w", driver="GTiff", dtype="uint8", count=3, width=g.width, height=g.height,
        crs="EPSG:28992", transform=g.transform,
    ) as ds:  # fmt: skip
        ds.write(img.transpose(2, 0, 1))
    poly = box(15 - true_dx, 15 - true_dy, 35 - true_dx, 35 - true_dy)
    bgt = gpd.GeoDataFrame({"relatieve_hoogteligging": [0]}, geometry=[poly], crs=28992)
    r, _ = edge_offset(tif, BBox(0, 0, 50, 50), bgt, res, max_shift_m=2.0)
    assert r["dx_m"] == pytest.approx(true_dx, abs=res)
    assert r["dy_m"] == pytest.approx(true_dy, abs=res)
