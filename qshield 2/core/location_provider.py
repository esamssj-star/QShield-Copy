"""Normalize browser Geolocation API readings into validated observations."""
from __future__ import annotations

import math
from typing import Any

from core.geo_risk import GeoPoint, LocationObservation


def browser_location_observation(payload: Any) -> LocationObservation:
    """Convert a browser geolocation component value without trusting its input.

    No reverse geocoder or third-party location service is called. The exact
    coordinates remain in the current Streamlit session and are not written to
    the risk audit record.
    """
    if not isinstance(payload, dict):
        return LocationObservation(
            point=None,
            city=None,
            source="browser-geolocation",
            confidence="UNKNOWN",
            demo_mode=False,
            reason_code="TXN_LOCATION_MISSING",
        )

    try:
        point = GeoPoint(payload.get("latitude"), payload.get("longitude"))
    except (TypeError, ValueError, OverflowError):
        return LocationObservation(
            point=None,
            city=None,
            source="browser-geolocation",
            confidence="UNKNOWN",
            demo_mode=False,
            reason_code="TXN_LOCATION_INVALID",
        )

    accuracy = payload.get("accuracy")
    try:
        accuracy_m = float(accuracy)
    except (TypeError, ValueError, OverflowError):
        accuracy_m = math.nan

    if not math.isfinite(accuracy_m) or accuracy_m < 0:
        return LocationObservation(
            point=point,
            city=None,
            source="browser-geolocation",
            confidence="UNKNOWN",
            accuracy_m=None,
            demo_mode=False,
            reason_code="TXN_LOCATION_ACCURACY_UNKNOWN",
        )

    confidence = "HIGH" if accuracy_m <= 1000.0 else "MEDIUM"
    return LocationObservation(
        point=point,
        city=None,
        source="browser-geolocation",
        confidence=confidence,
        accuracy_m=accuracy_m,
        demo_mode=False,
    )
