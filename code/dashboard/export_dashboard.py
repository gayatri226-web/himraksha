"""HimRaksha - export one pipeline run folder into the dashboard.

Usage:  python export_dashboard.py <run_dir> <dashboard_dir> [--id trishuli] [--name "Trishuli, Nepal"]
                                    [--validation eval_emsr927.json] [--kind "debris flood"]

Reads the standard run outputs (impact_fused.tif, dem30.tif, osm_*.geojson, settlements_status.geojson,
bridges_hit.geojson, roads_hit.geojson, impact_polygons.geojson, repairs.csv, damage_stats.json,
access_stats.json, run_log.json, optional s2_before_rgb.png / s2_after_rgb.png) and writes
  <dashboard_dir>/data/<id>/data.js       all layers + numbers + situation-report text (window.HR_DATA)
  <dashboard_dir>/data/<id>/map.jpg       topographic base map (Copernicus DEM: elevation colours, relief, rivers)
  <dashboard_dir>/data/<id>/sat.jpg       Sentinel-2 true colour BEFORE the event (cloud gaps filled with the map)
  <dashboard_dir>/data/<id>/terrain.jpg   shaded relief
  <dashboard_dir>/data/<id>/ev_*.jpg      Sentinel-2 before / after / detected close-up (if Sentinel-2 was used)
  <dashboard_dir>/data/<id>/meta.json     name / date of the run
  <dashboard_dir>/data/events.js          list of all exported runs (rebuilt from every data/*/meta.json)
Copied an exported data/<id> folder in from elsewhere (e.g. Colab)?  python export_dashboard.py --manifest <dashboard_dir>
Everything is plain files: the dashboard opens from disk (double-click index.html), no server needed.
Inputs are only our own run outputs (Sentinel-1/2, Copernicus DEM, pre-event OSM). EMS numbers appear only if a
validation file is given, and only in the Validation tab.
"""
import argparse
import base64
import datetime as dt
import glob
import json
import os
import warnings

warnings.filterwarnings("ignore")


def _heavy():
    """geo libraries are only needed for exporting, not for rebuilding the event list"""
    global gpd, np, pd, rasterio, Image, from_origin, reproject, Resampling, ndimage, box
    import geopandas as gpd
    import numpy as np
    import pandas as pd
    import rasterio
    from PIL import Image
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, Resampling
    from scipy import ndimage
    from shapely.geometry import box
    Image.MAX_IMAGE_PIXELS = None
    if not hasattr(np, "in1d"):                   # numpy >= 2.4 removed in1d; pysheds still calls it
        np.in1d = np.isin

W = 1000                                          # map units across the area (all layers share this grid)
IMG = 2                                           # base-map pixels per map unit
FLOW_CELL = 2                                     # flood-path grid cell = 2 map units
CLASS = {1: "new water (radar)", 2: "debris, darkened (radar)", 3: "debris, brightened (radar)",
         4: "debris (optical only)", 5: "new water (optical only)"}
STATUS = {"cut off": "cut", "long detour": "detour", "unaffected": "ok"}
HW_WORD = {"primary": "main road", "trunk": "highway", "secondary": "road", "tertiary": "road", "unclassified": "local road",
           "residential": "village road", "track": "track", "service": "access road"}


def read(path, **kw):
    return gpd.read_file(path, **kw) if os.path.exists(path) else None


def jload(path, default=None):
    return json.load(open(path)) if os.path.exists(path) else default


