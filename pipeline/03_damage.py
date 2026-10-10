# Track B - what was damaged: pre-event OpenStreetMap (ohsome) x impact map
# Run in the same Colab session after trishuli_map_v1.py (uses OUT / run folder with impact_fused.tif + run_log.json)
# Data rule: OSM snapshot is ALWAYS before the event date (checked below). No post-event OSM, no EMS/UNOSAT.
!pip install -q geopandas shapely rasterio requests

import os, glob, json, time, datetime as dt
import numpy as np, pandas as pd, requests, rasterio, geopandas as gpd, matplotlib.pyplot as plt
from rasterio.features import shapes, rasterize
from shapely.geometry import shape

RUN = OUT if "OUT" in globals() else sorted(glob.glob("run_*"))[-1]
log = json.load(open(f"{RUN}/run_log.json"))
BBOX, EVENT = log["bbox"], log["event"]
OHSOME = "https://api.ohsome.org/v1"
BUILDING_BUF_M, ROAD_BUF_M, BRIDGE_BUF_M = 10, 10, 30
CONF_NAME = {3: "high", 2: "medium", 1: "low"}
HEADERS = {"User-Agent": "TrackB-flood-mapping/1.0 (student hackathon project; Multimodal AI Hackathon 2026)"}


def say(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------- 1. pre-event snapshot date (data rule) ----------
if "OSM_SNAPSHOT" in globals():                       # set in the config cell (ohsome v2 snapshot used for download)
    SNAP = OSM_SNAPSHOT
else:
    meta = requests.get(f"{OHSOME}/metadata", headers=HEADERS, timeout=120).json()
    latest = meta["extractRegion"]["temporalExtent"]["toTimestamp"][:10]
    SNAP = str(min(dt.date.fromisoformat(latest), dt.date.fromisoformat(EVENT) - dt.timedelta(days=1)))
assert dt.date.fromisoformat(SNAP) < dt.date.fromisoformat(EVENT), "OSM snapshot must be BEFORE the event"
say(f"pre-event OSM snapshot {SNAP}")

LAYERS = {
    "buildings":   ("building=* and building!=no and geometry:polygon", 3),
    "roads":       ("highway in (motorway,trunk,primary,secondary,tertiary,unclassified,residential,service,track,"
                    "motorway_link,trunk_link,primary_link,secondary_link,tertiary_link) and geometry:line", 2),
    "footpaths":   ("highway in (path,footway,steps,bridleway) and geometry:line", 2),
    "bridges":     ("bridge=* and bridge!=no and geometry:line", 1),
    "settlements": ("place in (city,town,village,hamlet,isolated_dwelling)", 1),
    "health":      ("amenity in (hospital,clinic,doctors) or healthcare=*", 1),
}


def ohsome_post(flt, bb, clip):
    data = {"bboxes": ",".join(f"{v:.5f}" for v in bb), "time": SNAP, "filter": flt, "properties": "tags"}
    if not clip:
        data["clipGeometry"] = "false"
    return requests.post(f"{OHSOME}/elements/geometry", data=data, headers=HEADERS, timeout=600)


def get_tile(name, flt, bb, depth, clip=False):
    """Fetch one tile. Busy/too big -> wait or split into 4 smaller tiles. Bad request -> show why."""
    label = f"{name} [{bb[0]:.3f},{bb[1]:.3f},{bb[2]:.3f},{bb[3]:.3f}]"
    for attempt in range(1, 3):
        try:
            r = ohsome_post(flt, bb, clip)
        except requests.exceptions.RequestException as ex:          # timeout / connection reset
            say(f"   {label}: {type(ex).__name__} (attempt {attempt})"); time.sleep(15 * attempt); continue
        if r.status_code == 200:
            return r.json().get("features", [])
        body = r.text[:300].replace("\n", " ")
        say(f"   {label}: HTTP {r.status_code} - {body}")
        if r.status_code == 400 and not clip:                         # parameter problem -> retry with default clipping
            return get_tile(name, flt, bb, depth, clip=True)
        if r.status_code in (400, 401, 403, 404):
            raise RuntimeError(f"ohsome rejected the request for {label}: HTTP {r.status_code} {body}")
        time.sleep(30 * attempt)                                      # 413 / 429 / 5xx -> wait, then split
    if depth >= 3:
        raise RuntimeError(f"ohsome kept failing for {label} even after splitting - try again later")
    say(f"   splitting {label} into 4 smaller tiles")
    mx, my = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    out = []
    for sub in [(bb[0], bb[1], mx, my), (mx, bb[1], bb[2], my), (bb[0], my, mx, bb[3]), (mx, my, bb[2], bb[3])]:
        out += get_tile(name, flt, sub, depth + 1, clip)
    return out


def fetch(name, flt, tiles):
    """Download one OSM layer from ohsome, tiled + retried + cached. Returns GeoDataFrame (EPSG:4326)."""
    path = f"{RUN}/osm_{name}.geojson"
    if os.path.exists(path):
        say(f"cached {name}"); return gpd.read_file(path)
    w, s, e, n = BBOX
    xs, ys = np.linspace(w, e, tiles + 1), np.linspace(s, n, tiles + 1)
    feats = []
    for i in range(tiles):
        for j in range(tiles):
            feats += get_tile(name, flt, (xs[i], ys[j], xs[i + 1], ys[j + 1]), depth=0)
    if feats:
        gdf = gpd.GeoDataFrame.from_features(feats, crs=4326)
        gdf = gdf.drop_duplicates("@osmId").reset_index(drop=True)
    else:
        gdf = gpd.GeoDataFrame({"@osmId": []}, geometry=[], crs=4326)
    gdf.to_file(path, driver="GeoJSON")
    say(f"{name}: {len(gdf)} features")
    return gdf


osm = {name: fetch(name, flt, tiles) for name, (flt, tiles) in LAYERS.items()}

# ---------- 2. impact map -> polygons with class + confidence ----------
with rasterio.open(f"{RUN}/impact_fused.tif") as src:
    cls, conf = src.read(1), src.read(2)
    tr, crs = src.transform, src.crs
hit = np.isin(cls, [1, 2, 3])
geoms, vals = [], []
for g, v in shapes(cls.astype(np.uint8), mask=hit, transform=tr):
    geoms.append(shape(g)); vals.append(int(v))
imp = gpd.GeoDataFrame({"impact_class": vals}, geometry=geoms, crs=crs)
idx = rasterize(((g, i + 1) for i, g in enumerate(imp.geometry)), out_shape=cls.shape, transform=tr, dtype="int32")
m = idx > 0
conf0 = np.where(conf == 255, 0, conf)
best = pd.DataFrame({"i": idx[m] - 1, "c": conf0[m]}).groupby("i")["c"].max()
imp["conf"] = best.reindex(range(len(imp))).fillna(0).astype(int).values
imp["impact"] = imp["impact_class"].map({1: "new water", 2: "debris (darkened)", 3: "debris (brightened)"})
imp["confidence"] = imp["conf"].map(CONF_NAME).fillna("low")
imp["area_m2"] = imp.area
say(f"impact polygons: {len(imp)}")


def union(gs):
    return gs.union_all() if hasattr(gs, "union_all") else gs.unary_union


def tag_hits(gdf, buf_m):
    """Return copy of gdf (in UTM) with hit flag + best confidence of nearby impact."""
    g = gdf.to_crs(crs).copy()
    if g.empty:
        g["hit"] = False; g["conf"] = 0; return g
    zone = imp[["conf", "geometry"]].copy(); zone["geometry"] = zone.buffer(buf_m)
    j = gpd.sjoin(g[["geometry"]], zone, how="left", predicate="intersects")
    best = j.groupby(level=0)["conf"].max()
    g["conf"] = best.reindex(g.index).fillna(0).astype(int)
    g["hit"] = g["conf"] > 0
    g["confidence"] = g["conf"].map(CONF_NAME)
    return g


# ---------- 3. buildings, roads, bridges ----------
b = tag_hits(osm["buildings"], BUILDING_BUF_M)
br = tag_hits(osm["bridges"], BRIDGE_BUF_M)
rd = tag_hits(osm["roads"], ROAD_BUF_M)
fp = tag_hits(osm["footpaths"], ROAD_BUF_M)

zone_road = union(imp.buffer(ROAD_BUF_M))
rd_hit = rd[rd["hit"]].copy()
rd_hit["geometry"] = rd_hit.intersection(zone_road)
rd_hit["km_affected"] = rd_hit.length / 1000
km_by_class = rd_hit.groupby("highway")["km_affected"].sum().sort_values(ascending=False).round(2)

stats = {
    "osm_snapshot": SNAP,
    "buildings_total": int(len(b)), "buildings_likely_hit": int(b["hit"].sum()),
    "buildings_hit_high_conf": int((b["conf"] == 3).sum()),
    "road_km_total": round(float(rd.length.sum() / 1000), 1),
    "road_km_likely_affected": round(float(rd_hit["km_affected"].sum()), 2),
    "road_segments_likely_affected": int(rd["hit"].sum()),
    "footpath_segments_likely_affected": int(fp["hit"].sum()),
    "bridges_total": int(len(br)), "bridges_likely_damaged": int(br["hit"].sum()),
    "bridges_damaged_high_conf": int((br["conf"] == 3).sum()),
    "road_km_affected_by_class": km_by_class.to_dict(),
}

# ---------- 4. save for dashboard + cut-off analysis (EPSG:4326 GeoJSON) ----------
def save(gdf, name, cols):
    keep = [c for c in cols if c in gdf.columns] + ["geometry"]
    gdf[keep].to_crs(4326).to_file(f"{RUN}/{name}.geojson", driver="GeoJSON")

save(imp, "impact_polygons", ["impact", "confidence", "area_m2"])
save(b[b["hit"]], "buildings_hit", ["@osmId", "building", "name", "confidence"])
save(rd_hit, "roads_hit", ["@osmId", "highway", "name", "ref", "confidence", "km_affected"])
save(br[br["hit"]], "bridges_hit", ["@osmId", "highway", "bridge", "name", "confidence"])
json.dump(stats, open(f"{RUN}/damage_stats.json", "w"), indent=2)
log["damage"] = stats; json.dump(log, open(f"{RUN}/run_log.json", "w"), indent=2)

print("\n================ DAMAGE SUMMARY (likely hit, pre-event OSM", SNAP, ") ================")
for k, v in stats.items():
    if k != "road_km_affected_by_class":
        print(f"{k:<34} {v}")
print("road km affected by class:", stats["road_km_affected_by_class"])
named = br[br["hit"]].copy()
if len(named):
    named["label"] = named.get("name", pd.Series(index=named.index, dtype=object)).fillna("(unnamed)")
    print("\nBridges likely damaged:")
    for _, r in named.sort_values("conf", ascending=False).iterrows():
        print(f"  - {r['label']:<35} highway={r.get('highway')} confidence={r['confidence']}")

# ---------- 5. map ----------
fig, ax = plt.subplots(1, 2, figsize=(22, 11))
zoom = (314000, 3087000, 324500, 3099000)       # lower Trishuli corridor (UTM 45N, metres)
for axis, title, lim in [(ax[0], "Full area", None), (ax[1], "Zoom: lower Trishuli corridor", zoom)]:
    rd.plot(ax=axis, color="#bbbbbb", linewidth=0.6)
    imp.plot(ax=axis, column="impact", categorical=True, cmap="copper", alpha=0.8, legend=(lim is None))
    if b["hit"].any():
        b[b["hit"]].centroid.plot(ax=axis, color="red", markersize=4, label="building likely hit")
    if len(rd_hit):
        rd_hit.plot(ax=axis, color="black", linewidth=2, label="road affected")
    if br["hit"].any():
        br[br["hit"]].centroid.plot(ax=axis, color="magenta", marker="*", markersize=180, label="bridge likely damaged")
    if lim:
        axis.set_xlim(lim[0], lim[2]); axis.set_ylim(lim[1], lim[3])
    axis.set_title(title); axis.legend(loc="lower right")
plt.tight_layout(); plt.savefig(f"{RUN}/damage_map.png", dpi=110); plt.show()
print("© OpenStreetMap contributors | Contains modified Copernicus Sentinel data 2026.")
