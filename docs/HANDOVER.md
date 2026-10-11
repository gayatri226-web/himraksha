# HimRaksha - handover and team workflow

## Who does what
| Person | Owns | Branch |
|---|---|---|
| Partner (GIS / SAR) | `pipeline/grd_preprocess.py` (raw GRD processing), `eval/` (EMSR927, JRC comparison), Sentinel-2 layer | `grd-pipeline`, `eval` |
| Anurag (ML + product) | model evaluation on other datasets, `app/` dashboard, README, report, video | `dashboard` |

`main` must always run. Nobody pushes to `main` directly: work on your branch, open a Pull Request, the other person reviews and merges.

## Organizer answers (keep - they justify our choices)
1. ImageNet-pretrained encoder + Kuro Siwo is allowed - **show results on the other listed datasets too**.
2. We must **process the raw Sentinel-1 GRD ourselves** (not ready-made backscatter from a server).
3. Make a video and document account setup; live judging may use our own account.
4. JRC Global Surface Water is allowed - **compare with and without it and justify**.
5. Registration: Devpost only.

## Task 1 (partner, top priority): raw GRD -> analysis-ready Sentinel-1 stack
Replaces the openEO `sar_backscatter` step in `pipeline/01_mapping.py`. Keep everything else as is.

### Steps the code must do
1. Use the existing pair search (`find_pairs` in `01_mapping.py`) - same orbit, pre and post dates.
2. Download the matching **GRD (IW, GRDH) `.SAFE`** products from the Copernicus Data Space catalogue (OData), using
   username/password from `.env` (`CDSE_USER`, `CDSE_PASSWORD`). Never hard-code credentials.
3. Process each scene with our own chain: precise orbit -> thermal noise removal -> radiometric calibration (sigma0) ->
   subset to the area -> terrain correction with Copernicus DEM GLO-30 -> resample to 10 m in the area's UTM zone -> dB.
   Suggested tool: `sarsen` (pure Python) or ESA SNAP `gpt`; document the choice in the README.
4. **No speckle filter here** - `01_mapping.py` already despeckles later; filtering twice changes the results.

### Output contract (must match exactly, so nothing downstream breaks)
| Item | Value |
|---|---|
| File | `<run_folder>/s1_orbit<NN>_<ascending|descending>.tif` (one per orbit pair) |
| Bands (order) | 1 `pre_VV`, 2 `pre_VH`, 3 `post_VV`, 4 `post_VH` |
| Units | sigma0 in **dB**, float32, nodata = NaN |
| Grid | UTM zone from `utm_epsg(bbox)`, 10 m pixels, covering the bbox |

### Done means
- [ ] `pipeline/grd_preprocess.py` runs for Trishuli (orbits 85 asc + 19 desc) from Colab with only `.env` credentials.
- [ ] Output files pass the contract above (script prints bands, CRS, pixel size, % valid pixels).
- [ ] Side-by-side check vs the old openEO output on the same pair: median difference and correlation per band
      (save as `eval/grd_vs_openeo.json` + one figure). Small differences are fine - explain them.
- [ ] `notebooks/himraksha_full_run.ipynb` mapping cell switched to the new step; full run still produces `impact_fused.tif`.
- [ ] Pull Request `grd-pipeline -> main` with a short description.

## Task 2 (partner): evaluation - `eval/` only
- EMSR927: download EMS vectors into `eval/data/` (git-ignored), compare with `impact_fused.tif` inside EMS areas:
  precision, recall, F1, IoU (all / high-confidence / water-only) -> `eval/eval_emsr927.json`.
- JRC Global Surface Water: run Trishuli with and without the JRC permanent-water mask, score both vs EMSR927, keep the better one.
- **Data rule:** EMS / UNOSAT / post-event OSM are for checking only - never inputs, never for tuning. Breaking it = disqualification.

## Task 3 (Anurag): model on other datasets
Score `flood_unet.onnx` on the test splits of the other allowed datasets -> `eval/eval_datasets.json` (F1, IoU per dataset).

## Setup (both)
1. Python 3.11, `pip install -r requirements.txt`.
2. Copy `.env.example` to `.env`, fill in your own values (Copernicus Data Space account: https://dataspace.copernicus.eu,
   ohsome key: https://account.heigit.org). `.env` is git-ignored.
3. Model files (`flood_unet.onnx`, `norm.json`) from the shared Drive folder into `models/`.

## Gotchas
- Colab disk is wiped on disconnect - download run zips. College Google accounts block Drive mounting - use a personal Gmail.
- A GRD scene is ~1 GB; 4 scenes per run. Subset early to keep processing fast.
- Search the code for keys before every commit. Git history keeps deleted secrets forever.
