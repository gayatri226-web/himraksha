"""How much does the AI model (U-Net) itself contribute, on unseen Himalayan scenes?  (validation only)

Three checks, using outputs that already exist - no new satellite download:
  1. U-Net water vs Copernicus EMS EMSR927 (Trishuli): is the water the model finds inside the EMS flood/debris area?
  2. Ablation (Trishuli): score the map against EMS with and without the U-Net water class.
     (Upper bound of the model's effect: pixels removed here might partly be caught by change detection instead.)
  3. U-Net water vs our own Sentinel-2 optical layer (Trishuli + Chamoli): independent optical check where the sky
     was clear on both dates. Caveat: the clear Sentinel-2 image is 2-3 weeks after the radar image, so water may
     have drained by then; this check is weaker than EMS.

Usage:
  python model_only_check.py <ems_dir> <trishuli_results_dir> <chamoli_results_dir> <out_json>
EMS is only read here (and in eval_emsr927.py) - never by the pipeline.
"""
import sys, os, json, shutil, subprocess, tempfile
import numpy as np, rasterio

EMS, TRI, CHA, OUT = sys.argv[1:5]
HERE = os.path.dirname(os.path.abspath(__file__))
px_km2 = 1e-4                                  # 10 m pixels


def ems_eval(results_dir):
    """Run eval_emsr927.py on a results folder, return its totals."""
    with tempfile.TemporaryDirectory() as out:
        subprocess.run([sys.executable, os.path.join(HERE, "eval_emsr927.py"), EMS, results_dir, out],
                       check=True, capture_output=True)
        return json.load(open(f"{out}/eval_emsr927.json"))


def optical_check(results_dir):
    c = rasterio.open(f"{results_dir}/impact_fused.tif")
    cls, conf = c.read(1), c.read(2)
    s2 = rasterio.open(f"{results_dir}/s2_change.tif").read(1)   # 0 none, 4 debris, 5 new water, 254 not clear, 255 outside
    clear = (s2 != 254) & (s2 != 255)
    u = cls == 1
    uc = u & clear
    n = max(int(uc.sum()), 1)
    return {"unet_water_km2": round(u.sum() * px_km2, 3),
            "unet_water_high_conf_km2": round((u & (conf == 3)).sum() * px_km2, 3),
            "unet_water_where_s2_clear_km2": round(uc.sum() * px_km2, 3),
            "s2_says_new_water_pct": round(100 * (uc & (s2 == 5)).sum() / n, 1),
            "s2_says_debris_pct": round(100 * (uc & (s2 == 4)).sum() / n, 1),
            "s2_says_no_change_pct": round(100 * (uc & (s2 == 0)).sum() / n, 1)}


res = {"what": "Contribution of the U-Net (AI component) on two unseen Himalayan events",
       "trishuli_2026": {}, "chamoli_2021": {}}

# 1 + 2: EMS (Trishuli only - no EMS map exists for our Chamoli run)
full = ems_eval(TRI)["total_3_aois"]
with tempfile.TemporaryDirectory() as d:
    for f in ("run_log.json", "buildings_hit.geojson", "roads_hit.geojson", "bridges_hit.geojson"):
        shutil.copy(f"{TRI}/{f}", d)
    src = rasterio.open(f"{TRI}/impact_fused.tif"); prof = src.profile; a = src.read()
    a[0][a[0] == 1] = 0                                            # drop U-Net water
    with rasterio.open(f"{d}/impact_fused.tif", "w", **prof) as w:
        w.write(a)
    no_unet = ems_eval(d)["total_3_aois"]

res["trishuli_2026"]["unet_water_vs_ems"] = full["area_water_only_unet"]
res["trishuli_2026"]["ablation_vs_ems"] = {
    "with_unet": {k: full["area_all_detections"][k] for k in ("precision", "recall", "f1")},
    "without_unet": {k: no_unet["area_all_detections"][k] for k in ("precision", "recall", "f1")},
    "with_unet_high_conf": {k: full["area_high_confidence"][k] for k in ("precision", "recall", "f1")},
    "without_unet_high_conf": {k: no_unet["area_high_confidence"][k] for k in ("precision", "recall", "f1")},
    "note": "Upper bound: removed pixels might partly be picked up by change detection if the model were absent."}
res["trishuli_2026"]["unet_vs_sentinel2"] = optical_check(TRI)
res["chamoli_2021"]["unet_vs_sentinel2"] = optical_check(CHA)
res["chamoli_2021"]["note"] = ("No EMS map for this run; U-Net found little water (winter debris flood), mostly low "
                               "confidence (the two orbits disagree), and none of it is confirmed by Sentinel-2 two "
                               "weeks later -> treated as unconfirmed.")
json.dump(res, open(OUT, "w"), indent=2)
print(json.dumps(res, indent=2))
