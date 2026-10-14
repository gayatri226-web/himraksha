"""
HimRaksha - raw Sentinel-1 GRD -> analysis-ready before/after stack (our own processing chain).

Replaces the openEO `sar_backscatter` step. For each same-orbit pair (pre date, post date) it:
  1. finds every GRD slice of that orbit/date touching the area (Copernicus Data Space catalogue, OData)
  2. downloads the original IW GRDH .SAFE zips (CDSE account from .env / environment variables)
  3. processes each slice with ESA SNAP (gpt), step by step:
        Apply-Orbit-File (precise orbits) -> ThermalNoiseRemoval -> Remove-GRD-Border-Noise
        -> Calibration (sigma0) -> Subset (area + margin) -> Terrain-Correction (Copernicus DEM GLO-30, 10 m, UTM)
  4. mosaics the slices of each date onto one fixed 10 m UTM grid covering the area, converts to dB (in Python)
  5. writes the stack used by the rest of the pipeline.

Output contract (identical to the old openEO output, so nothing downstream changes):
  file   <out_dir>/s1_orbit<NN>_<ascending|descending>.tif
  bands  1 pre_VV, 2 pre_VH, 3 post_VV, 4 post_VH   (sigma0, dB, float32, nodata = NaN)
  grid   UTM zone of the area (utm_epsg), 10 m pixels, covering the bbox
No speckle filter here: 01_mapping.py despeckles before change detection.

Why SNAP: it is ESA's reference Sentinel-1 toolbox and the only open tool that does all of the steps above
(precise orbits + border/thermal noise removal + calibration + terrain correction). Kuro Siwo, our training
data, was also prepared with SNAP, so the model sees images processed the same way.

Use in Colab (see notebooks/grd_preprocess_colab.ipynb) or from a shell:
  python pipeline/grd_preprocess.py --bbox 85.10 27.90 85.45 28.32 --orbit 85 --direction ASCENDING \
         --pre 2026-08-16 --post 2026-08-28 --out run_trishuli
"""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import requests

CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
DOWNLOAD = "https://download.dataspace.copernicus.eu/odata/v1/Products({id})/$value"
TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
PIXEL_M = 10
MARGIN_DEG = 0.02          # extra border around the area before terrain correction (avoids edge gaps)
BANDS = ["pre_VV", "pre_VH", "post_VV", "post_VH"]
DB_FLOOR = -50.0           # sigma0 below this (or 0 = no data) is treated as missing


