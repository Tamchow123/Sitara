"""Cost estimator: input-aware calculation vs conservative fallback."""

import pytest

from conftest import make_pricing
from model_eval.costs import calculated_generation_cost, final_cost


class TestCalculatedCost:
    def test_flat_per_image(self):
        pricing = make_pricing(expected=0.04, maximum=0.08, formula_verified=True)
        assert calculated_generation_cost(
            pricing, output_megapixels=1.0, input_megapixels=0.0
        ) == pytest.approx(0.04)

    def test_per_megapixel_is_input_aware(self):
        pricing = make_pricing(
            expected=0.03,
            maximum=0.12,
            formula_verified=True,
            unit="per_megapixel",
            usd_per_run=0.015,
            usd_per_input_megapixel=0.015,
            usd_per_output_megapixel=0.015,
        )
        # run fee + 1 output MP + 2 reference-input MP
        assert calculated_generation_cost(
            pricing, output_megapixels=1.0, input_megapixels=2.0
        ) == pytest.approx(0.015 + 0.015 + 0.030)

    def test_unresolved_formula_yields_no_calculation(self):
        pricing = make_pricing(formula_verified=False)
        assert (
            calculated_generation_cost(pricing, output_megapixels=1.0, input_megapixels=0.0)
            is None
        )


class TestFinalCost:
    def test_unresolved_pricing_cannot_undercount(self):
        """The core guarantee: unresolved pricing reconciles the FULL
        reservation, regardless of how cheap the advertised price looks."""
        pricing = make_pricing(expected=0.001, maximum=0.05, formula_verified=False)
        amount, basis = final_cost(
            pricing, reserved_usd=0.05, output_megapixels=1.0, input_megapixels=0.0
        )
        assert amount == pytest.approx(0.05)
        assert basis == "reserved_conservative"

    def test_calculated_cost_is_capped_at_the_reservation(self):
        pricing = make_pricing(
            expected=0.03,
            maximum=0.12,
            formula_verified=True,
            unit="per_megapixel",
            usd_per_run=0.0,
            usd_per_input_megapixel=0.05,
            usd_per_output_megapixel=0.05,
        )
        amount, basis = final_cost(
            pricing, reserved_usd=0.12, output_megapixels=4.0, input_megapixels=4.0
        )
        assert amount == pytest.approx(0.12)  # 0.4 calculated, capped at reservation
        assert basis == "calculated"