class Grid:
    def __init__(self, bbox, crs):
        x0, y0, x1, y1 = gpd.GeoSeries([box(*bbox)], crs=4326).to_crs(crs).total_bounds
        self.x0, self.y1, self.crs = x0, y1, crs
        self.res = (x1 - x0) / W
        self.H = int(round((y1 - y0) / self.res))
        self.clip = box(x0, y0, x1, y1)

    def xy(self, x, y):
        return round((x - self.x0) / self.res, 1), round((self.y1 - y) / self.res, 1)

    def coords(self, geom, tol):
        """geometry -> list of parts; each part = list of rings/lines; each = [[x, y], ...] in map units"""
        g = geom.intersection(self.clip)
        if g.is_empty:
            return []
        g = g.simplify(tol * self.res)
        out = []
        for p in getattr(g, "geoms", [g]):
            if p.geom_type == "Polygon":
                rings = [p.exterior] + list(p.interiors)
                out.append([[list(self.xy(x, y)) for x, y in r.coords] for r in rings if len(r.coords) >= 4])
            elif p.geom_type == "LineString" and len(p.coords) >= 2:
                out.append([[list(self.xy(x, y)) for x, y in p.coords]])
        return [o for o in out if o]

    def warp(self, arr, transform, scale, resampling=None, nodata=float("nan")):
        resampling = Resampling.bilinear if resampling is None else resampling
        H, Wd = self.H * scale, W * scale
        out = np.full((H, Wd), nodata, "float32")
        reproject(arr.astype("float32"), out, src_transform=transform, src_crs=self.crs,
                  dst_transform=from_origin(self.x0, self.y1, self.res / scale, self.res / scale), dst_crs=self.crs,
                  resampling=resampling, src_nodata=nodata, dst_nodata=nodata)
        return out


