# Export the trained U-Net (best.pth) to ONNX + collect all final model files in one place
!pip install -q segmentation-models-pytorch onnx onnxscript onnxruntime

import glob, os, shutil, numpy as np, torch, onnxruntime as ort
import segmentation_models_pytorch as smp

OUT = "/kaggle/working"
pth = glob.glob("/kaggle/input/**/best.pth", recursive=True)
assert pth, "best.pth not found - Add Input > Notebooks > flood_unet_training.ipynb"
src = os.path.dirname(pth[0])
print("Using:", pth[0])

# same architecture as training; weights come from best.pth
model = smp.Unet("resnet34", encoder_weights=None, in_channels=6, classes=3)
model.load_state_dict(torch.load(pth[0], map_location="cpu"))
model.eval()

dummy = torch.randn(1, 6, 256, 256)
torch.onnx.export(model, dummy, f"{OUT}/flood_unet.onnx",
                  input_names=["x"], output_names=["logits"],
                  dynamic_axes={"x": {0: "b", 2: "h", 3: "w"}, "logits": {0: "b", 2: "h", 3: "w"}},
                  opset_version=17, dynamo=False)

# verify ONNX gives the same answer as PyTorch
sess = ort.InferenceSession(f"{OUT}/flood_unet.onnx")
with torch.no_grad():
    ref = model(dummy).numpy()
diff = float(np.abs(sess.run(None, {"x": dummy.numpy()})[0] - ref).max())
print("ONNX export OK, max difference vs PyTorch:", diff)

# copy the other training outputs next to the ONNX file
for name in ("best.pth", "norm.json", "metrics.json", "training_curves.png"):
    p = os.path.join(src, name)
    if os.path.exists(p):
        shutil.copy(p, OUT)
print("Final files:", sorted(f for f in os.listdir(OUT) if not f.startswith("__")))
