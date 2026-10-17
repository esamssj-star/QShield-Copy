"""Shared memory for the app.

Streamlit re-runs the whole script on every click. st.session_state is a
dictionary that survives those re-runs, so the Sender, Receiver and Security
Status screens can share information through it.

Payment records and geolocation audit items are session-only demo state. No
persistent database is created by this module.
"""
import streamlit as st


def init_state() -> None:
    """Create the shared keys the first time the app starts."""
    defaults = {
        "encrypted_package": None,
        "recipient_keys": None,
        "recovered_file": None,
        "last_decrypt_ok": None,
        "last_check": None,
        "receiver_identity": None,
        "trust_store": None,
        # Payment page (MOCK provider). Never holds card numbers, only results.
        "payment_result": None,
        "payment_ledger": {},
        "payment_attempt_id": None,
        "payment_error": None,
        "payment_token": None,
        # Extra check for large payments (dynamic one-time credential). Holds no card data.
        "otc_user_id": None,
        "otc_issued": None,
        "otc_verified": None,
        "otc_error": None,
        # Handoff to the Wallet team (encrypted package only; never card details)
        "handoff_package": None,
        "handoff_code": None,
        "handoff_error": None,
        "wallet_opened": None,
        "wallet_error": None,
        # Geolocation demo controls. These defaults preserve the ordinary payment flow.
        "geo_account_id": "demo-acct-blr",
        "geo_location_mode": "Simulated demo scenario",
        "geo_scenario": "same_city",
        "geo_browser_payload": None,
        "geo_last_assessment": None,
        "geo_blocked_attempt_id": None,
        "geo_risk_audit": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