def basemaps(run, grid, out):
    """topographic map, satellite (Sentinel-2 before) and terrain images + river mask + flood-path grids"""
    from pysheds.grid import Grid as PGrid
    dsrc = rasterio.open(f"{run}/dem30.tif")
    z = grid.warp(dsrc.read(1), dsrc.transform, IMG)
    z = np.where(np.isfinite(z), z, np.nanmean(z))
    pg = PGrid.from_raster(f"{run}/dem30.tif"); dem = pg.read_raster(f"{run}/dem30.tif")
    cond = pg.resolve_flats(pg.fill_depressions(pg.fill_pits(dem)))
    acc = np.asarray(pg.accumulation(pg.flowdir(cond)), "float32")
    A = grid.warp(acc, dsrc.transform, IMG, Resampling.max)
    res = grid.res / IMG
    gy, gx = np.gradient(z, res)
    slope, aspect = np.arctan(np.hypot(gx, gy)), np.arctan2(-gx, gy)
    alt, az = np.radians(42), np.radians(315)
    hs = np.clip(np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect), 0, 1)
    stops = [(300, (168, 205, 150)), (1000, (190, 214, 160)), (1800, (214, 214, 170)), (2700, (205, 190, 160)),
             (3600, (190, 182, 175)), (4500, (232, 232, 236)), (6000, (250, 250, 252))]
    zs, cs = np.array([s[0] for s in stops], float), np.array([s[1] for s in stops], float)
    tint = np.stack([np.interp(z, zs, cs[:, k]) for k in range(3)], -1)
    topo = tint * (0.62 + 0.38 * hs[..., None])
    akm = A * (abs(dsrc.transform.a) * abs(dsrc.transform.e)) / 1e6
    river = np.zeros(z.shape, np.uint8)
    for thr, w in [(5, 1), (60, 2), (400, 3), (2000, 4)]:
        m = akm >= thr
        if w > 1:
            m = ndimage.binary_dilation(m, iterations=w - 1)
        river = np.maximum(river, m.astype(np.uint8) * w)
    blue, blue2 = np.array([74, 140, 220.]), np.array([46, 110, 205.])
    mp = topo.copy()
    mp[river == 1] = mp[river == 1] * 0.35 + blue * 0.65
    mp[river >= 2] = blue2
    Image.fromarray(np.clip(mp, 0, 255).astype(np.uint8)).save(f"{out}/map.jpg", quality=84)
    shade = np.array([226, 222, 232.]) * (0.45 + 0.55 * hs[..., None])
    Image.fromarray(np.clip(shade, 0, 255).astype(np.uint8)).save(f"{out}/terrain.jpg", quality=82)
    sat_ok = False
    if os.path.exists(f"{run}/s2_before_rgb.png"):
        with rasterio.open(f"{run}/impact_fused.tif") as s:
            tr = s.transform
        b = np.array(Image.open(f"{run}/s2_before_rgb.png").convert("RGB")).astype("float32")
        sat = np.stack([grid.warp(b[..., k], tr, IMG) for k in range(3)], -1)
        gap = ~np.isfinite(sat[..., 0]) | (np.abs(sat - 217).sum(-1) < 8)
        if os.path.exists(f"{run}/s2_after_rgb.png"):          # fill cloud / shadow gaps from the after-image first
            a_ = np.array(Image.open(f"{run}/s2_after_rgb.png").convert("RGB")).astype("float32")
            aft = np.stack([grid.warp(a_[..., k], tr, IMG) for k in range(3)], -1)
            agap = ~np.isfinite(aft[..., 0]) | (np.abs(aft - 217).sum(-1) < 8)
            use = gap & ~agap
            sat[use] = aft[use]; gap = gap & agap
        dark = np.array([78, 70, 62.]) * (0.6 + 0.9 * hs[..., None])   # what is left is mostly terrain shadow: shade it dark
        sat = np.where(gap[..., None], dark, sat)
        sat = np.clip((sat - 10) * 1.12, 0, 255)
        sat[river >= 3] = sat[river >= 3] * 0.4 + blue * 0.6
        Image.fromarray(sat.astype(np.uint8)).save(f"{out}/sat.jpg", quality=82)
        sat_ok = True
    # flood-path grids (D8 on the DEM resampled to FLOW_CELL map units): direction codes + elevation
    zf = grid.warp(dsrc.read(1), dsrc.transform, 1)[::FLOW_CELL, ::FLOW_CELL] if FLOW_CELL > 1 else grid.warp(dsrc.read(1), dsrc.transform, 1)
    zf = np.where(np.isfinite(zf), zf, np.nanmax(zf[np.isfinite(zf)]) if np.isfinite(zf).any() else 0)
    tmp = f"{out}/_flowdem.tif"
    fr = from_origin(grid.x0, grid.y1, grid.res * FLOW_CELL, grid.res * FLOW_CELL)
    with rasterio.open(tmp, "w", driver="GTiff", width=zf.shape[1], height=zf.shape[0], count=1, dtype="float32",
                       crs=grid.crs, transform=fr) as d:
        d.write(zf.astype("float32"), 1)
    pg2 = PGrid.from_raster(tmp); d2 = pg2.read_raster(tmp)
    c2 = pg2.resolve_flats(pg2.fill_depressions(pg2.fill_pits(d2)))
    fdir = np.asarray(pg2.flowdir(c2, dirmap=(64, 128, 1, 2, 4, 8, 16, 32)))
    os.remove(tmp)
    code = {64: 1, 128: 2, 1: 3, 2: 4, 4: 5, 8: 6, 16: 7, 32: 8}           # 1..8 = N NE E SE S SW W NW, 0 = none
    f8 = np.zeros(fdir.shape, np.uint8)
    for k, v in code.items():
        f8[fdir == k] = v
    flow = {"cell": FLOW_CELL, "w": int(zf.shape[1]), "h": int(zf.shape[0]),
            "dir": base64.b64encode(f8.tobytes()).decode(),
            "elev": base64.b64encode(np.clip(np.round(zf), -500, 9000).astype("<i2").tobytes()).decode()}
    return sat_ok, flow


