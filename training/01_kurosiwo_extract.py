# Kuro Siwo train_GRD -> compact training chips (FULL RUN, streams all 5 shards)
import io, os, json, random, tarfile, requests, numpy as np

BASE = "https://huggingface.co/datasets/orion-ai-lab/Kuro-Siwo-Webdataset/resolve/main"
SHARDS = [f"train_GRD/shard-{i:05d}.tar" for i in range(5)]
OUT = "/kaggle/working/ks_chips"
MAX_SAMPLES = None     # None = process everything
KEEP_DRY = 0.30        # keep 30% of chips that contain no flood pixels
NEED = ("flood_vv", "flood_vh", "sec1_vv", "sec1_vh", "dem", "mask", "valid_mask", "info")

os.makedirs(OUT, exist_ok=True)
random.seed(0)
stats = {"seen": 0, "kept": 0, "flood_chips": 0, "skipped_incomplete": 0}


def process(shard_i, key, s):
    """Convert one sample and save it. Returns True if kept."""
    path = f"{OUT}/{shard_i}_{key}.npz"
    if os.path.exists(path):
        return True
    if not all(k in s for k in NEED):
        stats["skipped_incomplete"] += 1
        return False
    mask = s["mask"].astype(np.uint8)
    mask[s["valid_mask"] < 0.5] = 255                     # 255 = ignore during training
    has_flood = bool((mask == 2).any())
    if not has_flood and random.random() > KEEP_DRY:
        return False
    sar = np.stack([s["sec1_vv"], s["sec1_vh"], s["flood_vv"], s["flood_vh"]])   # pre VV, pre VH, post VV, post VH
    sar = (10 * np.log10(np.clip(sar, 1e-5, None))).astype(np.float16)          # linear -> dB (matches GEE)
    np.savez_compressed(path, sar=sar, dem=s["dem"].astype(np.float16), mask=mask, info=s["info"])
    stats["flood_chips"] += has_flood
    return True


def done():
    return MAX_SAMPLES is not None and stats["seen"] >= MAX_SAMPLES


for shard_i, shard in enumerate(SHARDS):
    if done():
        break
    print(f"\n--- streaming {shard}", flush=True)
    try:
        r = requests.get(f"{BASE}/{shard}", stream=True, timeout=300)
        r.raise_for_status()
        r.raw.decode_content = True
        cur_key, sample = None, {}
        with tarfile.open(fileobj=r.raw, mode="r|") as tar:
            for m in tar:
                if not m.isfile():
                    continue
                parts = m.name.split(".")
                key, field = parts[0], parts[1]
                if key != cur_key and sample:              # previous sample complete
                    stats["seen"] += 1
                    stats["kept"] += process(shard_i, cur_key, sample)
                    sample = {}
                    if stats["seen"] % 2000 == 0:
                        print(f"seen {stats['seen']}  kept {stats['kept']}  flood chips {stats['flood_chips']}", flush=True)
                    if done():
                        break
                cur_key = key
                if field not in NEED:
                    continue
                data = tar.extractfile(m).read()
                sample[field] = data.decode() if field == "info" else np.load(io.BytesIO(data))[0]
        if sample and not done():                          # last sample of the shard
            stats["seen"] += 1
            stats["kept"] += process(shard_i, cur_key, sample)
        r.close()
        print(f"finished {shard}: total seen {stats['seen']}, kept {stats['kept']}", flush=True)
    except Exception as e:
        print(f"!! {shard} stopped: {e}  -> re-run later; saved files are skipped", flush=True)

used_gb = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT)) / 1e9
print(f"\nkept {stats['kept']} of {stats['seen']}  |  flood chips {stats['flood_chips']}  |  "
      f"incomplete {stats['skipped_incomplete']}  |  disk used {used_gb:.2f} GB")

# sanity check one saved chip
f = sorted(os.listdir(OUT))[0]
z = np.load(os.path.join(OUT, f))
print(f, {k: (z[k].shape, z[k].dtype) for k in ("sar", "dem", "mask")})
print("dB range pre VV:", float(z["sar"][0].min()), float(z["sar"][0].max()),
      "| mask values:", np.unique(z["mask"]))
print("info:", json.loads(str(z["info"]))["sources"].keys())
