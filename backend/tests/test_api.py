import time
from PIL import Image


def wait(client):
    for _ in range(100):
        state = client.get("/api/scans/current").json()
        if state["state"] != "running":
            return state
        time.sleep(0.01)
    raise AssertionError("scan did not finish")


def test_health_and_invalid_path(client, tmp_path):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/scans", json={"path": str(tmp_path / "missing")}).status_code == 400


def test_scan_gallery_and_thumbnail(client, tmp_path):
    source = tmp_path / "photos"; source.mkdir()
    Image.new("RGB", (100, 80), "blue").save(source / "photo.jpg")
    assert client.post("/api/scans", json={"path": str(source)}).status_code == 202
    assert wait(client)["state"] == "completed"
    gallery = client.get("/api/photos").json()
    assert gallery["total"] == 1
    item = gallery["items"][0]
    assert item["assets"][0]["path"] == str((source / "photo.jpg").resolve())
    assert item["recommendation"] in {"KEEP", "REVIEW", "REJECT"}
    assert item["analysis"]["metrics"]["measurement_version"] == "quality-1"
    thumb = client.get(f"/api/photos/{item['id']}/thumbnail")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"


def test_config_recomputes_decisions_without_measurement(client, tmp_path):
    source = tmp_path / "photos"; source.mkdir()
    Image.new("RGB", (100, 80), "gray").save(source / "photo.jpg")
    client.post("/api/scans", json={"path": str(source)})
    assert wait(client)["state"] == "completed"
    config = client.get("/api/quality/config").json()
    config["blur_sensitivity"] = 0.8
    response = client.put("/api/quality/config", json=config)
    assert response.status_code == 200
    assert response.json() == {"config": config, "recommendations_updated": 1, "measurements_rerun": False}


def test_invalid_config_rejected(client):
    config = client.get("/api/quality/config").json()
    config["noise_sensitivity"] = -1
    assert client.put("/api/quality/config", json=config).status_code == 422
