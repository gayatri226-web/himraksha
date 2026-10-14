"""HimRaksha - BONUS: flood path from any upstream point.

"Given any point upstream, trace the flood path down the valley using elevation data and list the settlements along it."

How:
  1. Copernicus DEM GLO-30 (already made by the mapping step: dem30.tif) -> fill pits / depressions, resolve flats
  2. D8 flow direction + flow accumulation (pysheds)
  3. the clicked point is snapped to the nearest river channel (largest flow accumulation within 600 m),
     so a click on a hillside still starts in the valley
  4. follow the steepest-descent direction cell by cell until the edge of the DEM
  5. exposure corridor = ground within 1 km of the channel and at most 30 m above it (a height-above-channel test,
     the same idea as HAND). Every pre-event OSM building is tested; a settlement is listed when buildings within
     500 m of it lie in the corridor:
        exposure "high"   = some of those buildings are at most 10 m above the channel
        exposure "medium" = all of them are 10-30 m above it
     ordered by distance down the path (km from the start point), with the number of exposed buildings
  6. if an impact map exists, it reports how much of the path runs through detected flood / debris (sanity check)

Inputs: Copernicus DEM + pre-event OSM settlements only. Outputs (in the run folder, names include the start point):
  flow_path.geojson, flow_path_settlements.csv, flow_path_settlements.geojson, flow_path.png, flow_path.json
"""
import json
import os

import numpy as np
import rasterio
from pyproj import Transformer

DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)            # pysheds D8 codes: N, NE, E, SE, S, SW, W, NW
STEP = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}
SNAP_M = 600
CORRIDOR_M, CORRIDOR_H = 1000, 30
HIGH_H = 10
SETTLEMENT_R = 500
IMPACT_NEAR_M = 100


def _terrain(dem_path):
    from pysheds.grid import Grid
    if not hasattr(np, "in1d"):                  # numpy >= 2.4 removed in1d; pysheds still calls it
        np.in1d = np.isin
    grid = Grid.from_raster(dem_path)
    dem = grid.read_raster(dem_path)
    cond = grid.resolve_flats(grid.fill_depressions(grid.fill_pits(dem)))
    fdir = grid.flowdir(cond, dirmap=DIRMAP)
    acc = grid.accumulation(fdir, dirmap=DIRMAP)
    with rasterio.open(dem_path) as src:
        z, tr, crs = src.read(1).astype("float32"), src.transform, src.crs
    return z, np.asarray(fdir), np.asarray(acc), tr, crs


def trace(dem_path, lon, lat, max_km=300):
    z, fdir, acc, tr, crs = _terrain(dem_path)
    to_utm = Transformer.from_crs(4326, crs, always_xy=True)
    x, y = to_utm.transform(lon, lat)
    r0, c0 = rasterio.transform.rowcol(tr, x, y)
    H, W = z.shape
    if not (0 <= r0 < H and 0 <= c0 < W):
        raise SystemExit("Start point is outside the DEM of this run - pick a point inside the mapped area.")
    px = abs(tr.a)
    k = int(SNAP_M / px)
    ra, rb, ca, cb = max(r0 - k, 0), min(r0 + k + 1, H), max(c0 - k, 0), min(c0 + k + 1, W)
    win = acc[ra:rb, ca:cb]
    rr, cc = np.ogrid[ra:rb, ca:cb]
    win = np.where((rr - r0) ** 2 + (cc - c0) ** 2 <= k * k, win, -1)
    i = np.unravel_index(np.argmax(win), win.shape)
    r, c = ra + i[0], ca + i[1]
    snapped_m = float(np.hypot(r - r0, c - c0) * px)
    cells, seen = [], set()
    while 0 <= r < H and 0 <= c < W and (r, c) not in seen and len(cells) * px < max_km * 1000:
        seen.add((r, c)); cells.append((r, c))
        d = int(fdir[r, c])
        if d not in STEP:
            break                                 # sink / flat / nodata - path ends
        dr, dc = STEP[d]; r, c = r + dr, c + dc
    rows, cols = np.array([p[0] for p in cells]), np.array([p[1] for p in cells])
    xs, ys = rasterio.transform.xy(tr, rows, cols)
    xs, ys = np.asarray(xs), np.asarray(ys)
    seg = np.hypot(np.diff(xs), np.diff(ys))
    dist = np.concatenate([[0], np.cumsum(seg)])
    elev = z[rows, cols]
    return {"x": xs, "y": ys, "rows": rows, "cols": cols, "dist_m": dist, "elev": elev, "crs": crs, "tr": tr,
            "z": z, "acc": acc, "snapped_m": snapped_m, "start": (lon, lat),
            "ends_at_edge": bool(not (0 <= r < H and 0 <= c < W))}


