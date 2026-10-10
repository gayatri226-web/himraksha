# Track B - mapping pipeline v1: area + flood date -> fused impact map (ascending + descending Sentinel-1)
# Needs in the Colab session: `con` (openEO login) and flood_unet.onnx + norm.json uploaded.
# Inputs used: Sentinel-1 GRD + Copernicus DEM GLO-30 only (allowed sources). No EMS / post-event OSM.
!pip install -q openeo rasterio pysheds onnxruntime scipy

import os, json, time, datetime as dt
from collections import defaultdict
import numpy as np, requests, rasterio, onnxruntime as ort, matplotlib.pyplot as plt
from rasterio.warp import reproject, Resampling
from scipy.ndimage import uniform_filter, label
from matplotlib.colors import ListedColormap
from pysheds.grid import Grid

# ============ CONFIG (judges change only these two) ============
BBOX = [85.10, 27.90, 85.45, 28.32]      # W, S, E, N  - full Trishuli corridor
EVENT = "2026-08-26"
# ===============================================================
SEARCH_DAYS = 36                          # look this far before/after the event for S1 scenes
MAX_PAIR_GAP = 48                         # days between pre and post (12 or 24 typical)
MIN_COVERAGE = 0.6                        # a pair must cover >= 60% of the area to be preferred
HAND_MAX_M, SLOPE_MAX_DEG = 50, 30        # valley-floor mask (tuned on Trishuli)
K_SIGMA, MIN_DB, MIN_BLOB_PX = 2.5, 2.0, 50
TILE, STRIDE = 224, 160
missing = [f for f in ("flood_unet.onnx", "norm.json") if not os.path.exists(f)]
assert not missing, f"Upload {missing} first (run the upload cell), then re-run this cell"
assert "con" in globals(), "Run the openEO login cell first (con = openeo.connect(...).authenticate_oidc())"
SAVE_TO_DRIVE = False                    # keep results in Google Drive so a Colab disconnect loses nothing
RUN_NAME = "run_" + EVENT.replace("-", "") + "_" + "_".join(f"{v:.2f}" for v in BBOX)
OUT = RUN_NAME
if SAVE_TO_DRIVE:
    try:
        from google.colab import drive
        drive.mount("/content/drive")
        OUT = f"/content/drive/MyDrive/trackb/{RUN_NAME}"
    except Exception as e:
        print("Drive not mounted, saving locally:", e)
os.makedirs(OUT, exist_ok=True)
for name in ("s1_orbit85_ascending.tif", "s1_orbit121_descending.tif", "dem30.tif"):   # reuse local copies from an earlier run
    if os.path.exists(f"{RUN_NAME}/{name}") and not os.path.exists(f"{OUT}/{name}"):
        import shutil; shutil.copy(f"{RUN_NAME}/{name}", f"{OUT}/{name}"); print("copied", name, "to", OUT)
ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
log = {"bbox": BBOX, "event": EVENT, "sources": ["Sentinel-1 GRD (CDSE)", "Copernicus DEM GLO-30"], "orbits": []}


