import sys, os

print("Python executable:", sys.executable)
try:
    import xgboost
    print("XGBoost import SUCCESS! version:", xgboost.__version__)
except Exception as e:
    print("XGBoost import FAILED:", type(e), e)

# Test joblib load of demand_v2.joblib
model_path = os.path.join(os.path.dirname(__file__), "..", "saved_models", "demand_v2.joblib")
print("Model path:", model_path, "exists:", os.path.exists(model_path))
if os.path.exists(model_path):
    try:
        import joblib
        art = joblib.load(model_path)
        print("joblib load SUCCESS! art keys:", art.keys() if isinstance(art, dict) else type(art))
    except Exception as e:
        print("joblib load FAILED:", type(e), e)
