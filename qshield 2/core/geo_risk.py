"""Configurable, deterministic geolocation risk screening for the QShield demo.

This module is deliberately independent of Streamlit and payment-provider code.
The branch fixture is synthetic; it is not a bank directory and is not evidence
of a person's residence or current location.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

EARTH_RADIUS_KM = 6371.0088
WARNING_THRESHOLD_KM = 100.0
HARD_BLOCK_THRESHOLD_KM = 500.0
MAX_LOCATION_ACCURACY_M = 5000.0
UNKNOWN_LOCATION_DECISION = "BLOCK"


@dataclass(frozen=True)
class GeoPoint:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if isinstance(self.latitude, bool) or isinstance(self.longitude, bool):
            raise ValueError("Latitude and longitude must be numeric coordinates.")
        try:
            latitude = float(self.latitude)
            longitude = float(self.longitude)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Latitude and longitude must be numeric coordinates.") from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise ValueError("Latitude and longitude must be finite.")
        if not -90.0 <= latitude <= 90.0:
            raise ValueError("Latitude must be between -90 and 90 degrees.")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("Longitude must be between -180 and 180 degrees.")
        object.__setattr__(self, "latitude", latitude)
        object.__setattr__(self, "longitude", longitude)


@dataclass(frozen=True)
class BranchLocation:
    account_id: str
    label: str
    branch_id: str | None
    city: str | None
    point: GeoPoint | None


@dataclass(frozen=True)
class LocationObservation:
    point: GeoPoint | None
    city: str | None
    source: str
    confidence: str
    accuracy_m: float | None = None
    demo_mode: bool = False
    reason_code: str | None = None


@dataclass(frozen=True)
class RiskPolicy:
    warning_threshold_km: float = WARNING_THRESHOLD_KM
    hard_block_threshold_km: float = HARD_BLOCK_THRESHOLD_KM
    max_location_accuracy_m: float = MAX_LOCATION_ACCURACY_M
    unknown_decision: str = UNKNOWN_LOCATION_DECISION

    def __post_init__(self) -> None:
        values = (
            self.warning_threshold_km,
            self.hard_block_threshold_km,
            self.max_location_accuracy_m,
        )
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("Risk thresholds must be finite.")
        if self.warning_threshold_km < 0:
            raise ValueError("Warning threshold cannot be negative.")
        if self.hard_block_threshold_km <= self.warning_threshold_km:
            raise ValueError("Hard-block threshold must exceed warning threshold.")
        if self.max_location_accuracy_m <= 0:
            raise ValueError("Location accuracy threshold must be positive.")
        if self.unknown_decision not in {"BLOCK", "REVIEW"}:
            raise ValueError("Unknown-location policy must be BLOCK or REVIEW.")


@dataclass(frozen=True)
class RiskAssessment:
    risk_level: str
    decision: str
    distance_km: float | None
    branch_location: str | None
    transaction_location: str | None
    location_source: str
    location_confidence: str
    reason_code: str
    user_safe_explanation: str
    assessed_at: str
    demo_mode: bool


@dataclass(frozen=True)
class DemoAccount:
    account_id: str
    label: str
    branch_id: str | None
    city: str | None
    point: GeoPoint | None


# Synthetic, non-real account identifiers. Replace this mapping only with a
# trusted institution-provided account-to-branch mapping in a future integration.
DEMO_ACCOUNTS: dict[str, DemoAccount] = {
    "demo-acct-blr": DemoAccount(
        "demo-acct-blr", "Demo account · Bengaluru branch", "branch-blr-demo",
        "Bengaluru", GeoPoint(12.9716, 77.5946),
    ),
    "demo-acct-mum": DemoAccount(
        "demo-acct-mum", "Demo account · Mumbai branch", "branch-mum-demo",
        "Mumbai", GeoPoint(19.0760, 72.8777),
    ),
    "demo-acct-del": DemoAccount(
        "demo-acct-del", "Demo account · Delhi branch", "branch-del-demo",
        "Delhi", GeoPoint(28.6139, 77.2090),
    ),
    "demo-acct-unmapped": DemoAccount(
        "demo-acct-unmapped", "Demo account · branch mapping missing", None,
        None, None,
    ),
    "demo-acct-nocoords": DemoAccount(
        "demo-acct-nocoords", "Demo account · branch coordinates missing",
        "branch-unconfigured-demo", "Unknown branch", None,
    ),
}

DEMO_SCENARIOS: dict[str, str] = {
    "same_city": "Same city as selected branch",
    "nearby_city": "Mysuru, Karnataka · nearby city",
    "distant_domestic": "Delhi, India · distant domestic",
    "international": "Berlin, Germany · international",
    "unknown_location": "Unknown location · no coordinates",
}

_DEMO_POINTS: dict[str, tuple[str, GeoPoint]] = {
    "nearby_city": ("Mysuru", GeoPoint(12.2958, 76.6394)),
    "distant_domestic": ("Delhi", GeoPoint(28.6139, 77.2090)),
    "international": ("Berlin", GeoPoint(52.5200, 13.4050)),
}


DEFAULT_POLICY = RiskPolicy()


def resolve_branch(account_id: str) -> BranchLocation | None:
    """Resolve only known synthetic account references; never infer from an ID."""
    account = DEMO_ACCOUNTS.get(account_id)
    if account is None:
        return None
    return BranchLocation(
        account_id=account.account_id,
        label=account.label,
        branch_id=account.branch_id,
        city=account.city,
        point=account.point,
    )


def demo_location(scenario: str, account_id: str = "demo-acct-blr") -> LocationObservation:
    """Build a deterministic simulated location; it never changes device GPS."""
    if scenario == "unknown_location":
        return LocationObservation(
            point=None,
            city=None,
            source="demo-simulator",
            confidence="UNKNOWN",
            demo_mode=True,
            reason_code="TXN_LOCATION_MISSING",
        )
    if scenario == "same_city":
        branch = resolve_branch(account_id)
        if branch is None or branch.point is None:
            return LocationObservation(
                point=None,
                city=None,
                source="demo-simulator",
                confidence="UNKNOWN",
                demo_mode=True,
                reason_code="TXN_LOCATION_UNAVAILABLE",
            )
        return LocationObservation(
            point=branch.point,
            city=branch.city,
            source="demo-simulator",
            confidence="SIMULATED",
            accuracy_m=0.0,
            demo_mode=True,
        )
    if scenario not in _DEMO_POINTS:
        return LocationObservation(
            point=None,
            city=None,
            source="demo-simulator",
            confidence="UNKNOWN",
            demo_mode=True,
            reason_code="TXN_LOCATION_SCENARIO_INVALID",
        )
    city, point = _DEMO_POINTS[scenario]
    return LocationObservation(
        point=point,
        city=city,
        source="demo-simulator",
        confidence="SIMULATED",
        accuracy_m=0.0,
        demo_mode=True,
    )


def haversine_km(origin: GeoPoint, destination: GeoPoint) -> float:
    """Return great-circle distance between validated coordinates in kilometres."""
    lat1 = math.radians(origin.latitude)
    lat2 = math.radians(destination.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(destination.longitude - origin.longitude)
    a = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    # Floating-point noise can move a a tiny amount outside the valid interval.
    a = min(1.0, max(0.0, a))
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _unknown_assessment(
    *,
    policy: RiskPolicy,
    branch_city: str | None,
    observation: LocationObservation | None,
    reason_code: str,
    explanation: str,
) -> RiskAssessment:
    return RiskAssessment(
        risk_level="UNKNOWN",
        decision=policy.unknown_decision,
        distance_km=None,
        branch_location=branch_city,
        transaction_location=(observation.city if observation else None)
        or ("Device GPS location" if observation and observation.point else None),
        location_source=observation.source if observation else "unavailable",
        location_confidence=observation.confidence if observation else "UNKNOWN",
        reason_code=reason_code,
        user_safe_explanation=explanation,
        assessed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        demo_mode=bool(observation.demo_mode) if observation else False,
    )


def assess_locations(
    branch: BranchLocation | None,
    observation: LocationObservation | None,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> RiskAssessment:
    """Assess location risk independently of the UI or payment provider."""
    if branch is None or not branch.branch_id:
        return _unknown_assessment(
            policy=policy,
            branch_city=branch.city if branch else None,
            observation=observation,
            reason_code="BRANCH_UNMAPPED",
            explanation=(
                "The account branch could not be verified. This transaction was "
                "not allowed under the demo's unknown-location policy."
            ),
        )
    if branch.point is None:
        return _unknown_assessment(
            policy=policy,
            branch_city=branch.city,
            observation=observation,
            reason_code="BRANCH_COORDS_INVALID",
            explanation=(
                "The registered branch location is not configured well enough to "
                "assess this transaction. It was not allowed under the demo policy."
            ),
        )
    if observation is None:
        return _unknown_assessment(
            policy=policy,
            branch_city=branch.city,
            observation=None,
            reason_code="TXN_LOCATION_MISSING",
            explanation=(
                "The transaction location could not be established, so this attempt "
                "was blocked under the demo's fail-closed policy."
            ),
        )
    if observation.reason_code:
        return _unknown_assessment(
            policy=policy,
            branch_city=branch.city,
            observation=observation,
            reason_code=observation.reason_code,
            explanation=(
                "The transaction location could not be verified reliably. This attempt "
                "was not allowed under the demo's unknown-location policy."
            ),
        )
    if observation.point is None:
        return _unknown_assessment(
            policy=policy,
            branch_city=branch.city,
            observation=observation,
            reason_code="TXN_LOCATION_MISSING",
            explanation=(
                "The transaction location could not be established, so this attempt "
                "was blocked under the demo's fail-closed policy."
            ),
        )
    if observation.source == "browser-geolocation":
        accuracy = observation.accuracy_m
        if (
            accuracy is None
            or isinstance(accuracy, bool)
            or not math.isfinite(float(accuracy))
            or float(accuracy) < 0
        ):
            return _unknown_assessment(
                policy=policy,
                branch_city=branch.city,
                observation=observation,
                reason_code="TXN_LOCATION_ACCURACY_UNKNOWN",
                explanation=(
                    "The device did not provide a usable location-accuracy reading. "
                    "This attempt was blocked under the demo policy."
                ),
            )
        if float(accuracy) > policy.max_location_accuracy_m:
            return _unknown_assessment(
                policy=policy,
                branch_city=branch.city,
                observation=observation,
                reason_code="TXN_LOCATION_ACCURACY_TOO_LOW",
                explanation=(
                    "The available device location is too imprecise to assess safely. "
                    "This attempt was blocked under the demo policy."
                ),
            )

    distance = haversine_km(branch.point, observation.point)
    transaction_location = observation.city or (
        "Device GPS location" if observation.source == "browser-geolocation" else "Unknown location"
    )
    if distance < policy.warning_threshold_km:
        risk_level, decision, reason = "LOW", "ALLOW", "LOCATION_WITHIN_RANGE"
        explanation = "The location check passed the configured demo policy."
    elif distance < policy.hard_block_threshold_km:
        risk_level, decision, reason = "MEDIUM", "ALLOW_WITH_WARNING", "LOCATION_DISTANT_WARNING"
        explanation = (
            "The transaction location differs from the registered branch location. "
            "It may continue under the demo policy, with a warning."
        )
    else:
        risk_level, decision, reason = "HIGH", "BLOCK", "LOCATION_FAR_HARD_BLOCK"
        explanation = (
            "This transaction was blocked because its detected location differs "
            "significantly from the registered account branch location."
        )
    return RiskAssessment(
        risk_level=risk_level,
        decision=decision,
        distance_km=distance,
        branch_location=branch.city,
        transaction_location=transaction_location,
        location_source=observation.source,
        location_confidence=observation.confidence,
        reason_code=reason,
        user_safe_explanation=explanation,
        assessed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        demo_mode=observation.demo_mode,
    )


def assess_transaction(
    account_id: str,
    observation: LocationObservation | None,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> RiskAssessment:
    """Resolve the account's configured branch, then apply the location policy."""
    return assess_locations(resolve_branch(account_id), observation, policy)


def format_distance_km(distance_km: float | None) -> str:
    """Round for display only; the engine uses the full-precision distance."""
    if distance_km is None:
        return "Unavailable"
    if not math.isfinite(distance_km) or distance_km < 0:
        return "Unavailable"
    if distance_km < 1000:
        return f"{distance_km:.1f} km"
    return f"{distance_km:,.0f} km"