def say(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def utm_epsg(bbox):
    lon, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1


# ---------- 1. find same-orbit pre/post pairs ----------
def find_pairs(bbox, event):
    ev = dt.date.fromisoformat(event)
    w, s, e, n = bbox
    wkt = f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"
    flt = ("Collection/Name eq 'SENTINEL-1' and contains(Name,'_IW_GRDH_1SDV_') "
           f"and OData.CSC.Intersects(area=geography'SRID=4326;{wkt}') "
           f"and ContentDate/Start gt {(ev - dt.timedelta(SEARCH_DAYS)).isoformat()}T00:00:00.000Z "
           f"and ContentDate/Start lt {(ev + dt.timedelta(SEARCH_DAYS)).isoformat()}T23:59:59.000Z")
    params = {"$filter": flt, "$expand": "Attributes", "$top": 200}
    items, url = [], ODATA
    while url:
        r = requests.get(url, params=params, timeout=120); r.raise_for_status(); js = r.json()
        items += js.get("value", []); url, params = js.get("@odata.nextLink"), None
    from shapely.geometry import shape, box
    from shapely.ops import unary_union
    aoi = box(w, s, e, n)
    foot = defaultdict(list)                          # (orbit, direction, date) -> footprints of all slices
    for p in items:
        if p["Name"].endswith("_COG.SAFE"):
            continue
        a = {x["Name"]: x.get("Value") for x in p.get("Attributes", [])}
        d = dt.date.fromisoformat(p["ContentDate"]["Start"][:10])
        try:
            g = shape(p["GeoFootprint"])
        except Exception:
            g = aoi                                    # footprint missing -> assume full cover
        foot[(a.get("relativeOrbitNumber"), str(a.get("orbitDirection")).upper(), d)].append(g)
    cover = {k: unary_union(v).intersection(aoi).area / aoi.area for k, v in foot.items()}
    groups = defaultdict(dict)                         # (orbit, direction) -> {date: coverage}
    for (orbit, direction, d), c in cover.items():
        groups[(orbit, direction)][d] = c
    pairs = []
    for (orbit, direction), dates in groups.items():
        pre = [d for d in dates if d < ev]; post = [d for d in dates if d >= ev]
        if pre and post and (min(post) - max(pre)).days <= MAX_PAIR_GAP:
            cov = min(dates[max(pre)], dates[min(post)])
            pairs.append({"orbit": orbit, "direction": direction, "pre": max(pre), "post": min(post),
                          "gap_days": (min(post) - max(pre)).days, "days_after_event": (min(post) - ev).days,
                          "aoi_coverage": round(cov, 2)})
    # prefer pairs that cover most of the area, then the earliest post-event image
    pairs.sort(key=lambda p: (p["aoi_coverage"] < MIN_COVERAGE, p["days_after_event"], -p["aoi_coverage"]))
    chosen = {}
    for p in pairs:                                   # best ascending + best descending
        chosen.setdefault(p["direction"], p)
    return list(chosen.values()), pairs


# ---------- 2. openEO: calibrated dB stack per pair (jobs run in parallel) ----------
def s1_cube(bbox, pair, epsg):
    ext = dict(zip(["west", "south", "east", "north"], bbox))
    def one(day, prefix):
        c = con.load_collection("SENTINEL1_GRD", spatial_extent=ext, bands=["VV", "VH"],
                                temporal_extent=[day.isoformat(), (day + dt.timedelta(1)).isoformat()])
        c = c.sar_backscatter(coefficient="sigma0-ellipsoid").resample_spatial(resolution=10, projection=epsg)
        c = c.reduce_dimension(dimension="t", reducer="mean").apply(lambda x: x.log(base=10) * 10)
        return c.rename_labels("bands", [f"{prefix}_VV", f"{prefix}_VH"])
    return one(pair["pre"], "pre").merge_cubes(one(pair["post"], "post"))


def with_retry(fn, what, tries=6):
    """Retry openEO calls on 'Too Many Requests' (429) and short network hiccups, waiting longer each time."""
    for k in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            busy = "429" in str(e) or "Too Many Requests" in str(e)
            net = isinstance(e, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))
            if (busy or net) and k < tries:
                wait = 30 * k
                say(f"   {what}: server busy ({type(e).__name__}) - retry {k}/{tries - 1} in {wait}s")
                time.sleep(wait)
            else:
                raise


def run_jobs(bbox, pairs, epsg):
    """One openEO batch job at a time (free tier limits parallel jobs), with retries and caching."""
    for p in pairs:
        path = f"{OUT}/s1_orbit{p['orbit']}_{p['direction'].lower()}.tif"
        p["file"] = path
        if os.path.exists(path):
            say(f"cached {path}"); continue
        tag = f"orbit {p['orbit']} {p['direction']}"
        try:
            job = with_retry(lambda: s1_cube(bbox, p, epsg).create_job(
                title=f"TrackB S1 orbit {p['orbit']}", out_format="GTiff"), f"create job {tag}")
            with_retry(job.start, f"start job {tag}")
            say(f"started openEO job for {tag} ({p['pre']} -> {p['post']})")
            while True:
                time.sleep(20)
                st = with_retry(job.status, f"status {tag}")
                if st == "finished":
                    with_retry(lambda: job.get_results().download_file(path), f"download {tag}")
                    say(f"downloaded {path}"); break
                if st in ("error", "canceled"):
                    say(f"!! job for {tag} ended with '{st}' - continuing without this orbit"); break
        except Exception as e:
            say(f"!! {tag} failed: {type(e).__name__}: {str(e)[:200]} - continuing without this orbit")
    return [p for p in pairs if os.path.exists(p["file"])]


# ---------- grid helpers ----------
def read(path):
    with rasterio.open(path) as s:
        return s.read().astype("float32"), s.transform, s.crs, s.profile


def to_grid(arr, src_tr, src_crs, ref, resampling=Resampling.bilinear):
    ref_tr, ref_crs, shape = ref
    out = np.full((arr.shape[0],) + shape, np.nan, dtype="float32")
    for i in range(arr.shape[0]):
        reproject(arr[i], out[i], src_transform=src_tr, src_crs=src_crs, dst_transform=ref_tr, dst_crs=ref_crs,
                  resampling=resampling, src_nodata=np.nan, dst_nodata=np.nan)
    return out