def evidence(run, out):
    """close-up of the strongest radar + optical agreement: before / after / detected (needs Sentinel-2)"""
    if not (os.path.exists(f"{run}/s2_before_rgb.png") and os.path.exists(f"{run}/s2_after_rgb.png")):
        return None
    src = rasterio.open(f"{run}/impact_fused.tif")
    if src.count < 3:
        return None
    cls, srcb = src.read(1), src.read(3)
    b = np.array(Image.open(f"{run}/s2_before_rgb.png").convert("RGB"))
    a = np.array(Image.open(f"{run}/s2_after_rgb.png").convert("RGB"))
    both = srcb == 3
    if not both.any():
        both = np.isin(cls, [1, 2, 3, 4, 5])
    lab, n = ndimage.label(ndimage.binary_dilation(both, iterations=10))
    if n == 0:
        return None
    sz = ndimage.sum(both, lab, range(1, n + 1)); k = 110; best = None
    for i in np.argsort(sz)[::-1][:20]:
        rr, cc = np.where(lab == i + 1); r, c = int(rr.mean()), int(cc.mean())
        win = a[max(r - k, 0):r + k, max(c - k, 0):c + k]
        cloud = (np.abs(win.astype(int) - 217).sum(-1) < 6).mean()
        score = cloud - 0.000002 * sz[i]
        if best is None or score < best[0]:
            best = (score, r, c)
    _, r, c = best
    sl = (slice(max(r - k, 0), r + k), slice(max(c - k, 0), c + k))
    hit = np.isin(cls[sl], [1, 2, 3, 4, 5])
    ov = a[sl].copy(); edge = hit ^ ndimage.binary_erosion(hit, iterations=2); ov[edge] = [226, 40, 190]
    for nm, arr in [("before", b[sl]), ("after", a[sl]), ("detected", ov)]:
        Image.fromarray(arr).resize((360, 360), Image.LANCZOS).save(f"{out}/ev_{nm}.jpg", quality=88)
    x, y = src.xy(r, c)
    return {"x": x, "y": y}


def sitrep(d):
    """priority actions + notes, computed from the run's numbers (no free text from a model)"""
    acts, notes = [], []
    st = d["settlements"]; reps = d["repairs"]; brs = d["bridges"]
    if reps:
        r = reps[0]
        kind = HW_WORD.get(r["hw"], "road")
        first = str(r["names"]).split(",")[0].strip()
        nm = r["name"] if r["name"] != "(unnamed)" else f"the unnamed {kind} near {first}"
        bl = f", ~{r['b']:,} buildings" if r["b"] else ""
        acts.append(f"Reopen {nm} ({r['m']:,} m damaged) — reconnects {r['s']} settlement{'s' if r['s'] > 1 else ''}{bl}: {r['names']}.")
    nofoot = sorted([s for s in st if s["s"] == "cut" and not s["foot"]], key=lambda s: -s["b"])
    if nofoot:
        names = ", ".join(s["n"] or "(unnamed)" for s in nofoot[:3])
        more = f" and {len(nofoot) - 3} more" if len(nofoot) > 3 else ""
        acts.append(f"{names}{more}: no road and no footpath to a health facility — plan air or river access.")
    foot = [s for s in st if s["s"] == "cut" and s["foot"]]
    if foot:
        acts.append(f"{len(foot)} cut-off settlement{'s are' if len(foot) > 1 else ' is'} still reachable on foot — send teams "
                    "along footpaths while roads are repaired.")
    poss = [b for b in brs if b["s"] == "possibly damaged"]
    if poss:
        named = [b["n"] for b in poss if b["n"]][:2]
        acts.append(f"Ground-check the {len(poss)} bridge{'s' if len(poss) > 1 else ''} graded 'possibly damaged'"
                    + (f" (incl. {', '.join(named)})" if named else "") + " before use.")
    if not [s for s in st if s["s"] in ("cut", "detour")]:
        acts.append("No settlement lost its road connection to a health facility or town in this area.")
    k = d["kpi"]
    if k.get("osm_buildings", 0) < 1000 or k.get("no_road", 0) > 0.4 * max(k.get("settlements", 1), 1):
        notes.append("OpenStreetMap is sparse here: buildings and roads missing from OSM are not counted.")
    if d.get("s2") and d["s2"].get("used") and d["s2"].get("valley_clear_both_dates_pct", 100) < 60:
        notes.append(f"Clouds: Sentinel-2 saw only {d['s2']['valley_clear_both_dates_pct']:.0f}% of the valley; radar carries the map.")
    notes.append("Narrow gorges are under-mapped by radar (steep slopes hide the valley floor).")
    notes.append("Satellites revisit every few days — this is a damage map, not an early warning. Casualties are not estimated.")
    return {"actions": acts, "notes": notes}


