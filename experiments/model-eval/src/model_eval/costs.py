"""Conservative cost accounting.

Replicate does not report a per-prediction dollar cost in the prediction
response, so nothing here is a provider-reported figure. Three bases exist:

- ``provider_reported``      reserved for a future API that returns real
                             charges (unused today).
- ``calculated``             an input-aware calculation from a pricing
                             formula that has been explicitly verified
                             (``pricing.formula_verified: true``): flat
                             per-image, or per-run fee + per-megapixel rates
                             applied to actual output and reference/edit
                             input megapixels.
- ``reserved_conservative``  the FULL reserved amount. Used whenever the
                             billing formula is unresolved — the ledger must
                             never undercount spend based on an optimistic
                             static estimate.

The final accounted figure is always capped at the reservation (the amount
that passed the budget check).
"""

from __future__ import annotations

from typing import Literal

from .config import Pricing

CostBasis = Literal["provider_reported", "calculated", "reserved_conservative"]


def calculated_generation_cost(
    pricing: Pricing,
    *,
    output_megapixels: float,
    input_megapixels: float,
) -> float | None:
    """Input-aware cost calculation, or None when the formula is unresolved."""
    if not pricing.formula_verified:
        return None
    if pricing.unit == "per_image":
        return pricing.usd_per_unit
    if pricing.unit == "per_megapixel":
        out_rate = pricing.usd_per_output_megapixel
        if out_rate is None:
            return None
        in_rate = pricing.usd_per_input_megapixel or 0.0
        return (
            pricing.usd_per_run
            + out_rate * output_megapixels
            + in_rate * input_megapixels
        )
    return None


def final_cost(
    pricing: Pricing,
    *,
    reserved_usd: float,
    output_megapixels: float,
    input_megapixels: float,
) -> tuple[float, CostBasis]:
    """The amount to reconcile against the ledger, and how it was derived.

    Unresolved pricing -> the full reservation (never an undercount).
    Calculated costs are capped at the reservation, which already passed the
    budget check; a calculation above the reservation indicates a wrong
    max_cost_per_generation_usd and is surfaced by the ledger's overrun
    handling rather than silently trusted here.
    """
    calculated = calculated_generation_cost(
        pricing,
        output_megapixels=output_megapixels,
        input_megapixels=input_megapixels,
    )
    if calculated is None:
        return reserved_usd, "reserved_conservative"
    return min(calculated, reserved_usd), "calculated"
