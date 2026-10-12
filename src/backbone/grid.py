"""Pixel grid for imagery requests.

Convention: a grid is defined by its outer bbox edges and a square pixel size.
Pixel (row 0, col 0) covers [xmin, xmin+res] x [ymax-res, ymax]. WMS GetMap bboxes
and GeoTIFF transforms both refer to outer pixel edges, so no half-pixel shifts.
"""

import math
from dataclasses import dataclass

from affine import Affine

from backbone.areas import BBox


def snap_bbox(bbox: BBox, res: float) -> BBox:
    """Grow bbox outward to the nearest multiple of `res`, so the grid is
    identical whatever the input precision."""
    # Round before floor/ceil so float noise (136000/0.08 = 1699999.999…) does not shift a pixel.
    def down(v):
        return math.floor(round(v / res, 6)) * res

    def up(v):
        return math.ceil(round(v / res, 6)) * res

    return BBox(down(bbox.xmin), down(bbox.ymin), up(bbox.xmax), up(bbox.ymax))


@dataclass(frozen=True)
class Tile:
    row: int  # tile row index (0 = north)
    col: int
    row_off: int  # pixel offset in the full mosaic
    col_off: int
    height: int
    width: int
    bbox: BBox


@dataclass(frozen=True)
class Grid:
    bbox: BBox  # snapped
    res: float

    @classmethod
    def for_bbox(cls, bbox: BBox, res: float) -> "Grid":
        return cls(snap_bbox(bbox, res), res)

    @property
    def width(self) -> int:
        return round(self.bbox.width / self.res)

    @property
    def height(self) -> int:
        return round(self.bbox.height / self.res)

    @property
    def transform(self) -> Affine:
        return Affine(self.res, 0, self.bbox.xmin, 0, -self.res, self.bbox.ymax)

    def tiles(self, tile_px: int) -> list[Tile]:
        out = []
        for r, row_off in enumerate(range(0, self.height, tile_px)):
            h = min(tile_px, self.height - row_off)
            for c, col_off in enumerate(range(0, self.width, tile_px)):
                w = min(tile_px, self.width - col_off)
                # Compute edges from integer pixel offsets to avoid accumulating float error.
                bb = BBox(
                    self.bbox.xmin + col_off * self.res,
                    self.bbox.ymax - (row_off + h) * self.res,
                    self.bbox.xmin + (col_off + w) * self.res,
                    self.bbox.ymax - row_off * self.res,
                )
                out.append(Tile(r, c, row_off, col_off, h, w, bb))
        return out
