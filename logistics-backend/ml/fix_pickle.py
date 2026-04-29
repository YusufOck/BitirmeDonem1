"""
One-time migration: re-save route_predictor_v6.pkl so _Log1pRegressor
resolves to model_classes._Log1pRegressor instead of __main__.
"""
import io
import sys
import os
import pickle
import joblib

_ML_DIR = os.path.dirname(os.path.abspath(__file__))
if _ML_DIR not in sys.path:
    sys.path.insert(0, _ML_DIR)

import model_classes

# Patch __main__ so the unpickler can resolve the old class reference.
sys.modules["__main__"]._Log1pRegressor = model_classes._Log1pRegressor

pkl = os.path.join(_ML_DIR, "route_predictor_v6.pkl")
predictor = joblib.load(pkl)
joblib.dump(predictor, pkl)
print(f"Re-saved {pkl}")