def rebuild_manifest(dash):
    """data/events.js = every exported event found in data/*/meta.json (so folders can be copied in from anywhere)"""
    events = []
    for m in sorted(glob.glob(f"{dash}/data/*/meta.json")):
        if os.path.exists(os.path.join(os.path.dirname(m), "data.js")):
            events.append(json.load(open(m)))
    events.sort(key=lambda e: str(e.get("event", "")), reverse=True)
    open(f"{dash}/data/events.js", "w").write("window.HR_EVENTS = " + json.dumps(events, indent=1) + ";\n")
    return events


def export(run, dash, eid, name, kind, validation=None, short=None):
    _heavy()
    out = f"{dash}/data/{eid}"; os.makedirs(out, exist_ok=True)
    log = jload(f"{run}/run_log.json", {})
    dmg = jload(f"{run}/damage_stats.json", log.get("damage", {}))
    acc = jload(f"{run}/access_stats.json", log.get("access", {}))
    with rasterio.open(f"{run}/impact_fused.tif") as s:
        crs = s.crs
    grid = Grid(log["bbox"], crs)
    sat_ok, flow = basemaps(run, grid, out)
    ev_center = evidence(run, out)

    d = {"id": eid, "name": name, "kind": kind, "event": log.get("event"), "bbox": log["bbox"], "W": W, "H": grid.H,
         "m_per_unit": round(grid.res, 2), "crs": str(crs), "sat": sat_ok, "flow": flow,
         "orbits": [{k: o.get(k) for k in ("orbit", "direction", "pre", "post", "days_after_event")} for o in log.get("orbits", [])],
         "s2": {k: log.get("s2", {}).get(k) for k in ("used", "valley_clear_both_dates_pct", "total_impacted_km2", "conf_high_km2",
                                                     "radar_and_optical_agree_km2", "optical_only_added_km2")},
         "s2_dates": {"pre": [p["date"] for p in log.get("s2", {}).get("pre", [])], "post": [p["date"] for p in log.get("s2", {}).get("post", [])]},
         "osm_snapshot": dmg.get("osm_snapshot")}
    # impact polygons (clickable)
    imp = read(f"{run}/impact_polygons.geojson")
    feats = []
    if imp is not None and len(imp):
        imp = imp.to_crs(crs)
        for _, r in imp.iterrows():
            g = r.geometry.buffer(grid.res * 0.5)
            parts = grid.coords(g, 0.5)
            if parts:
                cls = r["impact"]
                grp = "optical" if "optical" in cls else ("water" if "water" in cls else "debris")
                feats.append({"g": grp, "c": r["confidence"], "t": cls, "a": round(float(r["area_m2"]) / 1e4, 2), "p": parts})
    d["impact"] = feats
    # roads: network (major/minor) + affected (clickable)
    rd = read(f"{run}/osm_roads.geojson").to_crs(crs)
    hit = read(f"{run}/roads_hit.geojson")
    hitkm = {}
    if hit is not None and len(hit):
        hit = hit.to_crs(crs)
        hitkm = hit.groupby(hit["@osmId"].astype(str))["km_affected"].sum().to_dict()
    major = {"motorway", "trunk", "primary", "secondary", "tertiary", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
    roads = []
    for _, r in rd.iterrows():
        hw = str(r.get("highway"))
        parts = grid.coords(r.geometry, 1.0 if hw in major else 1.5)
        if not parts:
            continue
        oid = str(r.get("@osmId"))
        roads.append({"n": r.get("name") if isinstance(r.get("name"), str) else "", "hw": hw, "M": hw in major,
                      "k": round(float(hitkm.get(oid, 0)), 2), "p": [q[0] for q in parts]})
    d["roads"] = roads
    d["roads_hit"] = []
    if hit is not None and len(hit):
        for _, r in hit.iterrows():
            parts = grid.coords(r.geometry, 0.8)
            if parts:
                d["roads_hit"].append({"n": r.get("name") if isinstance(r.get("name"), str) else "", "hw": str(r.get("highway")),
                                       "k": round(float(r["km_affected"]), 2), "c": r.get("confidence"), "p": [q[0] for q in parts]})
    # settlements
    stt = read(f"{run}/settlements_status.geojson").to_crs(crs)
    S = []
    for _, r in stt.iterrows():
        x, y = grid.xy(r.geometry.x, r.geometry.y)
        if not (0 <= x <= W and 0 <= y <= grid.H):
            continue
        nm = r.get("name") if isinstance(r.get("name"), str) and r.get("name") != "(unnamed)" else ""
        S.append({"n": nm, "loc": r.get("name_local") if isinstance(r.get("name_local"), str) else "", "pl": r.get("place"),
                  "x": x, "y": y, "s": STATUS.get(r["status_all_detections"], "na"), "sc": STATUS.get(r["status_confident_only"], "na"),
                  "raw": r["status_all_detections"], "b": int(r.get("buildings_500m") or 0),
                  "pre": None if pd.isna(r.get("minutes_pre")) else float(r["minutes_pre"]),
                  "post": None if pd.isna(r.get("minutes_post")) else float(r["minutes_post"]),
                  "foot": bool(r.get("reachable_on_foot")), "out": r.get("road_out_of_area")})
    d["settlements"] = S
    h = read(f"{run}/osm_health.geojson")
    d["health"] = []
    if h is not None and len(h):
        h = h.to_crs(crs)
        for _, r in h.iterrows():
            p = r.geometry.representative_point(); x, y = grid.xy(p.x, p.y)
            if 0 <= x <= W and 0 <= y <= grid.H:
                kind_ = r.get("amenity") if isinstance(r.get("amenity"), str) else (r.get("healthcare") if isinstance(r.get("healthcare"), str) else "health facility")
                d["health"].append({"n": r.get("name") if isinstance(r.get("name"), str) else "Health facility", "k": kind_, "x": x, "y": y})
    b = read(f"{run}/bridges_hit.geojson")
    d["bridges"] = []
    if b is not None and len(b):
        b = b.to_crs(crs)
        for _, r in b.iterrows():
            p = r.geometry.representative_point(); x, y = grid.xy(p.x, p.y)
            d["bridges"].append({"n": r.get("name") if isinstance(r.get("name"), str) else "", "hw": r.get("highway"),
                                 "k": r.get("kind", ""), "s": r.get("status", "likely damaged"), "c": r.get("confidence"),
                                 "deck": float(r.get("deck_impact_pct", 0) or 0), "ea": int(r.get("debris_px_end_a", 0) or 0),
                                 "eb": int(r.get("debris_px_end_b", 0) or 0), "x": x, "y": y})
    reps = pd.read_csv(f"{run}/repairs.csv") if os.path.exists(f"{run}/repairs.csv") else pd.DataFrame()
    d["repairs"] = []
    for _, r in reps.iterrows():
        p = gpd.GeoSeries.from_xy([r["lon"]], [r["lat"]], crs=4326).to_crs(crs)[0]; x, y = grid.xy(p.x, p.y)
        d["repairs"].append({"rank": int(r["rank"]), "name": r["name"], "hw": r["highway"], "bridge": bool(r["is_bridge"]),
                             "m": int(r["damaged_m"]), "s": int(r["settlements_reconnected"]), "b": int(r["buildings_reconnected"]),
                             "names": r["names"], "x": x, "y": y})
    scen = acc.get("scenarios", {})
    area = d["s2"]["total_impacted_km2"] if d["s2"].get("used") else log.get("stats", {}).get("total_impacted_km2")
    area_hi = d["s2"]["conf_high_km2"] if d["s2"].get("used") else log.get("stats", {}).get("conf_high_km2")
    d["kpi"] = {"area": area, "area_hi": area_hi, "bld": dmg.get("buildings_likely_hit"), "bld_hi": dmg.get("buildings_hit_high_conf"),
                "osm_buildings": dmg.get("buildings_total"), "road_km": dmg.get("road_km_likely_affected"), "road_total": dmg.get("road_km_total"),
                "br_likely": dmg.get("bridges_likely_damaged"), "br_possibly": dmg.get("bridges_possibly_damaged"), "br_total": dmg.get("bridges_total"),
                "cut": scen.get("all_detections", {}).get("cut off", 0), "cut_hi": scen.get("confident_only", {}).get("cut off", 0),
                "detour": scen.get("all_detections", {}).get("long detour", 0), "detour_hi": scen.get("confident_only", {}).get("long detour", 0),
                "people": acc.get("buildings_near_cut_off_settlements"), "foot": acc.get("cut_off_but_reachable_on_foot"),
                "settlements": acc.get("settlements"), "no_road": scen.get("all_detections", {}).get("no road within 500 m", 0),
                "health": acc.get("destinations", {}).get("health_facilities"), "towns": acc.get("destinations", {}).get("towns")}
    d["sitrep"] = sitrep(d)
    if validation and os.path.exists(validation):
        v = json.load(open(validation)); t = v["total_3_aois"]
        d["validation"] = {"ref": "Copernicus EMS EMSR927 grading maps (photo-interpretation of very-high-resolution optical images)",
                           "rows": [["Affected area (pixels)", t["area_all_detections"]["precision"], t["area_all_detections"]["recall"], t["area_all_detections"]["f1"]],
                                    ["Area — high confidence only", t["area_high_confidence"]["precision"], t["area_high_confidence"]["recall"], t["area_high_confidence"]["f1"]],
                                    [f"Damaged buildings ({t['buildings']['ems_damaged']:,} in EMS)", t["buildings"]["precision"], t["buildings"]["recall"], None],
                                    [f"Damaged roads ({t['roads']['ems_damaged_km']:.0f} km in EMS)", t["roads"]["precision"], t["roads"]["recall"], None],
                                    ["Bridges — likely damaged", t["bridges"].get("precision"), t["bridges"].get("recall"), None]],
                           "aois": [[n, a["area_all_detections"]["f1"], a["buildings"]["recall"]] for n, a in v["aois"].items()]}
    if ev_center:
        d["evidence"] = True
    js = "window.HR_DATA = window.HR_DATA || {};\nwindow.HR_DATA[" + json.dumps(eid) + "] = " + \
         json.dumps(d, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o)) + ";\n"
    open(f"{out}/data.js", "w").write(js)
    json.dump({"id": eid, "name": name, "short": short or name.split(",")[0], "event": log.get("event"), "kind": kind,
               "exported": dt.datetime.now().isoformat(timespec="minutes")}, open(f"{out}/meta.json", "w"), indent=1)
    rebuild_manifest(dash)
    print(f"exported {eid}: {len(feats)} impact polygons, {len(roads)} roads, {len(S)} settlements, "
          f"{len(d['bridges'])} bridges, {len(d['health'])} health facilities, data.js {os.path.getsize(out + '/data.js') / 1e6:.1f} MB")
    return d


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--manifest":          # python export_dashboard.py --manifest <dashboard_dir>
        ev = rebuild_manifest(sys.argv[2]); print("events:", ", ".join(e["id"] for e in ev)); raise SystemExit
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dir"); ap.add_argument("dashboard_dir")
    ap.add_argument("--id"); ap.add_argument("--name"); ap.add_argument("--kind", default="Flood / debris flow")
    ap.add_argument("--validation"); ap.add_argument("--short", help="short label for the event switcher")
    a = ap.parse_args()
    log = jload(f"{a.run_dir}/run_log.json", {})
    eid = a.id or ("run_" + str(log.get("event", "")).replace("-", ""))
    export(a.run_dir, a.dashboard_dir, eid, a.name or eid, a.kind, a.validation, a.short)
