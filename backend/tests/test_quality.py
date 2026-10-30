from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from frame.quality import MEASUREMENT_VERSION, QualityConfig, measure, recommend


def save(path: Path, array: np.ndarray) -> Path:
    Image.fromarray(array.astype(np.uint8)).save(path)
    return path


def checker(size=256) -> np.ndarray:
    y, x = np.indices((size, size))
    gray = (((x // 8 + y // 8) % 2) * 255).astype(np.uint8)
    return np.dstack([gray] * 3)


def test_sharp_scores_above_gaussian_blur(tmp_path):
    sharp = checker()
    blurred = cv2.GaussianBlur(sharp, (0, 0), 6)
    a = measure(save(tmp_path / "sharp.png", sharp))
    b = measure(save(tmp_path / "blur.png", blurred))
    assert a["tenengrad"] > b["tenengrad"] * 5
    assert a["laplacian_variance"] > b["laplacian_variance"]


def test_blur_strength_is_monotonic_for_fixture(tmp_path):
    values = []
    for index, sigma in enumerate((0, 2, 7)):
        pixels = checker()
        if sigma:
            pixels = cv2.GaussianBlur(pixels, (0, 0), sigma)
        values.append(measure(save(tmp_path / f"{index}.png", pixels))["tenengrad"])
    assert values[0] > values[1] > values[2]


def test_exposure_clipping_grayscale_and_resolution(tmp_path):
    dark = measure(save(tmp_path / "dark.png", np.full((200, 200), 2)))
    bright = measure(save(tmp_path / "bright.png", np.full((200, 200), 255)))
    gradient = np.tile(np.arange(256, dtype=np.uint8), (256, 1))
    gray = measure(save(tmp_path / "gray.png", gradient))
    large = measure(save(tmp_path / "large.png", np.repeat(np.repeat(checker(128), 4, 0), 4, 1)))
    assert dark["mean_luminance"] < gray["mean_luminance"] < bright["mean_luminance"]
    assert bright["highlight_clipping"] == pytest.approx(1)
    assert dark["shadow_clipping"] > 0
    assert 0 <= large["noise_estimate"] <= 1
    assert large["measurement_version"] == MEASUREMENT_VERSION


def test_uniform_image_is_review_not_default_reject(tmp_path):
    metrics = measure(save(tmp_path / "uniform.png", np.full((100, 100, 3), 90)))
    decision = recommend(metrics)
    assert decision["recommendation"] == "REVIEW"
    assert "Very low detail" in decision["primary_problems"]
    assert "not a calibrated probability" in decision["score_note"]


def test_shallow_depth_style_fixture_notes_concentration(tmp_path):
    pixels = np.full((300, 300, 3), 120, np.uint8)
    pixels[100:180, 100:180] = checker(80)
    decision = recommend(measure(save(tmp_path / "subject.png", pixels)))
    assert any("shallow depth" in text for text in decision["explanations"])
    assert decision["recommendation"] != "REJECT"


def test_corrupt_and_invalid_config(tmp_path):
    bad = tmp_path / "bad.jpg"; bad.write_bytes(b"broken")
    with pytest.raises(ValueError):
        measure(bad)
    with pytest.raises(ValueError):
        QualityConfig(blur_sensitivity=1.2)
    with pytest.raises(ValueError):
        QualityConfig(version=99)
