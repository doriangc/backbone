"""OSM ways to segments, and tag-based widths.

Splitting rule (SPEC "Segment"): a kept way is split at every node it shares
with another kept way, at every node it visits more than once (loops), and at
its own ends. Ways excluded in fetch (parking, hidden roads, private yards) are
not in the input, so they do not split anything. Segment ids are
`<osm_id>:<first_node>-<last_node>`; a way that visits the same node pair twice
gets a `#<n>` suffix on the repeats.
"""

import json
import re
from collections import Counter

import geopandas as gpd
from shapely.geometry import LineString

from backbone import config


def split_ways(ways: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Split kept ways (from osm_drivable.gpkg, layer `ways`) into segments.

    Relies on each way's geometry having one vertex per OSM node, in order,
    which is how fetch.osm_to_gdf builds it.
    """
    node_lists = [json.loads(n) for n in ways["nodes"]]
    # Number of distinct ways each node belongs to.
    way_count = Counter(n for nodes in node_lists for n in set(nodes))

    rows = []
    for (_, way), nodes in zip(ways.iterrows(), node_lists):
        coords = list(way.geometry.coords)
        if len(coords) != len(nodes):
            raise ValueError(f"way {way.osm_id}: {len(coords)} vertices for {len(nodes)} nodes")
        in_way = Counter(nodes)
        cuts = [0] + [
            i for i in range(1, len(nodes) - 1) if way_count[nodes[i]] > 1 or in_way[nodes[i]] > 1
        ] + [len(nodes) - 1]
        seen = Counter()
        for a, b in zip(cuts[:-1], cuts[1:]):
            part = coords[a : b + 1]
            if len({tuple(c) for c in part}) < 2:
                continue  # degenerate (repeated node)
            key = f"{way.osm_id}:{nodes[a]}-{nodes[b]}"
            seen[key] += 1
            seg_id = key if seen[key] == 1 else f"{key}#{seen[key]}"
            row = way.drop(labels=["geometry", "nodes"]).to_dict()
            row.update(
                segment_id=seg_id,
                start_node=nodes[a],
                end_node=nodes[b],
                nodes=json.dumps(nodes[a : b + 1]),
                geometry=LineString(part),
            )
            rows.append(row)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=ways.crs)


_METRIC = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(m|meter|meters|metre|metres)?\s*$", re.I)
_FEET = re.compile(r"""^\s*(\d+(?:\.\d+)?)\s*(?:'|ft)\s*(?:(\d+(?:\.\d+)?)\s*(?:"|in))?\s*$""", re.I)


def parse_width(value) -> float | None:
    """OSM `width` value in metres, or None if missing or unparseable.

    Accepts `3.5`, `3,5`, `3.5 m`, `12'`, `12'6"`, `12 ft`. Ranges, lists and
    other units are rejected, so the next rule in tag_width applies.
    """
    if value is None or (isinstance(value, float) and value != value):
        return None
    text = str(value)
    if m := _METRIC.match(text):
        w = float(m.group(1).replace(",", "."))
    elif m := _FEET.match(text):
        w = float(m.group(1)) * 0.3048 + float(m.group(2) or 0) * 0.0254
    else:
        return None
    return w if 0 < w < 100 else None


def parse_lanes(value) -> int | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    try:
        n = float(str(value).strip())
    except ValueError:
        return None
    return int(n) if n == int(n) and 0 < n < 20 else None


def is_oneway(tags: dict) -> bool:
    return tags.get("oneway") in ("yes", "1", "-1", "true") or tags.get("junction") in (
        "roundabout",
        "circular",
    )


def tag_width(tags: dict) -> tuple[float, str]:
    """(width in m, source) from OSM tags; source is `width`, `lanes` or `default`.

    Note: OSM `width` is kerb to kerb, incl. on-street parking and cycle lanes
    (OSM wiki Key:width), so it overestimates carriageway width where those exist.
    """
    highway = tags.get("highway", "")
    lane_w = config.LANE_WIDTH_M[highway.removesuffix("_link")]
    if (w := parse_width(tags.get("width"))) is not None:
        return w, "width"
    if (n := parse_lanes(tags.get("lanes"))) is not None:
        return n * lane_w, "lanes"
    lanes = 1 if highway == "service" or is_oneway(tags) else 2
    return lanes * lane_w, "default"