def say(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def utm_epsg(bbox):
    lon, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1


def load_env(path=".env"):
    """Read KEY=value lines from .env into os.environ (values already set win). No extra package needed."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------------------------------------------------------------- 1. catalogue search
def bbox_wkt(bbox, margin=0.0):
    w, s, e, n = bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def search_slices(bbox, orbit, direction, day):
    """All IW GRDH dual-pol slices of one relative orbit + direction on one date that touch the area."""
    day = dt.date.fromisoformat(str(day))
    flt = ("Collection/Name eq 'SENTINEL-1' and contains(Name,'_IW_GRDH_1SDV_') "
           f"and OData.CSC.Intersects(area=geography'SRID=4326;{bbox_wkt(bbox)}') "
           f"and ContentDate/Start gt {day.isoformat()}T00:00:00.000Z "
           f"and ContentDate/Start lt {day.isoformat()}T23:59:59.999Z")
    r = requests.get(CATALOGUE, params={"$filter": flt, "$expand": "Attributes", "$top": 50}, timeout=120)
    r.raise_for_status()
    out = []
    for p in r.json().get("value", []):
        if p["Name"].endswith("_COG.SAFE") or not p.get("Online", True):
            continue
        a = {x["Name"]: x.get("Value") for x in p.get("Attributes", [])}
        if int(a.get("relativeOrbitNumber", -1)) == int(orbit) and str(a.get("orbitDirection", "")).upper() == direction.upper():
            out.append({"id": p["Id"], "name": p["Name"], "size_mb": round(p.get("ContentLength", 0) / 1e6)})
    out.sort(key=lambda x: x["name"])
    return out


# ---------------------------------------------------------------- 2. download
def cdse_token(user=None, password=None):
    user = user or os.environ.get("CDSE_USER")
    password = password or os.environ.get("CDSE_PASSWORD")
    if not (user and password):
        raise SystemExit("Set CDSE_USER and CDSE_PASSWORD (in .env or the environment) - your Copernicus Data Space login.")
    r = requests.post(TOKEN_URL, data={"grant_type": "password", "username": user, "password": password,
                                       "client_id": "cdse-public"}, timeout=60)
    if r.status_code != 200:
        raise SystemExit(f"Copernicus login failed ({r.status_code}). Check CDSE_USER / CDSE_PASSWORD and that the account is verified.")
    return r.json()["access_token"]


def download_slice(item, dest_dir, tries=3):
    """Stream one .SAFE zip to dest_dir (skipped if already complete). Fresh token per attempt."""
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, item["name"].replace(".SAFE", ".zip"))
    if os.path.exists(path) and os.path.getsize(path) > 1e8:
        say(f"cached {os.path.basename(path)}")
        return path
    for attempt in range(1, tries + 1):
        try:
            headers = {"Authorization": f"Bearer {cdse_token()}"}
            url = DOWNLOAD.format(id=item["id"])
            with requests.Session() as s:
                r = s.get(url, headers=headers, allow_redirects=False, stream=True, timeout=120)
                while r.status_code in (301, 302, 303, 307, 308):      # keep the token across redirects
                    r.close()
                    url = r.headers["Location"]
                    r = s.get(url, headers=headers, allow_redirects=False, stream=True, timeout=120)
                r.close()                                              # body not read here
                with s.get(url, headers=headers, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("Content-Length", 0))
                    done, t0, tmp = 0, time.time(), path + ".part"
                    with open(tmp, "wb") as fh:
                        for chunk in r.iter_content(8 << 20):
                            fh.write(chunk)
                            done += len(chunk)
                            if total and done % (200 << 20) < (8 << 20):
                                say(f"   {item['name'][:40]}... {done / total:.0%} ({done / 1e6 / max(time.time() - t0, 1):.0f} MB/s)")
            if total and os.path.getsize(tmp) != total:
                raise IOError(f"incomplete download ({os.path.getsize(tmp)} of {total} bytes)")
            os.replace(tmp, path)
            say(f"downloaded {os.path.basename(path)} ({os.path.getsize(path) / 1e6:.0f} MB)")
            return path
        except Exception as e:
            say(f"!! download attempt {attempt}/{tries} failed: {type(e).__name__}: {str(e)[:150]}")
            time.sleep(15 * attempt)
    raise RuntimeError(f"could not download {item['name']}")


# ---------------------------------------------------------------- 3. SNAP processing
GRAPH = """<graph id="himraksha_grd">
  <version>1.0</version>
  <node id="Read"><operator>Read</operator><sources/>
    <parameters><file>{src}</file></parameters></node>
  <node id="Orbit"><operator>Apply-Orbit-File</operator><sources><sourceProduct refid="Read"/></sources>
    <parameters><orbitType>Sentinel Precise (Auto Download)</orbitType><polyDegree>3</polyDegree>
      <continueOnFail>true</continueOnFail></parameters></node>
  <node id="Noise"><operator>ThermalNoiseRemoval</operator><sources><sourceProduct refid="Orbit"/></sources>
    <parameters><selectedPolarisations>VH,VV</selectedPolarisations><removeThermalNoise>true</removeThermalNoise>
      <reIntroduceThermalNoise>false</reIntroduceThermalNoise></parameters></node>
  <node id="Border"><operator>Remove-GRD-Border-Noise</operator><sources><sourceProduct refid="Noise"/></sources>
    <parameters><selectedPolarisations>VH,VV</selectedPolarisations><borderLimit>500</borderLimit>
      <trimThreshold>0.5</trimThreshold></parameters></node>
  <node id="Calib"><operator>Calibration</operator><sources><sourceProduct refid="Border"/></sources>
    <parameters><selectedPolarisations>VH,VV</selectedPolarisations><outputImageScaleInDb>false</outputImageScaleInDb>
      <outputSigmaBand>true</outputSigmaBand><outputGammaBand>false</outputGammaBand>
      <outputBetaBand>false</outputBetaBand></parameters></node>
  <node id="Subset"><operator>Subset</operator><sources><sourceProduct refid="Calib"/></sources>
    <parameters><geoRegion>{wkt}</geoRegion><copyMetadata>true</copyMetadata></parameters></node>
  <node id="TC"><operator>Terrain-Correction</operator><sources><sourceProduct refid="Subset"/></sources>
    <parameters><demName>Copernicus 30m Global DEM</demName>
      <demResamplingMethod>BILINEAR_INTERPOLATION</demResamplingMethod>
      <imgResamplingMethod>BILINEAR_INTERPOLATION</imgResamplingMethod>
      <pixelSpacingInMeter>{pixel}</pixelSpacingInMeter><mapProjection>EPSG:{epsg}</mapProjection>
      <nodataValueAtSea>false</nodataValueAtSea><saveSelectedSourceBand>true</saveSelectedSourceBand></parameters></node>
  <node id="Write"><operator>Write</operator><sources><sourceProduct refid="TC"/></sources>
    <parameters><file>{dst}</file><formatName>GeoTIFF-BigTIFF</formatName></parameters></node>
</graph>
"""


def find_gpt():
    for p in (os.environ.get("SNAP_GPT"), shutil.which("gpt"), "/opt/snap/bin/gpt",
              os.path.expanduser("~/snap/bin/gpt"), os.path.expanduser("~/esa-snap/bin/gpt")):
        if p and os.path.exists(p):
            return p
    raise SystemExit("SNAP gpt not found. In Colab run install_snap() first, or set SNAP_GPT=/path/to/gpt.")


def install_snap(target="/opt/snap", versions=("12.0", "11.0", "10.0"), memory_gb=8):
    """Unattended ESA SNAP install (Linux / Colab, about 5-10 min). Tries the newest version first. Returns gpt path."""
    gpt = os.path.join(target, "bin", "gpt")
    if os.path.exists(gpt):
        return gpt
    urls = [os.environ["SNAP_INSTALLER_URL"]] if os.environ.get("SNAP_INSTALLER_URL") else [
        f"https://download.esa.int/step/snap/{v}/installers/esa-snap_sentinel_linux-{v}.0.sh" for v in versions]
    inst = "/tmp/snap_installer.sh"
    for url in urls:
        if subprocess.run(["wget", "-q", "-O", inst, url]).returncode == 0 and os.path.getsize(inst) > 1e8:
            say(f"downloaded SNAP installer {url}")
            break
    else:
        raise SystemExit("Could not download the SNAP installer - set SNAP_INSTALLER_URL to the Linux installer link "
                         "from https://step.esa.int/main/download/snap-download/")
    subprocess.run(["sh", inst, "-q", "-dir", target], check=True)
    with open(gpt + ".vmoptions", "a") as fh:                     # give gpt enough memory for full scenes
        fh.write(f"\n-Xmx{memory_gb}G\n")
    say(f"SNAP installed: {gpt}")
    return gpt


def process_slice(zip_path, bbox, epsg, work_dir, gpt=None):
    """Run our SNAP graph on one slice -> linear sigma0 GeoTIFF (VH, VV) in UTM, 10 m. Cached."""
    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.basename(zip_path).replace(".zip", "")
    dst = os.path.join(work_dir, stem + "_tc.tif")
    if os.path.exists(dst):
        say(f"cached {os.path.basename(dst)}")
        return dst
    graph = os.path.join(work_dir, stem + "_graph.xml")
    with open(graph, "w") as fh:
        fh.write(GRAPH.format(src=os.path.abspath(zip_path), wkt=bbox_wkt(bbox, MARGIN_DEG),
                              pixel=PIXEL_M, epsg=epsg, dst=os.path.abspath(dst)))
    cmd = [gpt or find_gpt(), graph, "-q", str(os.cpu_count() or 2), "-c", "4G"]
    say(f"SNAP: {stem[:60]}... (orbit, thermal+border noise, calibration, subset, terrain correction)")
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(dst):
        tail = "\n".join((res.stdout + res.stderr).strip().splitlines()[-15:])
        if "No intersection" in tail or "empty region" in tail.lower():
            say(f"   slice does not overlap the area - skipped")
            return None
        raise RuntimeError(f"SNAP failed on {stem}:\n{tail}")
    say(f"   done in {(time.time() - t0) / 60:.1f} min")
    return dst


# ---------------------------------------------------------------- 4. mosaic + dB on a fixed grid
def target_grid(bbox, epsg, pixel=PIXEL_M):
    """Fixed UTM grid covering the bbox, snapped to whole pixels -> (transform, width, height)."""
    from pyproj import Transformer
    from rasterio.transform import from_origin
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    xs, ys = tr.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    x0, x1 = np.floor(min(xs) / pixel) * pixel, np.ceil(max(xs) / pixel) * pixel
    y0, y1 = np.floor(min(ys) / pixel) * pixel, np.ceil(max(ys) / pixel) * pixel
    return from_origin(x0, y1, pixel, pixel), int(round((x1 - x0) / pixel)), int(round((y1 - y0) / pixel))


def band_index(src, pol):
    """Find the band holding sigma0 for a polarisation (SNAP band names, else VH-then-VV order)."""
    for i, d in enumerate(src.descriptions or (), start=1):
        if d and pol.upper() in d.upper():
            return i
    return {"VH": 1, "VV": 2}[pol.upper()]


def mosaic_date(tc_files, epsg, transform, width, height):
    """Reproject each slice onto the target grid and combine (first valid pixel wins). Returns linear (VV, VH)."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    out = {pol: np.full((height, width), np.nan, "float32") for pol in ("VV", "VH")}
    for f in tc_files:
        with rasterio.open(f) as src:
            for pol in ("VV", "VH"):
                arr = src.read(band_index(src, pol)).astype("float32")
                arr[(arr <= 0) | ~np.isfinite(arr)] = np.nan
                tmp = np.full((height, width), np.nan, "float32")
                reproject(arr, tmp, src_transform=src.transform, src_crs=src.crs, src_nodata=np.nan,
                          dst_transform=transform, dst_crs=f"EPSG:{epsg}", dst_nodata=np.nan,
                          resampling=Resampling.bilinear)
                fill = np.isnan(out[pol]) & np.isfinite(tmp)
                out[pol][fill] = tmp[fill]
    vv, vh = out["VV"], out["VH"]
    if np.isfinite(vv).any() and np.nanmedian(vh) > np.nanmedian(vv):   # VH is always darker than VV over land
        say("   !! VH brighter than VV - band order was swapped, correcting")
        vv, vh = vh, vv
    return vv, vh


def to_db(lin):
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10 * np.log10(lin)
    db[~np.isfinite(db) | (db < DB_FLOOR)] = np.nan
    return db.astype("float32")


def write_stack(path, layers, epsg, transform, meta):
    import rasterio
    h, w = layers[0].shape
    prof = dict(driver="GTiff", width=w, height=h, count=4, dtype="float32", crs=f"EPSG:{epsg}",
                transform=transform, nodata=np.nan, compress="deflate", predictor=3, tiled=True,
                blockxsize=512, blockysize=512, BIGTIFF="IF_SAFER")
    with rasterio.open(path, "w", **prof) as dst:
        for i, (arr, name) in enumerate(zip(layers, BANDS), start=1):
            dst.write(arr, i)
            dst.set_band_description(i, name)
        dst.update_tags(**{k: str(v) for k, v in meta.items()})


# ---------------------------------------------------------------- 5. contract check
def check_contract(path, bbox):
    """Verify the output matches what 01_mapping.py expects. Raises on a mismatch, returns a summary."""
    import rasterio
    with rasterio.open(path) as s:
        a = s.read()
        info = {"file": os.path.basename(path), "bands": list(s.descriptions), "crs": str(s.crs),
                "pixel_m": [round(s.res[0], 3), round(s.res[1], 3)], "shape": [s.height, s.width],
                "dtype": s.dtypes[0],
                "valid_pct": {b: round(float(np.isfinite(a[i]).mean() * 100), 1) for i, b in enumerate(BANDS)},
                "median_db": {b: (round(float(np.nanmedian(a[i])), 2) if np.isfinite(a[i]).any() else None)
                              for i, b in enumerate(BANDS)}}
    assert info["bands"] == BANDS, f"band order {info['bands']} != {BANDS}"
    assert info["crs"] == f"EPSG:{utm_epsg(bbox)}", f"CRS {info['crs']} != EPSG:{utm_epsg(bbox)}"
    assert info["pixel_m"] == [PIXEL_M, PIXEL_M], f"pixel size {info['pixel_m']}"
    assert info["dtype"] == "float32"
    for b, m in info["median_db"].items():
        assert m is None or -35 < m < 5, f"{b} median {m} dB looks wrong (expected roughly -25 to 0 dB)"
    say(f"contract OK: {info['file']} {info['shape']} px, valid % {info['valid_pct']}, median dB {info['median_db']}")
    return info


# ---------------------------------------------------------------- 6. one pair end-to-end
def build_pair(bbox, pair, out_dir, cache_dir="grd_cache", gpt=None, keep_zips=True):
    """pair = {'orbit': 85, 'direction': 'ASCENDING', 'pre': '2026-08-16', 'post': '2026-08-28'} -> stack path."""
    epsg = utm_epsg(bbox)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"s1_orbit{pair['orbit']}_{pair['direction'].lower()}.tif")
    if os.path.exists(out):
        say(f"cached {out}")
        return out
    transform, width, height = target_grid(bbox, epsg)
    layers, meta = [], {"source": "Sentinel-1 IW GRDH (Copernicus Data Space), processed by HimRaksha grd_preprocess.py",
                        "chain": "Apply-Orbit-File > ThermalNoiseRemoval > Remove-GRD-Border-Noise > Calibration(sigma0) "
                                 "> Subset > Terrain-Correction(Copernicus 30m DEM, 10 m) > mosaic > dB",
                        "orbit": pair["orbit"], "direction": pair["direction"]}
    for when in ("pre", "post"):
        day = str(pair[when])
        slices = search_slices(bbox, pair["orbit"], pair["direction"], day)
        if not slices:
            raise RuntimeError(f"no GRD slices for orbit {pair['orbit']} {pair['direction']} on {day}")
        say(f"{when} {day}: {len(slices)} slice(s) - " + ", ".join(s["name"][17:48] for s in slices))
        meta[f"{when}_products"] = ";".join(s["name"] for s in slices)
        tcs = []
        for s in slices:
            z = download_slice(s, os.path.join(cache_dir, "zips"))
            tc = process_slice(z, bbox, epsg, os.path.join(cache_dir, "tc"), gpt)
            if tc:
                tcs.append(tc)
            if not keep_zips:
                os.remove(z)
        if not tcs:
            raise RuntimeError(f"none of the {when} slices overlap the area")
        vv, vh = mosaic_date(tcs, epsg, transform, width, height)
        layers += [to_db(vv), to_db(vh)]
    write_stack(out, layers, epsg, transform, meta)
    check_contract(out, bbox)
    return out


