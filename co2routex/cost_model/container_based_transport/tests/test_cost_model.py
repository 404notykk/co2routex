import pytest

from container_transport_cost_model.cost_model import calculate_gamma2, unit_cost
from container_transport_cost_model.models import ModeParameters


TRUCK = ModeParameters("truck", 5.58, 0.15)
RAILWAY = ModeParameters("railway", 28.9, 0.07)


def test_truck_coefficients_at_100_km():
    assert unit_cost(100, TRUCK) == pytest.approx(0.2058)
    assert calculate_gamma2(100, TRUCK) == pytest.approx(20.58)


def test_railway_coefficients_at_100_km():
    assert unit_cost(100, RAILWAY) == pytest.approx(0.359)
    assert calculate_gamma2(100, RAILWAY) == pytest.approx(35.9)


def test_modal_break_even_is_about_291_5_km():
    distance = (28.9 - 5.58) / (0.15 - 0.07)
    assert distance == pytest.approx(291.5)
    assert calculate_gamma2(distance, TRUCK) == pytest.approx(
        calculate_gamma2(distance, RAILWAY)
    )


@pytest.mark.parametrize("distance", [0, -1, float("nan"), float("inf")])
def test_nonpositive_or_nonfinite_distance_is_rejected(distance):
    with pytest.raises(ValueError):
        calculate_gamma2(distance, TRUCK)

