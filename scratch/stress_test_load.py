import subprocess, sys, os

cmd = [
    sys.executable, "-c",
    "import joblib, os; "
    "p = os.path.join('saved_models', 'demand_v2.joblib'); "
    "art = joblib.load(p); "
    "print('Loaded successfully, version:', art.get('version'))"
]

env = os.environ.copy()
env["OMP_NUM_THREADS"] = "1"
env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
env["OPENBLAS_NUM_THREADS"] = "1"
env["MKL_NUM_THREADS"] = "1"

procs = []
for i in range(4):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    procs.append(p)

all_ok = True
for i, p in enumerate(procs):
    out, err = p.communicate()
    print(f"--- Process {i} (exit={p.returncode}) ---")
    if p.returncode == 0:
        print(" ", out.strip())
    else:
        print("  FAILED:", err.strip())
        all_ok = False

if all_ok:
    print("\nALL 4 CONCURRENT PROCESSES LOADED XGBOOST / DEMAND_V2 SUCCESSFULLY!")
