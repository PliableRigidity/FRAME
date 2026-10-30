from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MEASUREMENT_VERSION = "quality-1"


@dataclass(frozen=True)
class QualityConfig:
    version: int = 1
    blur_sensitivity: float = 0.5
    underexposure_tolerance: float = 0.65
    highlight_clipping_tolerance: float = 0.02
    noise_sensitivity: float = 0.5
    rejection_conservativeness: float = 0.85

    def __post_init__(self) -> None:
        if self.version != 1:
            raise ValueError("Unsupported quality configuration version")
        for name in ("blur_sensitivity", "underexposure_tolerance", "noise_sensitivity", "rejection_conservativeness"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not 0 <= self.highlight_clipping_tolerance <= 0.25:
            raise ValueError("highlight_clipping_tolerance must be between 0 and 0.25")

    @property
    def decision_key(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def measure(path: Path, max_dimension: int = 1600) -> dict[str, Any]:
    data = np.fromfile(path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError("Image pixels could not be decoded")
    if image.ndim == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    elif image.shape[2] == 4:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    if rgb.dtype != np.uint8:
        rgb = cv2.normalize(rgb, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    height, width = rgb.shape[:2]
    scale = min(1.0, max_dimension / max(height, width))
    if scale < 1:
        rgb = cv2.resize(rgb, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0

    laplacian = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    lap_var = float(laplacian.var())
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    gradient_sq = gx * gx + gy * gy
    tenengrad = float(np.mean(gradient_sq))
    cells = []
    for y in np.array_split(np.arange(gray.shape[0]), min(4, gray.shape[0])):
        for x in np.array_split(np.arange(gray.shape[1]), min(4, gray.shape[1])):
            cells.append(float(np.mean(gradient_sq[np.ix_(y, x)])))
    regional_p90 = float(np.percentile(cells, 90))
    regional_median = float(np.median(cells))
    detail_concentration = regional_p90 / (float(np.mean(cells)) + 1e-8)
    detailed_region_fraction = float(np.mean(np.asarray(cells) > max(0.002, regional_p90 * 0.25)))

    pixels = gray.ravel()
    percentiles = np.percentile(pixels, [1, 5, 50, 95, 99])
    channel_low = np.mean(rgb <= 2, axis=(0, 1))
    channel_high = np.mean(rgb >= 253, axis=(0, 1))
    shadow_clipping = float(np.mean(pixels <= 2 / 255))
    highlight_clipping = float(np.mean(pixels >= 253 / 255))

    smooth = cv2.GaussianBlur(gray, (0, 0), 1.0)
    residual = gray - smooth
    # Robust residual estimate, discounted where gradients imply real texture.
    mad = float(np.median(np.abs(residual - np.median(residual))))
    texture = float(np.median(np.sqrt(gradient_sq)))
    noise_estimate = max(0.0, 1.4826 * mad - 0.08 * texture)
    entropy_hist = cv2.calcHist([(gray * 255).astype(np.uint8)], [0], None, [256], [0, 256]).ravel()
    probabilities = entropy_hist / max(1, entropy_hist.sum())
    entropy = float(-np.sum(probabilities[probabilities > 0] * np.log2(probabilities[probabilities > 0])))

    return {
        "measurement_version": MEASUREMENT_VERSION,
        "working_width": int(gray.shape[1]), "working_height": int(gray.shape[0]),
        "laplacian_variance": lap_var, "tenengrad": tenengrad,
        "regional_sharpness_p90": regional_p90, "regional_sharpness_median": regional_median,
        "detail_concentration": detail_concentration, "detailed_region_fraction": detailed_region_fraction,
        "mean_luminance": float(np.mean(pixels)), "median_luminance": float(percentiles[2]),
        "luminance_p01": float(percentiles[0]), "luminance_p05": float(percentiles[1]),
        "luminance_p95": float(percentiles[3]), "luminance_p99": float(percentiles[4]),
        "dynamic_range_p98": float(percentiles[4] - percentiles[0]),
        "shadow_clipping": shadow_clipping, "highlight_clipping": highlight_clipping,
        "channel_shadow_clipping": [float(v) for v in channel_low],
        "channel_highlight_clipping": [float(v) for v in channel_high],
        "noise_estimate": noise_estimate, "entropy": entropy,
    }


def recommend(metrics: dict[str, Any], config: QualityConfig = QualityConfig()) -> dict[str, Any]:
    primary: list[str] = []
    secondary: list[str] = []
    explanations: list[str] = []
    mean = metrics["mean_luminance"]
    p95 = metrics["luminance_p95"]
    p05 = metrics["luminance_p05"]
    sharp = math.sqrt(max(0.0, metrics["tenengrad"]))
    detail_fraction = metrics["detailed_region_fraction"]

    severe_dark = mean < 0.035 and p95 < 0.12
    dark = mean < (0.11 - 0.045 * config.underexposure_tolerance) and p95 < 0.42
    severe_bright = mean > 0.975 and p05 > 0.92
    clipping = metrics["highlight_clipping"] > config.highlight_clipping_tolerance
    severe_clip = metrics["highlight_clipping"] > max(0.35, config.highlight_clipping_tolerance * 8) and mean > 0.88
    low_detail = sharp < (0.035 + 0.035 * config.blur_sensitivity) and detail_fraction < 0.32
    absent_detail = sharp < 0.018 and metrics["regional_sharpness_p90"] < 0.002
    noisy = metrics["noise_estimate"] > (0.045 - 0.02 * config.noise_sensitivity)

    if severe_dark:
        primary.append("Severe underexposure risk"); explanations.append("Almost all measured luminance is near black; review whether this is an intentional dark scene.")
    elif dark:
        secondary.append("Underexposure risk"); explanations.append("The luminance distribution is dark, but FRAME does not assume that a night scene is defective.")
    if severe_bright or severe_clip:
        primary.append("Severe highlight loss risk"); explanations.append("A large portion of the image is near white with little measured highlight detail.")
    elif clipping:
        secondary.append("Highlight clipping"); explanations.append("Some highlights reach the measurable clipping range; bright skies and lights can make this intentional.")
    if absent_detail:
        primary.append("Very low detail"); explanations.append("Useful edge energy is nearly absent across every measured region.")
    elif low_detail:
        secondary.append("Focus or motion-blur risk"); explanations.append("Fine detail is weak across the frame; this is a focus-risk indicator, not subject-focus detection.")
    elif metrics["detail_concentration"] > 3.0:
        explanations.append("Detail is concentrated in a small part of the frame, which can be consistent with intentional shallow depth of field.")
    if noisy:
        secondary.append("Elevated noise estimate"); explanations.append("High-frequency residuals suggest noise, though fine texture can produce a similar measurement.")

    severe_count = sum((severe_dark, severe_bright or severe_clip, absent_detail))
    recommendation = "REVIEW" if primary or secondary else "KEEP"
    # REJECT needs two independent severe signals, or one extreme signal under a less-conservative policy.
    extreme = (severe_dark and mean < 0.012) or (severe_bright and mean > 0.992)
    if severe_count >= 2 or (extreme and config.rejection_conservativeness < 0.55):
        recommendation = "REJECT"
    exposure_component = min(1.0, max(0.0, (metrics["dynamic_range_p98"] / 0.65)))
    sharp_component = min(1.0, sharp / 0.15)
    clipping_component = max(0.0, 1 - metrics["highlight_clipping"] * 2 - metrics["shadow_clipping"])
    noise_component = max(0.0, 1 - metrics["noise_estimate"] * 10)
    score = round(100 * (0.4 * sharp_component + 0.25 * exposure_component + 0.2 * clipping_component + 0.15 * noise_component), 1)
    if not explanations:
        explanations.append("No strong conventional technical defect indicators were measured.")
    return {"technical_score": score, "recommendation": recommendation, "primary_problems": primary, "secondary_problems": secondary, "explanations": explanations, "decision_config": asdict(config), "score_note": "Heuristic technical index; not a calibrated probability."}


def analyse(path: Path, config: QualityConfig = QualityConfig()) -> dict[str, Any]:
    metrics = measure(path)
    return {"metrics": metrics, "decision": recommend(metrics, config)}