# ---------- 3. terrain: DEM -> slope + HAND ----------
def terrain(bbox, epsg, ref):
    dem_path = f"{OUT}/dem30.tif"
    if not os.path.exists(dem_path):
        ext = dict(zip(["west", "south", "east", "north"], bbox))
        (con.load_collection("COPERNICUS_30", spatial_extent=ext).reduce_dimension(dimension="t", reducer="max")
            .resample_spatial(resolution=30, projection=epsg)).download(dem_path, format="GTiff")
    grid = Grid.from_raster(dem_path); dem = grid.read_raster(dem_path)
    cond = grid.resolve_flats(grid.fill_depressions(grid.fill_pits(dem)))
    fdir = grid.flowdir(cond); acc = grid.accumulation(fdir)
    hand30 = np.asarray(grid.compute_hand(fdir, cond, acc > 2000), dtype="float32")
    d30, tr, crs, _ = read(dem_path)
    gy, gx = np.gradient(d30[0], abs(tr.e), tr.a)
    slope30 = np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")
    stack = to_grid(np.stack([d30[0], slope30, hand30]), tr, crs, ref)
    return stack[0], stack[1], stack[2]


# ---------- 4. per-orbit detection ----------
NORM = json.load(open("norm.json"))
SESS = ort.InferenceSession("flood_unet.onnx", providers=["CPUExecutionProvider"])


def make_input(sar_db, dem, norm=NORM):
    dem = np.nan_to_num(dem, nan=float(np.nanmean(dem)) if np.isfinite(dem).any() else 0.0)
    lo, hi = norm["sar_clip_db"]
    sar = (np.clip(sar_db, lo, hi) + norm["sar_shift"]) / norm["sar_scale"]
    gy, gx = np.gradient(dem, norm["pixel_size_m"])
    slp = np.degrees(np.arctan(np.hypot(gx, gy))) / norm["slope_scale_deg"]
    dem_rel = np.clip((dem - dem.mean()) / norm["dem_rel_scale_m"], -5, 5)
    return np.concatenate([sar, dem_rel[None], slp[None]]).astype(np.float32)


def unet_classes(sar_db, dem10):
    H, W = dem10.shape
    probs = np.zeros((3, H, W), np.float32); cnt = np.zeros((H, W), np.float32)
    rows = list(range(0, max(H - TILE, 0) + 1, STRIDE)); cols = list(range(0, max(W - TILE, 0) + 1, STRIDE))
    if rows[-1] != H - TILE: rows.append(H - TILE)
    if cols[-1] != W - TILE: cols.append(W - TILE)
    t0 = time.time()
    for i, r in enumerate(rows):
        if i % 5 == 0:
            done = i / len(rows)
            eta = (time.time() - t0) / done * (1 - done) / 60 if done else float("nan")
            say(f"   U-Net row {i}/{len(rows)} ({done:.0%}) - about {eta:.0f} min left")
        batch = np.stack([make_input(sar_db[:, r:r + TILE, c:c + TILE], dem10[r:r + TILE, c:c + TILE]) for c in cols])
        lg = SESS.run(None, {"x": batch})[0]
        e = np.exp(lg - lg.max(1, keepdims=True)); p = e / e.sum(1, keepdims=True)
        for k, c in enumerate(cols):
            probs[:, r:r + TILE, c:c + TILE] += p[k]; cnt[r:r + TILE, c:c + TILE] += 1
    return (probs / np.maximum(cnt, 1)).argmax(0)


def despeckle_db(db, size=7):
    return 10 * np.log10(np.clip(uniform_filter(10 ** (db / 10), size), 1e-6, None))


def drop_small(mask):
    lab, n = label(mask)
    if n == 0: return mask
    sz = np.bincount(lab.ravel()); keep = sz >= MIN_BLOB_PX; keep[0] = False
    return keep[lab]


