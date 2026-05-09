import os
import sys


def test_v9_pkl_exists():
    ml_dir = os.path.join(os.path.dirname(__file__), "..", "ml")
    pkl_path = os.path.join(ml_dir, "route_predictor_v9.pkl")
    assert os.path.exists(pkl_path), f"v9 pickle not found at {pkl_path}"


def test_v9_pkl_loads_through_inference_module():
    """Verify inference module's internal pickle loading works."""
    from ml.inference import _get_predictor

    predictor = _get_predictor()
    assert predictor is not None
    assert hasattr(predictor, "clf"), "predictor missing classifier"
    assert hasattr(predictor, "reg"), "predictor missing regressor"


def test_v9_pkl_loads_without_syspath_hack():
    """Verify v9 pickle loads from project root without ml sys.path hacks."""
    import joblib

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    pkl_path = os.path.join(project_root, "ml", "route_predictor_v9.pkl")

    predictor = joblib.load(pkl_path)

    assert predictor is not None
    assert hasattr(predictor, "clf"), "predictor missing classifier"
    assert hasattr(predictor, "reg"), "predictor missing regressor"


def test_ml_inference_imports():
    from ml.inference import predict_route

    assert callable(predict_route)


def test_model_info_endpoint_shape():
    from ml.inference import get_model_info

    info = get_model_info()
    assert "model_version" in info
    assert "evaluation" in info
    assert "n_clf_features" in info