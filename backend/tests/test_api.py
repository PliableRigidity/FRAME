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


def test_groups_selection_overrides_and_export(client, tmp_path):
    source = tmp_path / "photos"; source.mkdir()
    pixels = Image.new("RGB", (100, 80), "blue")
    pixels.save(source / "a.jpg"); pixels.save(source / "b.jpg")
    client.post("/api/scans", json={"path": str(source)})
    assert wait(client)["state"] == "completed"
    groups = client.get("/api/groups").json()["items"]
    assert any(group["kind"] == "exact" for group in groups)
    photos = client.get("/api/photos").json()["items"]
    assert client.patch(f"/api/photos/{photos[0]['id']}/selection", json={"pinned": True}).status_code == 200
    selection = client.post("/api/selection", json={"target": 1, "method": "diversity", "profile": "travel"})
    assert selection.status_code == 200 and selection.json()["metrics"]["selected_count"] == 1
    destination = tmp_path / "export"
    exported = client.post("/api/selection/export", json={"destination": str(destination), "mode": "jpeg", "metadata": "json"})
    assert exported.status_code == 200 and exported.json()["complete"]
    assert (destination / "frame-selection.json").exists()