def _exposure(path, xs, ys):
    """distance to the channel, height above it and km down the path for points (x, y) in the DEM CRS"""
    from scipy.spatial import cKDTree
    tree = cKDTree(np.c_[path["x"], path["y"]])
    d, i = tree.query(np.c_[xs, ys])
    tr, z = path["tr"], path["z"]
    rows, cols = rasterio.transform.rowcol(tr, xs, ys)
    rows, cols = np.asarray(rows), np.asarray(cols)
    ok = (rows >= 0) & (rows < z.shape[0]) & (cols >= 0) & (cols < z.shape[1])
    zs = np.full(len(xs), np.nan, "float32")
    zs[ok] = z[rows[ok], cols[ok]]
    return d, zs - path["elev"][i], path["dist_m"][i] / 1000, path["elev"][i]


def settlements_along(path, settlements, buildings=None):
    import geopandas as gpd
    from scipy.spatial import cKDTree
    s = settlements.to_crs(path["crs"]).copy()
    pts = s.geometry.representative_point()
    d, h, km, ce = _exposure(path, pts.x.values, pts.y.values)
    s["dist_to_channel_m"], s["height_above_channel_m"] = np.round(d).astype(int), np.round(h, 1)
    s["km_from_start"], s["channel_elev_m"] = np.round(km, 2), np.round(ce, 0)
    s["exposed_buildings_500m"], s["min_bldg_height_above_channel_m"] = 0, np.nan
    exposed_b = None
    if buildings is not None and len(buildings):
        b = buildings.to_crs(path["crs"]).geometry.representative_point()
        bd, bh, bkm, _ = _exposure(path, b.x.values, b.y.values)
        m = (bd <= CORRIDOR_M) & (bh <= CORRIDOR_H)
        exposed_b = gpd.GeoDataFrame({"height_above_channel_m": np.round(bh[m], 1), "km_from_start": np.round(bkm[m], 2)},
                                     geometry=b[m].values, crs=path["crs"])
        if m.any():
            t = cKDTree(np.c_[b.x.values[m], b.y.values[m]])
            for j, (px_, py_) in enumerate(zip(pts.x.values, pts.y.values)):
                idx = t.query_ball_point((px_, py_), SETTLEMENT_R)
                if idx:
                    s.iloc[j, s.columns.get_loc("exposed_buildings_500m")] = len(idx)
                    s.iloc[j, s.columns.get_loc("min_bldg_height_above_channel_m")] = round(float(np.min(bh[m][idx])), 1)
                    s.iloc[j, s.columns.get_loc("km_from_start")] = round(float(np.median(bkm[m][idx])), 2)
    point_in = (s.dist_to_channel_m <= CORRIDOR_M) & (s.height_above_channel_m <= CORRIDOR_H)
    s = s[point_in | (s.exposed_buildings_500m > 0)].copy()
    low = np.fmin(s["min_bldg_height_above_channel_m"].fillna(np.inf), s["height_above_channel_m"].where(point_in, np.inf))
    s["exposure"] = np.where(low <= HIGH_H, "high", "medium")
    blank = gpd.pd.Series(index=s.index, dtype=object)
    s["name"] = s.get("name:en", blank).fillna(s.get("name", blank)).fillna("(unnamed)")
    s["geometry"] = pts.loc[s.index]
    keep = ["name", "place", "km_from_start", "exposure", "exposed_buildings_500m", "min_bldg_height_above_channel_m",
            "dist_to_channel_m", "height_above_channel_m", "channel_elev_m", "geometry"]
    s = gpd.GeoDataFrame(s[[k for k in keep if k in s.columns]], geometry="geometry", crs=path["crs"])
    return s.sort_values("km_from_start"), exposed_b


