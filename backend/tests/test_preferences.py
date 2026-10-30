import numpy as np
import time
from PIL import Image

from frame.models import Photo
from frame.preferences import train


def test_small_data_fallback_and_deterministic_training():
    a, b = np.ones(6), np.zeros(6)
    assert train([(a, b, "a"), (a, b, "a")]) is None
    rows = [(a, b, "a"), (a, b, "a"), (b, a, "b"), (a, b, "equal")]
    assert train(rows) == train(rows)
    assert train(rows)["examples"] == 3


def test_contradictory_preferences_remain_finite():
    a, b = np.ones(6), np.zeros(6)
    model = train([(a, b, "a"), (a, b, "b"), (a, b, "a")])
    assert model and all(abs(weight) < 10 for weight in model["weights"])


def test_review_budget_profiles_feedback_and_undo(client, tmp_path):
    source = tmp_path / "photos"; source.mkdir()
    for index, colour in enumerate(("red", "green", "blue")): Image.new("RGB", (80, 60), colour).save(source / f"{index}.jpg")
    client.post("/api/scans", json={"path": str(source)})
    for _ in range(100):
        if client.get("/api/scans/current").json()["state"] != "running": break
        time.sleep(.01)
    queue = client.get("/api/review/queue?budget=2").json()
    assert len(queue["items"]) == 2 and queue["items"][0]["label"] == "Heuristic review priority"
    first = client.post("/api/preferences/profiles", json={"name": "Ishaan"}).json()
    second = client.post("/api/preferences/profiles", json={"name": "Guest"}).json()
    assert first["id"] != second["id"]
    pair = client.get(f"/api/preferences/profiles/{first['id']}/pair").json()["pair"]
    for choice in ("a", "b", "a"):
        response = client.post(f"/api/preferences/profiles/{first['id']}/feedback", json={"photo_a_id": pair["a"]["id"], "photo_b_id": pair["b"]["id"], "choice": choice})
        assert response.status_code == 200
        pair = client.get(f"/api/preferences/profiles/{first['id']}/pair").json()["pair"] or pair
    exported = client.get(f"/api/preferences/profiles/{first['id']}/export").json()
    assert exported["format_version"] == 1 and len(exported["feedback"]) == 3
    assert client.delete(f"/api/preferences/profiles/{first['id']}/feedback/latest").status_code == 200
    assert client.get(f"/api/preferences/profiles/{second['id']}/export").json()["feedback"] == []
