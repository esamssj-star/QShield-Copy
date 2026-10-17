"""Local Streamlit component for one-shot browser geolocation requests.

This ships its HTML alongside the Python app and has no third-party requests.
"""
from pathlib import Path

import streamlit.components.v1 as components

_COMPONENT = components.declare_component(
    "qshield_browser_geolocation",
    path=str(Path(__file__).resolve().parent / "browser_geo"),
)


def get_browser_location():
    """Render a button that requests location only after an explicit user click."""
    return _COMPONENT(
        key="qshield_browser_location_widget",
        default={"status": "idle"},
    )
