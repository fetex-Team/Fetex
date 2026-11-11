import zipfile, json, os, glob

print("=== 1. saved_models snapshot_meta.json ===")
for p in sorted(glob.glob("saved_models/*/snapshot_meta.json")):
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
        print(f"[{os.path.basename(os.path.dirname(p))}]")
        for k, v in d.items():
            print(f"  {k}: {v}")

print("\n=== 2. Zip internal metadata ===")
models = glob.glob("saved_models/*/rl_reposition_model.zip") + ["module4_dispatch/rl_reposition_model.zip"]
for m in sorted(set(models)):
    if os.path.exists(m):
        try:
            with zipfile.ZipFile(m, "r") as z:
                if "data" in z.namelist():
                    d = json.loads(z.read("data").decode("utf-8", errors="ignore"))
                    print(f"{m}:")
                    print(f"  num_timesteps: {d.get('num_timesteps')}")
                    print(f"  _total_timesteps: {d.get('_total_timesteps')}")
                    print(f"  lr: {d.get('learning_rate')}")
        except Exception as e:
            print(f"{m}: {e}")

print("\n=== 3. Training logs or checkpoints ===")
for root, dirs, files in os.walk("."):
    if any(k in root for k in (".git", ".gemini", "__pycache__")):
        continue
    for f in files:
        if any(w in f.lower() for w in ("train", "progress", "checkpoint", "training")):
            if f.endswith((".log", ".txt", ".csv", ".json")):
                p = os.path.join(root, f)
                print(f"  {p} ({os.path.getsize(p)} bytes)")
