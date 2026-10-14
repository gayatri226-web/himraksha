# Model card — HimRaksha flood segmentation U-Net

The AI component of HimRaksha (Track B option "flood segmentation model"): a U-Net that finds **flood water** in a
pair of Sentinel-1 radar images taken before and after an event.

## Files
| File | What | In git? |
|---|---|---|
| `flood_unet.onnx` | The trained model, exported to ONNX so it runs on CPU (~100 MB) | No (too big). Attached to the GitHub Release. |
| `norm.json` | The input recipe: channel order and scaling. Must match training. | Yes |
| `metrics.txt` | Validation scores per epoch, held-out event IDs, chip counts (JSON) | Yes |
| `training_curves.png` | Loss and validation curves | Yes |
| `flood-unet-onnx-export.ipynb` | Re-export to ONNX from the PyTorch checkpoint | Yes |

Training code: `training/01_kurosiwo_extract.py` (chip extraction), `training/02_flood_unet_training.ipynb`
(training + evaluation, Kaggle GPU), `training/03_export_onnx.py`.

## Architecture
- U-Net with a **ResNet-34 encoder initialised from ImageNet** (`segmentation_models_pytorch`). The organizers
  confirmed that an ImageNet-pretrained encoder is allowed.
- **6 input channels:** pre-event VV dB, pre VH dB, post-event VV dB, post VH dB, relative DEM height, slope.
- **3 output classes:** 0 dry land, 1 permanent water, 2 **flood water**. HimRaksha uses class 2 as "new water".
- Loss: cross-entropy (flood class weighted ×2) + Dice. AdamW, one-cycle learning rate 5e-4, 15 epochs, batch 16,
  rotation/flip augmentation.

## Training data
- **Kuro Siwo** (Bountos et al., 2024): 22,972 chips of 256 × 256 px at 10 m, extracted from Sentinel-1 GRD
  before/after pairs with expert flood masks.
- **Split by flood event, not by chip:** 19,071 training chips; 3,901 validation chips from **7 events never seen in
  training** (Kuro Siwo event IDs 502, 492, 1111011, 554, 520, 1111008, 1111007).

## Scores

**On the 7 held-out Kuro Siwo events** (pixel-level, flood-water class):

| | Flood F1 | Precision | Recall | Flood IoU | Permanent-water IoU |
|---|---|---|---|---|---|
| Threshold baseline (dark after *and* ≥ 3 dB darker) | 0.588 | 0.810 | 0.462 | 0.417 | 0.282 |
| **U-Net (this model)** | **0.819** | **0.874** | **0.771** | **0.694** | **0.639** |

The checkpoint with the best flood IoU is kept. That was epoch 1; later epochs stayed between 0.67 and 0.69, so the
model is stable, but more training did not help on unseen events.

**On unseen Himalayan scenes** (`validation/model_only_check.json`, from `code/evaluation/model_only_check.py`):

| Check | Result |
|---|---|
| Trishuli 2026: U-Net water inside the Copernicus EMS EMSR927 affected area | **99.4 %** precision (0.326 km² of water, 0.324 km² inside EMS) |
| Trishuli 2026: share of the EMS area that is water | 4.3 % — EMS maps a **debris flow**, so most damage is debris, which the change-detection step maps, not the U-Net |
| Trishuli 2026: whole-system area F1 with / without the U-Net | **0.678 / 0.642** (high-confidence F1 0.424 / 0.377) |
| Trishuli 2026: U-Net water where Sentinel-2 was clear 2–3 weeks later | 15 % optical water, 30 % optical debris, 55 % no change (the water may have drained by then) |
| Chamoli 2021: U-Net water | 0.072 km², mostly low confidence and not confirmed by Sentinel-2 → treated as unconfirmed |

## Intended use and limits
- Built for **before/after Sentinel-1 GRD pairs from the same orbit**, terrain-corrected to 10 m, in dB. The input
  recipe is in `norm.json`; HimRaksha's `01_mapping.py` applies it.
- Detects **open water**. It does not detect debris, mud or damaged buildings; those come from the change-detection
  and Sentinel-2 steps of the pipeline.
- Not trained on Himalayan gorges. Steep slopes cause radar shadow and layover, where no radar-based method sees well.
- Not tested on another listed dataset (Sen1Floods11), which is optional in the brief.
- Educational prototype, not an operational tool.

## How to use it
```python
import json, numpy as np, onnxruntime as ort
sess = ort.InferenceSession("model/flood_unet.onnx", providers=["CPUExecutionProvider"])
norm = json.load(open("model/norm.json"))
# x: float32 array (1, 6, H, W) built with the recipe in norm.json (see make_input in training/02_*.ipynb)
logits = sess.run(None, {"x": x})[0]          # (1, 3, H, W)
classes = logits.argmax(1)[0]                 # 0 dry, 1 permanent water, 2 flood water
```

## Citation
Bountos, N. I., Sdraka, M., Zavras, A., et al. (2024). *Kuro Siwo: 33 billion m² under the water. A global
multi-temporal satellite dataset for rapid flood mapping.* NeurIPS 2024 Datasets and Benchmarks Track.
