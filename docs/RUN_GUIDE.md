# HimRaksha — account setup and run guide

This guide takes you from nothing to a finished flood map for **any area and flood date**, starting from raw
satellite data. It needs a browser and three free accounts. No installation on your own computer is needed for
the run itself, which happens in Google Colab.

Total time: about **15 minutes of setup the first time**, then about **1 hour per area** (mostly downloading and
processing the radar images; you only wait).

---

## 1. Accounts (one time, all free)

| Account | Used for | How to get it |
|---|---|---|
| **Copernicus Data Space** | Downloading raw Sentinel-1 and Sentinel-2 images | Go to https://dataspace.copernicus.eu → *Login* → *Register*. Confirm the e-mail. |
| **ohsome API key** (HeiGIT) | Downloading OpenStreetMap as it was **before** the flood | Go to https://account.heigit.org/signup, create an account, then copy your API key. |
| **Google account** | Running the notebook in Google Colab | Any Google account. |

Notes:
- **Copernicus: keep two-step login off** for this account. The notebook logs in with e-mail and password and
  cannot answer a two-step prompt.
- You type the Copernicus password and the ohsome key into the notebook when it asks. **They are never saved to a
  file and never committed.** If Colab restarts, you type them again.
- The Copernicus download token expires after a few minutes. The pipeline asks for a new one before each
  download, so a long run does not fail because of this.

## 2. Files you need from this repository

Download these to your computer first. The notebook asks you to upload them.

| File | Where in the repo | Asked for in |
|---|---|---|
| `himraksha_full_run.ipynb` | `code/notebooks/` | Open this in Colab |
| `grd_preprocess.py` | `code/pipeline/` | Cell 2 |
| `flood_unet.onnx` + `norm.json` | `model/` (the `.onnx` file is ~100 MB, attached to the GitHub **Release** because it is too big for git) | Cell 3 |
| `s2_layer.py` | `code/pipeline/` | Cell 4b |
| `export_dashboard.py` | `code/dashboard/` | Cell 9 |

## 3. Open the notebook

1. Go to https://colab.research.google.com → *File* → *Upload notebook* → choose `himraksha_full_run.ipynb`.
2. A normal CPU runtime is enough. The model runs on CPU through ONNX, so no GPU is needed.

## 4. Run it, cell by cell

Run the cells **from top to bottom** (*Runtime* → *Run all* works too; it stops and waits whenever it asks for a
file or a password).

| Cell | What you do | What it does | Time |
|---|---|---|---|
| **1 CONFIG** | Change `BBOX` (west, south, east, north in degrees), `EVENT` (flood date) and `OSM_SNAPSHOT` (a date **before** the flood). Paste the ohsome key when asked. | Checks that the OSM date is before the flood. | — |
| **2 Tools** | Upload `grd_preprocess.py`. Type the Copernicus e-mail and password when asked. | Installs ESA SNAP and tests the Copernicus login. Stops with a clear message if the login fails. | 5–10 min |
| **3 Model** | Upload `flood_unet.onnx` and `norm.json`. | Loads the flood model. | 1 min |
| **4 Mapping** | Nothing. | Finds before/after radar images from the same orbit (ascending and descending), downloads the **raw** Sentinel-1 GRD files, processes them with SNAP, builds the valley mask from the Copernicus DEM, runs the U-Net and the change detection, and fuses both orbits. | 30–50 min |
| **4b Sentinel-2** | Upload `s2_layer.py`. | Finds the clearest optical images before and after, removes clouds and adds optical evidence. If no clear image exists, it says so and keeps the radar map. | 5–15 min |
| **5 OSM** | Nothing (uses the key from cell 1). | Downloads buildings, roads, bridges, footpaths, settlements and health facilities as they were on `OSM_SNAPSHOT`. | 3–5 min |
| **6 Damage** | Nothing. | Which buildings, roads and bridges were hit, with confidence. Bridges are graded *likely* / *possibly* damaged. | 1–3 min |
| **7 Access** | Nothing. | Which settlements lost their road connection to the nearest health facility or town, which still have a footpath, and which repairs reconnect the most people. | 1–5 min |
| **8 Save** | Nothing. | Downloads `trackb_run.zip` with all maps, GeoJSON layers and numbers. | 1 min |
| **9 Dashboard** | Upload `export_dashboard.py`. | Builds the dashboard data for this run and downloads `dashboard_<id>.zip`. | 1 min |

Tip: the Trishuli case study is already in the CONFIG cell:
```
BBOX = [85.10, 27.90, 85.45, 28.32]
EVENT = "2026-08-26"
OSM_SNAPSHOT = "2026-07-27"
```

Choosing an area: keep it to about **20–40 km across**. Larger areas take longer to download OSM data and to
compute cut-off routes. Use a box that follows the river valley you care about.

## 5. See the result in the dashboard

1. Unzip `dashboard_<id>.zip` into `code/dashboard/web/data/`. You get a folder `code/dashboard/web/data/<id>/`.
2. Run once (plain Python, no packages needed):
   ```
   python code/dashboard/export_dashboard.py --manifest code/dashboard/web
   ```
3. Open `code/dashboard/web/index.html` in a browser (double-click; no server needed). The new area is in the
   event list at the top. Press **Situation report** for the one-page sitrep, which you can print or save as PDF.

The dashboard's **New analysis** button does steps 1 of this guide for you: type the area and date, and it checks
them and writes the CONFIG text to copy into cell 1.

## 6. If something goes wrong

| Message / symptom | Meaning | What to do |
|---|---|---|
| `Copernicus login failed` | Wrong e-mail/password, or two-step login is on. | Run cell 2 again and retype; turn off two-step login. |
| No Sentinel-1 pair found | No radar images of the same orbit before and after the date. | Check the date; Sentinel-1 passes every 6–12 days. Cell 4 searches ±36 days. |
| `Upload [...] first` | A file from step 2 is missing. | Run the upload cell again and choose the file. |
| ohsome HTTP 429 | Too many requests for the free key. | Nothing; the cell waits and retries by itself. |
| No clear Sentinel-2 image | Clouds on all dates. | Nothing; the run continues with radar only (stated in the results). |
| Colab disconnected | Idle for too long, or the 12-hour limit. | Reconnect and run from cell 1 again. Radar stacks already made in that session are reused. |

## 7. Data rules (for the judges)

The pipeline only reads Sentinel-1, Sentinel-2, the Copernicus DEM, pre-event OpenStreetMap (snapshot before the
flood date, checked in cells 1 and 6) and the trained model. **Copernicus EMS, UNOSAT and post-event OSM are never
inputs.** They are only read by `code/evaluation/` to check the results afterwards.
