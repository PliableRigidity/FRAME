import json
from pathlib import Path

import pytest

from frame.ml import MLSettings, ModelUnavailable, OnnxQualityModel, hybrid_score


def test_hybrid_scoring_and_missing_learned_fallback():
    assert hybrid_score(80, 60, MLSettings("hybrid", "cpu", .75, .25)) == 75
    assert hybrid_score(80, None, MLSettings("hybrid")) == 80
    assert hybrid_score(80, None, MLSettings("learned")) == 80


def test_invalid_ml_settings():
    with pytest.raises(ValueError): MLSettings(mode="magic")
    with pytest.raises(ValueError): MLSettings(classical_weight=0, learned_weight=0)


def test_missing_and_incorrect_weights(tmp_path):
    manifest = tmp_path / "model.json"
    manifest.write_text(json.dumps({"identity": "test-iqa", "version": "1", "weights": "missing.onnx", "sha256": "0" * 64}))
    with pytest.raises(ModelUnavailable, match="missing"): OnnxQualityModel(manifest, "cpu")
    (tmp_path / "missing.onnx").write_bytes(b"wrong")
    with pytest.raises(ModelUnavailable, match="checksum"): OnnxQualityModel(manifest, "cpu")


def test_ml_api_reports_optional_model(client):
    status = client.get("/api/ml/status").json()
    assert status["installed"] is False and status["settings"]["mode"] == "classical"
    assert client.post("/api/ml/analyse").status_code == 409
    response = client.put("/api/ml/settings", json={"mode": "hybrid", "device": "cpu", "classical_weight": .7, "learned_weight": .3})
    assert response.status_code == 200 and response.json()["inference_rerun"] is False