def detect(stack, valley, dem10, pair):
    valid = np.all(np.isfinite(stack) & (stack > -45), axis=0)
    sar = np.where(np.isfinite(stack), stack, -30.0)
    region = valley & valid
    res = {"valid": valid}
    thr = {}
    for pol, (a, b) in {"VV": (0, 2), "VH": (1, 3)}.items():
        diff = despeckle_db(sar[b]) - despeckle_db(sar[a])
        diff -= np.median(diff[region])
        mad = np.median(np.abs(diff[region])) * 1.4826
        thr[pol] = max(MIN_DB, K_SIGMA * mad); res[pol] = diff
    dark = region & ((res["VV"] < -thr["VV"]) | (res["VH"] < -thr["VH"]))
    bright = region & ((res["VV"] > thr["VV"]) | (res["VH"] > thr["VH"])) & ~dark
    say(f"orbit {pair['orbit']}: running U-Net on {sar.shape[1]}x{sar.shape[2]} px")
    water = (unet_classes(sar, dem10) == 2) & region
    pair["thresholds_db"] = {k: round(float(v), 2) for k, v in thr.items()}
    return {"valid": valid, "water": water, "dark": drop_small(dark), "bright": drop_small(bright)}


# ======================= RUN =======================
say(f"Searching Sentinel-1 pairs for {BBOX}, event {EVENT}")
chosen, all_pairs = find_pairs(BBOX, EVENT)
for p in all_pairs:
    print(f"   orbit {p['orbit']:>4} {p['direction']:<10} pre {p['pre']} post {p['post']} "
          f"gap {p['gap_days']}d  (+{p['days_after_event']}d after event)  covers {p['aoi_coverage']*100:.0f}% of area")
if not chosen:
    raise SystemExit("No same-orbit Sentinel-1 pair found around this date - cannot map this event with radar.")
say("Using: " + ", ".join(f"orbit {p['orbit']} {p['direction']}" for p in chosen))

epsg = utm_epsg(BBOX)
chosen = run_jobs(BBOX, chosen, epsg)
if not chosen:
    raise SystemExit("All openEO jobs failed - see messages above.")

ref_arr, ref_tr, ref_crs, ref_prof = read(chosen[0]["file"])
ref = (ref_tr, ref_crs, ref_arr.shape[1:])
say("Building terrain masks (Copernicus DEM)")
dem10, slope, hand = terrain(BBOX, epsg, ref)
valley = (hand < HAND_MAX_M) & (slope < SLOPE_MAX_DEG)

per_orbit = []
for p in chosen:
    arr, tr, crs, _ = read(p["file"])
    stack = arr if p is chosen[0] else to_grid(arr, tr, crs, ref)
    per_orbit.append(detect(stack, valley, dem10, p))

# ---------- 5. fuse orbits (observability-aware) ----------
# An orbit only counts where it could actually see the ground (radar shadow / layover / swath edge = no data).
valids = np.array([o["valid"] for o in per_orbit])
dets = np.array([(o["water"] | o["dark"] | o["bright"]) for o in per_orbit]) & valids
n_valid = valids.sum(0)
n_det = dets.sum(0)
any_valid = n_valid > 0
water = np.any([o["water"] & o["valid"] for o in per_orbit], axis=0)
dark = np.any([o["dark"] & o["valid"] for o in per_orbit], axis=0) & ~water
bright = np.any([o["bright"] & o["valid"] for o in per_orbit], axis=0) & ~water & ~dark
hit = water | dark | bright

# confidence: 3 = seen by >=2 orbits, all agree | 2 = only one orbit could see it, and it saw damage
#             1 = >=2 orbits could see it, they disagree | 0 = no damage | 255 = outside valley / unobserved
conf = np.zeros(valley.shape, np.uint8)
conf[hit & (n_valid >= 2) & (n_det == n_valid)] = 3
conf[hit & (n_valid == 1)] = 2
conf[hit & (n_valid >= 2) & (n_det < n_valid)] = 1

impact = np.zeros(valley.shape, np.uint8)
impact[dark] = 2; impact[bright] = 3; impact[water] = 1
impact[~valley | ~any_valid] = 255
conf[impact == 255] = 255
prof = ref_prof.copy(); prof.update(count=2, dtype="uint8", nodata=255, compress="deflate")
with rasterio.open(f"{OUT}/impact_fused.tif", "w", **prof) as dst:
    dst.write(impact, 1); dst.write(conf, 2)
    dst.update_tags(1, classes="0=unchanged,1=new_water_unet,2=debris_darkened,3=debris_brightened,255=outside_valley_or_unobserved")
    dst.update_tags(2, confidence="3=high(all observing orbits agree),2=medium(single view),1=low(orbits disagree),0=no_damage,255=n/a")

px = abs(ref_tr.a * ref_tr.e) / 1e6
log["orbits"] = []
stats = {"new_water_km2": water.sum() * px, "debris_darkened_km2": dark.sum() * px,
         "debris_brightened_km2": bright.sum() * px, "total_impacted_km2": hit.sum() * px,
         "conf_high_km2": (conf == 3).sum() * px, "conf_medium_single_view_km2": (conf == 2).sum() * px,
         "conf_low_disagree_km2": (conf == 1).sum() * px,
         "valley_floor_km2": valley.sum() * px,
         "valley_seen_by_2plus_orbits_km2": (valley & (n_valid >= 2)).sum() * px,
         "valley_seen_by_1_orbit_km2": (valley & (n_valid == 1)).sum() * px,
         "valley_unobserved_km2": (valley & (n_valid == 0)).sum() * px}
