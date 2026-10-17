"""Deterministic geolocation risk policy and pre-provider gate tests."""
import math

import pytest

from core.geo_risk import (
    EARTH_RADIUS_KM,
    DEMO_ACCOUNTS,
    GeoPoint,
    BranchLocation,
    LocationObservation,
    RiskPolicy,
    assess_locations,
    assess_transaction,
    demo_location,
    haversine_km,
)
from core.location_provider import browser_location_observation
from core.payment_flow import invoke_payment_provider


def test_haversine_known_cities_and_antimeridian():
    london = GeoPoint(51.5074, -0.1278)
    paris = GeoPoint(48.8566, 2.3522)
    assert haversine_km(london, paris) == pytest.approx(343.6, abs=0.2)
    assert haversine_km(GeoPoint(0, 179.5), GeoPoint(0, -179.5)) == pytest.approx(111.195, abs=0.01)
    assert haversine_km(GeoPoint(0, 0), GeoPoint(0, 180)) == pytest.approx(math.pi * EARTH_RADIUS_KM, abs=0.001)


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (91, 0),
        (-91, 0),
        (0, 181),
        (0, -181),
        (math.inf, 0),
        (0, math.nan),
        (True, 0),
        ("not-a-number", 0),
    ],
)
def test_invalid_coordinates_are_rejected(latitude, longitude):
    with pytest.raises(ValueError):
        GeoPoint(latitude, longitude)


def test_same_city_is_low_risk_and_allowed():
    result = assess_transaction("demo-acct-blr", demo_location("same_city"))
    assert result.risk_level == "LOW"
    assert result.decision == "ALLOW"
    assert result.distance_km == pytest.approx(0.0)


def test_mysuru_is_medium_risk_warning_under_demo_policy():
    result = assess_transaction("demo-acct-blr", demo_location("nearby_city"))
    assert result.distance_km is not None
    assert 100 <= result.distance_km < 500
    assert result.risk_level == "MEDIUM"
    assert result.decision == "ALLOW_WITH_WARNING"


@pytest.mark.parametrize("scenario", ["distant_domestic", "international"])
def test_far_location_is_high_risk_and_blocked(scenario):
    result = assess_transaction("demo-acct-blr", demo_location(scenario))
    assert result.risk_level == "HIGH"
    assert result.decision == "BLOCK"
    assert result.reason_code == "LOCATION_FAR_HARD_BLOCK"


def test_missing_location_fails_closed_as_unknown():
    result = assess_transaction("demo-acct-blr", demo_location("unknown_location"))
    assert result.risk_level == "UNKNOWN"
    assert result.decision == "BLOCK"
    assert result.reason_code == "TXN_LOCATION_MISSING"


def test_unmapped_and_unconfigured_branches_fail_closed():
    observation = demo_location("same_city")
    unmapped = assess_transaction("demo-acct-unmapped", observation)
    no_coordinates = assess_transaction("demo-acct-nocoords", observation)
    assert (unmapped.risk_level, unmapped.decision, unmapped.reason_code) == (
        "UNKNOWN", "BLOCK", "BRANCH_UNMAPPED"
    )
    assert (no_coordinates.risk_level, no_coordinates.decision, no_coordinates.reason_code) == (
        "UNKNOWN", "BLOCK", "BRANCH_COORDS_INVALID"
    )


def test_unknown_location_can_be_configured_for_review_but_never_allow():
    policy = RiskPolicy(unknown_decision="REVIEW")
    result = assess_transaction("demo-acct-blr", demo_location("unknown_location"), policy)
    assert result.risk_level == "UNKNOWN"
    assert result.decision == "REVIEW"


def _branch_at_origin():
    return BranchLocation(
        account_id="synthetic-boundary-test",
        label="Test branch",
        branch_id="test-branch",
        city="Origin",
        point=GeoPoint(0, 0),
    )


def _observation_at_distance(distance_km):
    longitude = math.degrees(distance_km / EARTH_RADIUS_KM)
    return LocationObservation(
        point=GeoPoint(0, longitude),
        city="Test location",
        source="test-fixture",
        confidence="HIGH",
        demo_mode=True,
    )


@pytest.mark.parametrize(
    "distance,risk,decision",
    [
        (99.999, "LOW", "ALLOW"),
        (100.0, "MEDIUM", "ALLOW_WITH_WARNING"),
        (499.999, "MEDIUM", "ALLOW_WITH_WARNING"),
        (500.0, "HIGH", "BLOCK"),
    ],
)
def test_inclusive_threshold_boundaries(distance, risk, decision):
    result = assess_locations(_branch_at_origin(), _observation_at_distance(distance))
    assert result.distance_km == pytest.approx(distance, abs=1e-6)
    assert result.risk_level == risk
    assert result.decision == decision


def test_untrusted_non_demo_location_source_fails_closed():
    branch = _branch_at_origin()
    observation = LocationObservation(
        point=GeoPoint(0, 0),
        city="Unverified city",
        source="untrusted-ip-lookup",
        confidence="LOW",
        demo_mode=False,
    )
    result = assess_locations(branch, observation)
    assert result.risk_level == "UNKNOWN"
    assert result.decision == "BLOCK"
    assert result.reason_code == "TXN_LOCATION_SOURCE_UNTRUSTED"


def test_browser_location_requires_valid_coordinates_and_accuracy():
    missing = browser_location_observation({"latitude": None, "longitude": None, "accuracy": None})
    invalid = browser_location_observation({"latitude": 100, "longitude": 0, "accuracy": 5})
    inaccurate = browser_location_observation({"latitude": 12.9, "longitude": 77.5, "accuracy": 9000})
    assert missing.reason_code == "TXN_LOCATION_INVALID"
    assert invalid.reason_code == "TXN_LOCATION_INVALID"
    result = assess_transaction("demo-acct-blr", inaccurate)
    assert result.risk_level == "UNKNOWN"
    assert result.decision == "BLOCK"
    assert result.reason_code == "TXN_LOCATION_ACCURACY_TOO_LOW"


@pytest.mark.parametrize("scenario", list(("same_city", "nearby_city", "distant_domestic", "international", "unknown_location")))
def test_demo_scenarios_are_deterministic(scenario):
    one = assess_transaction("demo-acct-blr", demo_location(scenario))
    two = assess_transaction("demo-acct-blr", demo_location(scenario))
    assert (one.risk_level, one.decision, one.distance_km, one.reason_code) == (
        two.risk_level, two.decision, two.distance_km, two.reason_code
    )
    assert one.demo_mode and two.demo_mode


def test_blocked_decision_never_invokes_payment_provider():
    result = assess_transaction("demo-acct-blr", demo_location("international"))
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return "simulated-success"

    value = invoke_payment_provider(result, provider, amount_paise=50000)
    assert value is None
    assert calls == []


def test_allowed_decision_invokes_provider_once():
    result = assess_transaction("demo-acct-blr", demo_location("same_city"))
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return "simulated-success"

    value = invoke_payment_provider(result, provider, amount_paise=50000)
    assert value == "simulated-success"
    assert calls == [{"amount_paise": 50000}]


def test_invalid_or_unknown_data_never_reaches_provider():
    result = assess_transaction("demo-acct-blr", None)
    calls = []
    value = invoke_payment_provider(result, lambda **kw: calls.append(kw), amount_paise=50000)
    assert result.risk_level == "UNKNOWN"
    assert value is None
    assert calls == []
