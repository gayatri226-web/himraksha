# models/
Put here (NOT committed - too big for git, share via Drive or a GitHub Release):
- `flood_unet.onnx` (~100 MB) - U-Net, ResNet-34 encoder, 6 inputs (pre/post VV+VH dB, DEM, slope), 3 classes
- `norm.json` - input normalisation (must match training)
- `metrics.json`, `training_curves.png` - small, OK to commit
