"""Bridge damage grading from the fused impact map (no EMS / no post-event data).

Why: radar sees the river UNDER a bridge, so "any impact pixel near the bridge" flags almost every bridge
over a flooded river. A bridge is only called LIKELY DAMAGED when the evidence is on the structure itself:
  1. at least half of the deck footprint (bridge line + 15 m) shows impact, AND
  2. debris (not just water) is detected at BOTH ends (30 m around each end, deck excluded)
     -> the flow overtopped the crossing and hit both abutments / approach roads.
Anything with impact within 30 m that fails this test is POSSIBLY DAMAGED (needs a ground check).
Confidence: likely + high-confidence pixels on the deck = high, likely otherwise = medium, possibly = low.
Bridges are also split by use: vehicle / foot / other (aqueducts, untagged).
"""
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from shapely.geometry import Point
from shapely.ops import linemerge

DECK_BUF_M, END_R_M, NEAR_M = 15, 30, 30
DECK_MIN_FRAC, END_MIN_PX = 0.5, 5            # 5 px = 500 m2 of debris at each end
VEHICLE = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "service", "track",
           "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "living_street"}
FOOT = {"path", "footway", "steps", "bridleway", "cycleway", "pedestrian"}


def _pixels(src, geom):
    w = from_bounds(*geom.bounds, transform=src.transform).round_offsets().round_lengths()
    w = Window(w.col_off - 1, w.row_off - 1, w.width + 2, w.height + 2)
    c = src.read(1, window=w, boundless=True, fill_value=255)
    f = src.read(2, window=w, boundless=True, fill_value=0)
    m = geometry_mask([geom], c.shape, src.window_transform(w), invert=True, all_touched=True)
    return c[m], f[m]


def _ends(g):
    if g.geom_type == "MultiLineString":
        g = linemerge(g)
    if g.geom_type == "LineString":
        return Point(g.coords[0]), Point(g.coords[-1])
    pts = [Point(c) for part in getattr(g, "geoms", [g]) for c in (part.coords[0], part.coords[-1])]
    if len(pts) < 2:
        return g.centroid, g.centroid
    a = pts[0]; b = max(pts, key=a.distance)          # the two ends farthest apart
    return a, b


def grade_bridges(bridges, impact_tif):
    """bridges: GeoDataFrame of OSM bridge lines (any CRS). Returns a copy in the raster CRS with columns
    kind, deck_impact_pct, debris_px_end_a, debris_px_end_b, status, confidence."""
    src = rasterio.open(impact_tif)
    g = bridges.to_crs(src.crs).copy()
    hw = g["highway"] if "highway" in g.columns else gpd.pd.Series(index=g.index, dtype=object)
    g["kind"] = ["vehicle" if h in VEHICLE else "foot" if h in FOOT else "other" for h in hw]
    rows = []
    for geom in g.geometry:
        c, f = _pixels(src, geom.buffer(DECK_BUF_M))
        valid = c != 255
        hit = np.isin(c, [1, 2, 3, 4, 5])
        deck = hit.sum() / max(valid.sum(), 1)
        deck_hi = int((hit & (f == 3)).sum())
        ends = []
        for p in _ends(geom):
            ce, _ = _pixels(src, p.buffer(END_R_M).difference(geom.buffer(5)))
            ends.append(int(np.isin(ce, [2, 3, 4]).sum()))
        cn, _ = _pixels(src, geom.buffer(NEAR_M))
        near = bool(np.isin(cn, [1, 2, 3, 4, 5]).any())
        likely = deck >= DECK_MIN_FRAC and min(ends) >= END_MIN_PX
        status = "likely damaged" if likely else "possibly damaged" if near else "no evidence"
        conf = ("high" if deck_hi > 0 else "medium") if likely else "low" if near else None
        rows.append((round(100 * deck, 1), ends[0], ends[1], status, conf))
    g["deck_impact_pct"], g["debris_px_end_a"], g["debris_px_end_b"], g["status"], g["confidence"] = map(list, zip(*rows)) \
        if rows else ([], [], [], [], [])
    return g
