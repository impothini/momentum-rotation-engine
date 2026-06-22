"""Inverse-volatility weighting for the two-asset case.

Weight bounds: [min_weight, max_weight] = [0.30, 0.70]

Formula:
    raw_weight_a = (1/vol_a) / (1/vol_a + 1/vol_b)
    w_a = clip(raw_weight_a, min_weight, max_weight)
    w_b = 1 - w_a
"""

from __future__ import annotations


def compute_inverse_vol_weights(
    vol_a: float,
    vol_b: float,
    min_weight: float = 0.30,
    max_weight: float = 0.70,
) -> tuple[float, float]:
    """Inverse-volatility weighting with bounded weights.

    Args:
        vol_a: Annualised volatility for asset A (must be > 0).
        vol_b: Annualised volatility for asset B (must be > 0).
        min_weight: Minimum weight for either asset.
        max_weight: Maximum weight for either asset.

    Returns:
        (weight_a, weight_b) where weight_a + weight_b == 1.0.
    """
    if vol_a <= 0:
        raise ValueError(f"vol_a must be positive, got {vol_a}")
    if vol_b <= 0:
        raise ValueError(f"vol_b must be positive, got {vol_b}")
    if not 0 < min_weight < max_weight < 1:
        raise ValueError(
            f"Invalid weight bounds: min={min_weight}, max={max_weight}"
        )

    inv_a = 1.0 / vol_a
    inv_b = 1.0 / vol_b
    total_inv = inv_a + inv_b

    raw_weight_a = inv_a / total_inv
    w_a = max(min_weight, min(max_weight, raw_weight_a))
    w_b = 1.0 - w_a

    return w_a, w_b
