"""Named pilot areas and bounding-box helpers. All boxes are in EPSG:28992 (RD New)."""

from dataclasses import dataclass, field

from pyproj import Transformer

from backbone.config import RD_EPSG, WGS84_EPSG


@dataclass(frozen=True)
class BBox:
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    def __post_init__(self):
        if not (self.xmin < self.xmax and self.ymin < self.ymax):
            raise ValueError(f"degenerate bbox: {self}")

    @property
    def width(self) -> float:
        return self.xmax - self.xmin

    @property
    def height(self) -> float:
        return self.ymax - self.ymin

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.xmin, self.ymin, self.xmax, self.ymax)

    def to_wgs84(self, densify: int = 21) -> tuple[float, float, float, float]:
        """Lon/lat envelope that fully contains this box (edges are densified,
        because straight RD edges are curved in lon/lat)."""
        t = Transformer.from_crs(RD_EPSG, WGS84_EPSG, always_xy=True)
        return t.transform_bounds(*self.as_tuple(), densify_pts=densify)


@dataclass(frozen=True)
class Area:
    name: str
    bbox: BBox
    description: str = ""
    # QA zoom crops: (label, x_centre, y_centre) in RD metres.
    qa_crops: tuple[tuple[str, float, float], ...] = field(default=())


AREAS = {
    a.name: a
    for a in [
        Area(
            name="utrecht_centrum",
            bbox=BBox(135900, 454900, 137900, 456900),
            description="Dense city centre: old town, singels, station area and ring roads.",
            qa_crops=(
                ("pausdam", 136940, 455840),
                ("market_square", 136260, 456010),
                ("biltstraat", 137600, 456260),
            ),
        ),
    ]
}


def parse_bbox(text: str) -> BBox:
    parts = [float(p) for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be 'xmin,ymin,xmax,ymax' in EPSG:28992")
    return BBox(*parts)


def bbox_name(bbox: BBox) -> str:
    return "bbox_" + "_".join(f"{v:.0f}" for v in bbox.as_tuple())