# ---------------------------------------------------------------- 7. check against the old openEO output
def compare(grd_tif, openeo_tif, out_json=None, fig_png=None):
    """Per-band agreement between our stack and the openEO one (same pair). Small offsets are expected."""
    import rasterio
    from rasterio.warp import reproject, Resampling
    with rasterio.open(grd_tif) as g:
        A, tr, crs = g.read().astype("float32"), g.transform, g.crs
    with rasterio.open(openeo_tif) as o:
        B = np.full_like(A, np.nan)
        for i in range(4):
            reproject(o.read(i + 1).astype("float32"), B[i], src_transform=o.transform, src_crs=o.crs, src_nodata=np.nan,
                      dst_transform=tr, dst_crs=crs, dst_nodata=np.nan, resampling=Resampling.bilinear)
    res = {}
    for i, b in enumerate(BANDS):
        m = np.isfinite(A[i]) & np.isfinite(B[i]) & (B[i] > DB_FLOOR)
        if m.sum() < 100:
            res[b] = {"pixels": int(m.sum())}
            continue
        d = A[i][m] - B[i][m]
        res[b] = {"pixels": int(m.sum()), "median_diff_db": round(float(np.median(d)), 2),
                  "mad_diff_db": round(float(np.median(np.abs(d - np.median(d)))), 2),
                  "correlation": round(float(np.corrcoef(A[i][m], B[i][m])[0, 1]), 3)}
    if out_json:
        json.dump(res, open(out_json, "w"), indent=1)
    if fig_png:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(15, 5))
        for k, (arr, t) in enumerate([(A[2], "ours (raw GRD) post VV"), (B[2], "openEO post VV")]):
            ax[k].imshow(arr, cmap="gray", vmin=-25, vmax=0); ax[k].set_title(t); ax[k].axis("off")
        m = np.isfinite(A[2]) & np.isfinite(B[2])
        idx = np.random.default_rng(0).choice(np.flatnonzero(m), min(20000, int(m.sum())), replace=False) if m.any() else []
        ax[2].scatter(B[2].ravel()[idx], A[2].ravel()[idx], s=1, alpha=0.3)
        ax[2].plot([-30, 5], [-30, 5], "r-"); ax[2].set_xlabel("openEO dB"); ax[2].set_ylabel("ours dB")
        ax[2].set_title("post VV agreement")
        fig.tight_layout(); fig.savefig(fig_png, dpi=110); plt.close(fig)
    say("agreement: " + json.dumps(res))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Raw Sentinel-1 GRD -> HimRaksha before/after stack")
    ap.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    ap.add_argument("--orbit", type=int, required=True)
    ap.add_argument("--direction", choices=["ASCENDING", "DESCENDING"], required=True)
    ap.add_argument("--pre", required=True, help="pre-event acquisition date YYYY-MM-DD")
    ap.add_argument("--post", required=True, help="post-event acquisition date YYYY-MM-DD")
    ap.add_argument("--out", required=True, help="run folder")
    ap.add_argument("--cache", default="grd_cache")
    ap.add_argument("--delete-zips", action="store_true")
    a = ap.parse_args(argv)
    load_env()
    pair = {"orbit": a.orbit, "direction": a.direction, "pre": a.pre, "post": a.post}
    build_pair(a.bbox, pair, a.out, a.cache, keep_zips=not a.delete_zips)


if __name__ == "__main__":
    sys.exit(main())