def run_flow_path(run_dir, lon, lat, settlements_path=None, impact_path=None, label=None, buildings_path=None):
    import geopandas as gpd
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    from shapely.geometry import LineString
    label = label or f"{lon:.4f}_{lat:.4f}"
    path = trace(f"{run_dir}/dem30.tif", lon, lat)
    line = LineString(np.c_[path["x"], path["y"]])
    sett = gpd.read_file(settlements_path or f"{run_dir}/osm_settlements.geojson")
    bp = buildings_path or f"{run_dir}/osm_buildings.geojson"
    bld = gpd.read_file(bp) if os.path.exists(bp) else None
    along, exposed_b = settlements_along(path, sett, bld)

    summary = {"start_lonlat": [lon, lat], "snapped_to_channel_m": round(path["snapped_m"]),
               "path_length_km": round(float(path["dist_m"][-1]) / 1000, 2),
               "elevation_start_m": round(float(path["elev"][0])), "elevation_end_m": round(float(path["elev"][-1])),
               "ends_at_area_edge": path["ends_at_edge"],
               "settlements_in_corridor": int(len(along)), "settlements_high_exposure": int((along.exposure == "high").sum()),
               "buildings_in_corridor": int(len(exposed_b)) if exposed_b is not None else None,
               "buildings_at_most_10m_above_channel": int((exposed_b.height_above_channel_m <= HIGH_H).sum()) if exposed_b is not None else None,
               "corridor_rule": f"<= {CORRIDOR_M} m from the channel and <= {CORRIDOR_H} m above it; high exposure = buildings <= {HIGH_H} m above it",
               "data": "Copernicus DEM GLO-30 (D8 flow, pysheds) + pre-event OpenStreetMap settlements"}
    imp = impact_path or f"{run_dir}/impact_fused.tif"
    if os.path.exists(imp):
        with rasterio.open(imp) as src:
            cls = src.read(1)
            hit = np.isin(cls, [1, 2, 3, 4, 5]).astype(np.uint8)
            xs, ys = path["x"], path["y"]
            rc = [rasterio.transform.rowcol(src.transform, a, b) for a, b in zip(xs, ys)]
            from scipy.ndimage import binary_dilation
            near = binary_dilation(hit, iterations=int(IMPACT_NEAR_M / abs(src.transform.a)))
            vals = np.array([near[r, c] if 0 <= r < near.shape[0] and 0 <= c < near.shape[1] else False for r, c in rc])
            inside = np.array([0 <= r < near.shape[0] and 0 <= c < near.shape[1] and cls[r, c] != 255 for r, c in rc])
        summary["path_km_inside_mapped_valley"] = round(float(inside.sum() * np.mean(np.diff(path['dist_m'])) / 1000), 2) if len(rc) > 1 else 0
        summary["path_pct_with_detected_impact_within_100m"] = round(100 * float(vals[inside].mean()), 1) if inside.any() else None

    gpd.GeoDataFrame([{"start": label, **{k: v for k, v in summary.items() if not isinstance(v, list)}}],
                     geometry=[line], crs=path["crs"]).to_crs(4326).to_file(f"{run_dir}/flow_path_{label}.geojson", driver="GeoJSON")
    along.to_crs(4326).to_file(f"{run_dir}/flow_path_{label}_settlements.geojson", driver="GeoJSON")
    if exposed_b is not None:
        exposed_b.to_crs(4326).to_file(f"{run_dir}/flow_path_{label}_buildings.geojson", driver="GeoJSON")
    along.drop(columns="geometry").to_csv(f"{run_dir}/flow_path_{label}_settlements.csv", index=False)
    json.dump(summary, open(f"{run_dir}/flow_path_{label}.json", "w"), indent=2)

    # map: hillshade + path + settlements
    z = path["z"]; tr = path["tr"]
    gy, gx = np.gradient(np.nan_to_num(z, nan=float(np.nanmean(z))), abs(tr.e), tr.a)
    hs = np.clip(0.5 + 0.5 * (-gx - gy) / np.sqrt(1 + gx ** 2 + gy ** 2) * 2, 0, 1)
    ext = [tr.c / 1000, (tr.c + tr.a * z.shape[1]) / 1000, (tr.f + tr.e * z.shape[0]) / 1000, tr.f / 1000]
    fig, ax = plt.subplots(1, 2, figsize=(18, 11), gridspec_kw={"width_ratios": [1.3, 1]})
    ax[0].imshow(hs, cmap="gray", extent=ext)
    if exposed_b is not None and len(exposed_b):
        ax[0].scatter(exposed_b.geometry.x / 1000, exposed_b.geometry.y / 1000, s=1, c="#ff7f00", alpha=0.6,
                      label=f"buildings in the corridor ({len(exposed_b)})")
    ax[0].plot(path["x"] / 1000, path["y"] / 1000, color="#0057ff", lw=2.5, label="flood path (D8 steepest descent)")
    ax[0].plot(path["x"][0] / 1000, path["y"][0] / 1000, "^", color="red", ms=12, label="start (snapped to channel)")
    for exp, col in [("medium", "#f0a020"), ("high", "#d00000")]:
        sub = along[along.exposure == exp]
        ax[0].scatter(sub.geometry.x / 1000, sub.geometry.y / 1000, s=45, c=col, edgecolor="white", zorder=5,
                      label=f"settlement, {exp} exposure ({len(sub)})")
    for _, r in along[along.name != "(unnamed)"].head(25).iterrows():
        ax[0].annotate(r["name"], (r.geometry.x / 1000, r.geometry.y / 1000), fontsize=7, xytext=(3, 3), textcoords="offset points")
    ax[0].legend(loc="lower right", fontsize=9); ax[0].set_xlabel("UTM easting (km)"); ax[0].set_ylabel("UTM northing (km)")
    ax[0].set_title(f"Flood path from {lat:.4f} N, {lon:.4f} E - {summary['path_length_km']} km, "
                    f"{summary['settlements_in_corridor']} settlements in the corridor")
    ax[1].plot(path["dist_m"] / 1000, path["elev"], color="#0057ff")
    for exp, col in [("medium", "#f0a020"), ("high", "#d00000")]:
        sub = along[along.exposure == exp]
        ax[1].scatter(sub.km_from_start, sub.channel_elev_m, c=col, s=30, zorder=5)
    ax[1].set_xlabel("km down the path"); ax[1].set_ylabel("elevation (m)"); ax[1].set_title("Longitudinal profile + settlements")
    ax[1].grid(alpha=0.3)
    plt.tight_layout(); plt.savefig(f"{run_dir}/flow_path_{label}.png", dpi=110); plt.close()
    print(json.dumps(summary, indent=1))
    return summary, along


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Trace the flood path from an upstream point and list settlements along it")
    ap.add_argument("run_dir"); ap.add_argument("lon", type=float); ap.add_argument("lat", type=float)
    ap.add_argument("--settlements"); ap.add_argument("--buildings"); ap.add_argument("--label")
    a = ap.parse_args()
    run_flow_path(a.run_dir, a.lon, a.lat, a.settlements, label=a.label, buildings_path=a.buildings)
