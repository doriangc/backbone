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
    area_type: str  # "city", "suburb" or "rural", for per-type metrics
    description: str = ""
    # QA zoom crops: (label, x_centre, y_centre) in RD metres.
    qa_crops: tuple[tuple[str, float, float], ...] = field(default=())


AREAS = {
    a.name: a
    for a in [
        Area(
            name="utrecht_centrum",
            bbox=BBox(135900, 454900, 137900, 456900),
            area_type="city",
            description="Dense city centre: old town, singels, station area and ring roads.",
            qa_crops=(
                ("pausdam", 136940, 455840),
                ("market_square", 136260, 456010),
                ("biltstraat", 137600, 456260),
            ),
        ),
        Area(
            name="houten",
            bbox=BBox(139000, 448100, 141000, 450100),
            area_type="suburb",
            description="Houten: residential loops and cul-de-sacs, ring road, rail line, business park.",
            qa_crops=(
                ("roundabout", 140570, 448700),
                ("residential_loop", 139500, 449100),
                ("business_park", 140100, 448300),
            ),
        ),
        Area(
            name="benschop",
            bbox=BBox(126400, 445400, 128400, 447400),
            area_type="rural",
            description="Benschop: ribbon village on a dike road, polder access roads, provincial road.",
            qa_crops=(
                ("dorp", 127160, 446795),
                ("provincial_road", 127667, 445638),
                ("village_residential", 127173, 446585),
            ),
        ),
        Area(
            name="amsterdam_centrum",
            bbox=BBox(120400, 486300, 122400, 488300),
            area_type="city",
            description="City centre (BAG woonplaats: Amsterdam).",
        ),
        Area(
            name="rotterdam_centrum",
            bbox=BBox(91600, 436300, 93600, 438300),
            area_type="city",
            description="City centre (BAG woonplaats: Rotterdam).",
        ),
        Area(
            name="den_haag_centrum",
            bbox=BBox(80300, 454000, 82300, 456000),
            area_type="city",
            description="City centre (BAG woonplaats: 's-Gravenhage).",
        ),
        Area(
            name="groningen_centrum",
            bbox=BBox(232900, 581000, 234900, 583000),
            area_type="city",
            description="City centre (BAG woonplaats: Groningen).",
        ),
        Area(
            name="maastricht_centrum",
            bbox=BBox(175400, 317000, 177400, 319000),
            area_type="city",
            description="City centre (BAG woonplaats: Maastricht).",
        ),
        Area(
            name="eindhoven_centrum",
            bbox=BBox(160300, 382300, 162300, 384300),
            area_type="city",
            description="City centre (BAG woonplaats: Eindhoven).",
        ),
        Area(
            name="almere_buiten",
            bbox=BBox(145700, 488400, 147700, 490400),
            area_type="suburb",
            description="Residential suburb (BAG woonplaats: Almere).",
        ),
        Area(
            name="zoetermeer",
            bbox=BBox(92500, 451500, 94500, 453500),
            area_type="suburb",
            description="Residential suburb (BAG woonplaats: Zoetermeer).",
        ),
        Area(
            name="amersfoort_vathorst",
            bbox=BBox(156900, 466400, 158900, 468400),
            area_type="suburb",
            description="Residential suburb (BAG woonplaats: Amersfoort).",
        ),
        Area(
            name="apeldoorn_zuid",
            bbox=BBox(193200, 466000, 195200, 468000),
            area_type="suburb",
            description="Residential suburb (BAG woonplaats: Apeldoorn).",
        ),
        Area(
            name="breda_haagse_beemden",
            bbox=BBox(108800, 402400, 110800, 404400),
            area_type="suburb",
            description="Residential suburb (BAG woonplaats: Breda).",
        ),
        Area(
            name="zwolle_stadshagen",
            bbox=BBox(199700, 503900, 201700, 505900),
            area_type="suburb",
            description="Residential suburb (BAG woonplaats: Zwolle).",
        ),
        Area(
            name="wommels",
            bbox=BBox(167600, 568300, 169600, 570300),
            area_type="rural",
            description="Rural: village and countryside (BAG woonplaats: Wommels).",
        ),
        Area(
            name="kapelle",
            bbox=BBox(54100, 386800, 56100, 388800),
            area_type="rural",
            description="Rural: village and countryside (BAG woonplaats: Kapelle).",
        ),
        Area(
            name="westerbork",
            bbox=BBox(236400, 540000, 238400, 542000),
            area_type="rural",
            description="Rural: village and countryside (BAG woonplaats: Westerbork).",
        ),
        Area(
            name="gulpen",
            bbox=BBox(189400, 312500, 191400, 314500),
            area_type="rural",
            description="Rural: village and countryside (BAG woonplaats: Gulpen).",
        ),
        Area(
            name="aalten_buitengebied",
            bbox=BBox(237400, 440500, 239400, 442500),
            area_type="rural",
            description="Rural: village and countryside (BAG woonplaats: Aalten).",
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