for k, o in enumerate(per_orbit):
    stats[f"valley_visible_orbit{chosen[k]['orbit']}_pct"] = o["valid"][valley].mean() * 100
stats = {k: round(float(v), 3) for k, v in stats.items()}
for p in chosen:
    log["orbits"].append({k: (str(v) if isinstance(v, dt.date) else v) for k, v in p.items()})
log["stats"] = stats
log["params"] = {"hand_max_m": HAND_MAX_M, "slope_max_deg": SLOPE_MAX_DEG, "k_sigma": K_SIGMA,
                 "min_db": MIN_DB, "min_blob_ha": MIN_BLOB_PX * px * 100}
json.dump(log, open(f"{OUT}/run_log.json", "w"), indent=2)

print("\n================ SUMMARY ================")
for p in chosen:
    print(f"orbit {p['orbit']} {p['direction']}: {p['pre']} -> {p['post']} | thresholds {p['thresholds_db']}")
for k, v in stats.items():
    print(f"{k:<24} {v}")
print(f"Saved {OUT}/impact_fused.tif and {OUT}/run_log.json")

# ---------- 6. figure ----------
b = rasterio.transform.array_bounds(*ref[2], ref_tr)
ext = [b[0] / 1000, b[2] / 1000, b[1] / 1000, b[3] / 1000]
bg = despeckle_db(np.where(np.isfinite(ref_arr[2]), ref_arr[2], -30))
cmap = ListedColormap(["#1f6feb", "#8b4513", "#f0a020"])
fig, ax = plt.subplots(2, 2, figsize=(18, 20))

def draw(axis, cls, title):
    axis.imshow(bg, extent=ext, cmap="gray", vmin=-25, vmax=0)
    axis.imshow(np.where(np.isin(cls, [1, 2, 3]), cls, np.nan), extent=ext, cmap=cmap, vmin=0.5, vmax=3.5,
                interpolation="nearest")
    axis.set_title(title)

for k, (o, p) in enumerate(zip(per_orbit[:2], chosen[:2])):
    c = np.zeros_like(impact); c[o["dark"]] = 2; c[o["bright"]] = 3; c[o["water"]] = 1
    draw(ax[0, k], c, f"orbit {p['orbit']} {p['direction']} only ({p['pre']} -> {p['post']})")
if len(per_orbit) == 1:
    ax[0, 1].axis("off")
draw(ax[1, 0], impact, "FUSED impact (any orbit)")
ax[1, 1].imshow(bg, extent=ext, cmap="gray", vmin=-25, vmax=0)
ax[1, 1].imshow(np.where(valley & (n_valid == 0), 1.0, np.nan), extent=ext, cmap=ListedColormap(["#7a00a0"]),
                vmin=0, vmax=2, alpha=0.5, interpolation="nearest")
ax[1, 1].imshow(np.where(np.isin(conf, [1, 2, 3]), conf, np.nan), extent=ext,
                cmap=ListedColormap(["#999999", "#f0c000", "#d00000"]), vmin=0.5, vmax=3.5, interpolation="nearest")
ax[1, 1].set_title("Confidence: red = orbits agree, yellow = only 1 orbit could see,\n"
                   "grey = orbits disagree, purple = valley no orbit could see")
handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in ["#1f6feb", "#8b4513", "#f0a020"]]
ax[1, 0].legend(handles, ["new water (U-Net)", "debris - darkened", "debris - brightened"], loc="upper left")
for axis in ax.ravel():
    axis.set_xlabel("UTM easting (km)"); axis.set_ylabel("UTM northing (km)")
plt.tight_layout(); plt.savefig(f"{OUT}/impact_fused.png", dpi=110); plt.show()
print("Contains modified Copernicus Sentinel data 2026.")
print("Produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus Defence and Space GmbH 2014-2018 "
      "provided under COPERNICUS by the European Union and ESA; all rights reserved.")

# ---------- 7. download everything as one zip (Colab disk is temporary) ----------
import shutil
try:
    from google.colab import files
    shutil.make_archive("trishuli_run", "zip", OUT)
    files.download("trishuli_run.zip")
    print("Downloading trishuli_run.zip - keep it safe")
except Exception as e:
    print("Zip/download skipped:", e)
