"""Payment page: a fake test-card checkout backed by the MOCK provider."""
import secrets

import streamlit as st

from core import dynamic_credential as otc
from core.browser_geo_component import get_browser_location
from core.geo_risk import (
    DEMO_ACCOUNTS,
    DEMO_SCENARIOS,
    RiskAssessment,
    demo_location,
    format_distance_km,
)
from core.location_provider import browser_location_observation
from core.mock_provider import (
    MAX_AMOUNT_PAISE,
    MIN_AMOUNT_PAISE,
    TEST_CARDS,
    PaymentInputError,
    charge,
    validate_payment_input,
)
from core.payment_flow import invoke_payment_provider
from core.risk_gate import audit_record, evaluate_payment_location, payment_is_allowed
from core.token_store import TokenError, get_store
from core.wallet_handoff import HandoffError, build_handoff, load_config, payments_fingerprint
import relay


MERCHANT_ID = "qshield-demo-merchant"  # placeholder until there are real merchants
CURRENCY = "INR"
LOCATION_MODES = ("Simulated demo scenario", "Device GPS location")


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def _amount_paise() -> int:
    return int(round(st.session_state["pay_amount"] * 100))


def _credential_details() -> dict:
    """The details the one-time code is bound to. There are no user accounts yet, so
    the user is a random per-session ID; this becomes the real user/device ID later."""
    ss = st.session_state
    if ss["otc_user_id"] is None:
        ss["otc_user_id"] = secrets.token_urlsafe(12)
    return dict(user_id=ss["otc_user_id"], merchant=MERCHANT_ID, currency=CURRENCY,
                amount_paise=_amount_paise(), txn_id=ss["payment_attempt_id"])


def _is_verified(amount_paise: int) -> bool:
    """True if THIS payment attempt, at THIS amount, passed the one-time check."""
    ss = st.session_state
    return ss.get("otc_verified") == {"txn_id": ss["payment_attempt_id"],
                                      "amount_paise": amount_paise}


def _send_code() -> None:
    ss = st.session_state
    try:
        details = _credential_details()
        cred = otc.get_store().issue(**details)
    except otc.CredentialError as exc:
        ss["otc_error"] = str(exc)
        return
    ss["otc_issued"] = {"otp": cred.otp, "expires_at": cred.expires_at,
                        "amount_paise": details["amount_paise"]}
    ss["otc_verified"] = None
    ss["otc_error"] = None


def _check_code() -> None:
    ss = st.session_state
    code = ss.get("otc_code", "")
    ss["otc_code"] = ""
    try:
        details = _credential_details()
        otc.get_store().verify(**details, otp=code)  # consumes the credential on success
    except otc.CredentialError as exc:
        ss["otc_error"] = str(exc)
        return
    ss["otc_verified"] = {"txn_id": details["txn_id"], "amount_paise": details["amount_paise"]}
    ss["otc_issued"] = None
    ss["otc_error"] = None


def _clear_old_attempt_state() -> None:
    ss = st.session_state
    ss["payment_error"] = None
    ss["payment_token"] = None
    ss["payment_result"] = None
    ss["handoff_package"] = None
    ss["handoff_code"] = None
    ss["handoff_error"] = None
    ss["geo_last_assessment"] = None
    ss["geo_blocked_attempt_id"] = None


def _selected_location_observation():
    ss = st.session_state
    if ss.get("geo_location_mode") == "Device GPS location":
        return browser_location_observation(ss.get("geo_browser_payload"))
    return demo_location(
        ss.get("geo_scenario", "same_city"),
        ss.get("geo_account_id", "demo-acct-blr"),
    )


def _submit() -> None:
    """Validate and risk-screen the attempt before any mock provider approval."""
    ss = st.session_state
    name, number, expiry, cvv = ss["pay_name"], ss["pay_card"], ss["pay_expiry"], ss["pay_cvv"]
    amount_paise = _amount_paise()
    # Wipe widget state first. These local values are needed only for this callback.
    ss["pay_card"] = ss["pay_expiry"] = ss["pay_cvv"] = ""
    _clear_old_attempt_state()

    if not name.strip():
        ss["payment_error"] = "Enter the name on the card."
        return

    # Validate the full request without calling or recording a payment provider operation.
    try:
        validate_payment_input(
            card_number=number,
            expiry=expiry,
            cvv=cvv,
            amount_paise=amount_paise,
        )
    except PaymentInputError as exc:
        ss["payment_error"] = str(exc)
        return

    # The existing one-time credential is tied to this exact attempt and amount.
    if otc.requires_verification(amount_paise) and not _is_verified(amount_paise):
        ss["payment_error"] = "Payments above ₹10,000 need the verification code first."
        return

    attempt_ref = ss["payment_attempt_id"]
    observation = _selected_location_observation()
    assessment = evaluate_payment_location(ss.get("geo_account_id", "demo-acct-blr"), observation)
    ss["geo_last_assessment"] = assessment
    ss.setdefault("geo_risk_audit", []).append(audit_record(attempt_ref, assessment))

    # This guard is before charge(), token creation, wallet handoff, and all success UI.
    if not payment_is_allowed(assessment):
        ss["geo_blocked_attempt_id"] = attempt_ref
        ss["payment_error"] = None
        # BLOCKED is terminal for this attempt: rotate its reference and invalidate
        # any one-time code so a later action is necessarily a new attempt.
        ss["payment_attempt_id"] = secrets.token_urlsafe(16)
        ss["otc_verified"] = ss["otc_issued"] = ss["otc_error"] = None
        return

    try:
        result = invoke_payment_provider(
            assessment,
            charge,
            card_number=number,
            expiry=expiry,
            cvv=cvv,
            amount_paise=amount_paise,
            idempotency_key=attempt_ref,
            ledger=ss["payment_ledger"],
        )
        if result is None:
            # Defensive second guard: None cannot be rendered as payment success.
            ss["payment_error"] = "The location risk check did not authorize this payment."
            return
    except PaymentInputError as exc:
        ss["payment_error"] = str(exc)
        return

    ss["payment_result"] = result
    ss["payment_attempt_id"] = secrets.token_urlsafe(16)  # next submit = a NEW payment
    ss["otc_verified"] = ss["otc_issued"] = ss["otc_error"] = None
    if result.status == "succeeded":
        _issue_token()
        _send_to_wallet(number, expiry, name)


def _send_to_wallet(number: str, expiry: str, name: str) -> None:
    """Lock + sign the payment details for the Wallet team. This is the only moment the
    card details exist on the server; after this function returns they are gone.
    The CVV is never passed in and never sent."""
    ss = st.session_state
    ss["handoff_package"] = ss["handoff_code"] = ss["handoff_error"] = None
    token = ss["payment_token"]
    if token is None:
        return
    config, missing = load_config()
    if config is None:
        ss["handoff_error"] = (
            "Wallet handoff is not set up (missing: " + ", ".join(missing) + "). "
            "The card details were discarded and NOTHING was sent to Wallet."
        )
        return
    try:
        package = build_handoff(config, token=token, payment=ss["payment_result"],
                                card_number=number, expiry=expiry, cardholder=name)
        code = relay.put("package", package, "handoff.qshield") if relay.is_configured() else None
        ss["handoff_package"], ss["handoff_code"] = package, code
        ss["payment_token"] = get_store().advance(token.token, "sent")
    except (HandoffError, relay.RelayError, TokenError) as exc:
        ss["handoff_error"] = f"The handoff to Wallet failed: {exc}"


def _issue_token() -> None:
    """Make (or re-fetch) the token for the last successful payment. Safe to repeat:
    the same payment always gives the same token."""
    ss = st.session_state
    try:
        ss["payment_token"] = get_store().issue(ss["payment_result"])
        ss["payment_error"] = None
    except TokenError as exc:
        ss["payment_error"] = f"Payment succeeded, but the token was not saved: {exc}"


def _reset_location_demo() -> None:
    """Clear only geolocation controls/records, not payment or unrelated QShield state."""
    ss = st.session_state
    ss["geo_account_id"] = "demo-acct-blr"
    ss["geo_location_mode"] = "Simulated demo scenario"
    ss["geo_scenario"] = "same_city"
    ss["geo_browser_payload"] = None
    ss["geo_last_assessment"] = None
    ss["geo_blocked_attempt_id"] = None
    ss["geo_risk_audit"] = []
    ss["geo_previous_mode"] = "Simulated demo scenario"
    ss["geo_component_generation"] = ss.get("geo_component_generation", 0) + 1
    ss["geo_previous_selection"] = None


def _show_location_controls() -> None:
    ss = st.session_state
    with st.expander("Location risk demo controls"):
        st.caption(
            "Test facility only. Demo locations are simulated and do not change your "
            "device GPS. Only synthetic accounts are available."
        )
        st.selectbox(
            "Synthetic account / registered branch",
            options=list(DEMO_ACCOUNTS.keys()),
            format_func=lambda key: DEMO_ACCOUNTS[key].label,
            key="geo_account_id",
        )
        st.radio(
            "Transaction location source",
            options=list(LOCATION_MODES),
            key="geo_location_mode",
        )

        mode = ss["geo_location_mode"]
        previous_mode = ss.get("geo_previous_mode")
        if previous_mode is None:
            ss["geo_previous_mode"] = mode
        elif previous_mode != mode:
            ss["geo_browser_payload"] = None
            ss["geo_component_generation"] = ss.get("geo_component_generation", 0) + 1
            ss["geo_previous_mode"] = mode

        if mode == "Simulated demo scenario":
            st.selectbox(
                "Simulated transaction location",
                options=list(DEMO_SCENARIOS.keys()),
                format_func=lambda key: DEMO_SCENARIOS[key],
                key="geo_scenario",
            )
        else:
            st.caption(
                "The browser asks permission only after you press the button below. "
                "No continuous tracking or third-party geocoding is used."
            )
            payload = get_browser_location(
                key=f"qshield_browser_location_{ss.get('geo_component_generation', 0)}"
            )
            if isinstance(payload, dict) and payload.get("status") in {"success", "error"}:
                ss["geo_browser_payload"] = payload
            if not ss.get("geo_browser_payload"):
                st.info("No device reading yet. Request a location, or choose a demo scenario.")
            elif ss["geo_browser_payload"].get("status") == "error":
                code = ss["geo_browser_payload"].get("code", "")
                safe_errors = {
                    "PERMISSION_DENIED": "Location permission was denied.",
                    "POSITION_UNAVAILABLE": "The browser could not obtain a location.",
                    "TIMEOUT": "The location request timed out.",
                    "UNSUPPORTED": "This browser does not support device location.",
                }
                st.info(safe_errors.get(code, "The browser location is unavailable."))
            else:
                st.success("A device location reading is available for the next assessment.")

        st.caption(
            "Demo policy: under 100 km = LOW / allow; 100–499.999 km = MEDIUM / "
            "warning; 500 km or more = HIGH / block. Unknown location data is blocked."
        )
        st.button(
            "Reset location demo",
            key="geo_reset_demo",
            on_click=_reset_location_demo,
        )

        selection = (
            ss.get("geo_account_id"),
            ss.get("geo_location_mode"),
            ss.get("geo_scenario") if mode == "Simulated demo scenario" else None,
        )
        previous_selection = ss.get("geo_previous_selection")
        if previous_selection is not None and previous_selection != selection:
            ss["geo_last_assessment"] = None
        ss["geo_previous_selection"] = selection


def _show_risk_assessment(assessment: RiskAssessment | None) -> None:
    if assessment is None or assessment.risk_level == "LOW":
        return
    if assessment.decision == "BLOCK":
        st.error("### Transaction Blocked for Your Protection")
        st.write(assessment.user_safe_explanation)
        st.markdown(f"**Registered branch:** {assessment.branch_location or 'Not configured'}")
        st.markdown(
            f"**Detected transaction location:** {assessment.transaction_location or 'Unavailable'}"
        )
        st.markdown(f"**Approximate distance:** {format_distance_km(assessment.distance_km)}")
        st.markdown(f"**Risk category:** {assessment.risk_level}")
        st.markdown("**Transaction status:** BLOCKED")
        st.caption(
            "This attempt did not reach payment approval. Verify your location/account details "
            "or contact your bank. This QShield payment provider is a simulation."
        )
    elif assessment.risk_level == "MEDIUM":
        st.warning(
            "Location warning: "
            f"{format_distance_km(assessment.distance_km)} from the registered branch. "
            "The demo policy allows this payment to continue with a warning."
        )
    else:
        st.warning(
            "Transaction not allowed: the branch or transaction location could not be "
            "verified. The demo's fail-closed policy blocks unknown locations."
        )


def _show_risk_audit() -> None:
    records = st.session_state.get("geo_risk_audit", [])
    if not records:
        return
    with st.expander("Location risk audit · current session only"):
        st.caption(
            "Session-memory demonstration record only; it is not a persistent or bank audit log. "
            "Exact coordinates and full account identifiers are not stored here."
        )
        display = []
        for record in records:
            display.append({
                "Transaction ref": str(record["transaction_ref"])[-8:],
                "Risk": record["risk_level"],
                "Decision": record["decision"],
                "Distance": format_distance_km(record["distance_km"]),
                "Reason": record["reason_code"],
                "Simulated": record["simulated"],
            })
        st.table(display)


def show_payment() -> None:
    st.subheader("Pay (test mode)")
    st.warning(
        "**Simulation only.** This uses a mock payment provider: no bank is contacted "
        "and no money moves. Use the test cards below. Never enter a real card."
    )
    with st.expander("Test cards"):
        st.table(
            [{"Card number": n, "Result": msg} for n, (_, msg) in TEST_CARDS.items()]
            + [{"Card number": "Expiry / CVV", "Result": "Any future date (e.g. 12/30) / any 3 digits"}]
        )

    if st.session_state["payment_attempt_id"] is None:
        st.session_state["payment_attempt_id"] = secrets.token_urlsafe(16)

    _show_location_controls()

    st.number_input(
        "Amount (₹)", min_value=MIN_AMOUNT_PAISE / 100, max_value=MAX_AMOUNT_PAISE / 100,
        value=499.0, step=1.0, key="pay_amount",
    )  # outside the form so the page reacts the moment the amount crosses ₹10,000
    amount_paise = _amount_paise()
    needs_code = otc.requires_verification(amount_paise)
    if needs_code:
        _show_verification(amount_paise)

    with st.form("payment_form"):
        st.text_input("Name on card", key="pay_name")
        st.text_input("Test card number", key="pay_card", autocomplete="off")
        col1, col2 = st.columns(2)
        with col1:
            st.text_input("Expiry (MM/YY)", key="pay_expiry", autocomplete="off")
        with col2:
            st.text_input("CVV", type="password", max_chars=3, key="pay_cvv", autocomplete="off")
        st.form_submit_button("Pay (test mode)", type="primary", on_click=_submit,
                              disabled=needs_code and not _is_verified(amount_paise))

    if st.session_state.get("payment_error"):
        st.error(st.session_state["payment_error"])

    _show_risk_assessment(st.session_state.get("geo_last_assessment"))

    result = st.session_state["payment_result"]
    if result is not None:
        if result.status == "succeeded":
            st.success(f"{result.message} {_rupees(result.amount_paise)} on {result.brand} •••• {result.last4}")
        else:
            st.error(f"{result.message} {result.brand} •••• {result.last4}")
        st.caption(f"Provider reference: {result.provider_ref} · {result.created_at} · SIMULATED")
        st.caption("Only the brand and last 4 digits are kept. The full card number is discarded.")

        if result.status == "succeeded":
            token = st.session_state["payment_token"]
            if token is None:
                st.button("Create token", on_click=_issue_token, key="pay_create_token")
            else:
                st.markdown("**Payment token**")
                st.code(token.token, language=None)
                st.caption(
                    f"Status: **{token.status}** · a random reference, not the card number · "
                    f"Stored in: {get_store().name}"
                )
                _show_handoff()

    _show_risk_audit()


def _show_verification(amount_paise: int) -> None:
    """Step-up check for payments above Rs 10,000: a one-time code tied to this payment."""
    ss = st.session_state
    st.info(f"🔐 Payments above ₹{otc.OTP_THRESHOLD_PAISE // 100:,} need a one-time verification code.")
    if _is_verified(amount_paise):
        st.success("Verified for this payment. You can pay now.")
        return
    issued = ss.get("otc_issued")
    if issued is None or issued["amount_paise"] != amount_paise:
        if issued is not None:
            st.caption("The amount changed, so the earlier code no longer applies. Send a new one.")
        st.button("Send verification code", on_click=_send_code, key="otc_send")
    else:
        st.code(issued["otp"], language=None)
        st.caption(
            f"SIMULATED delivery: shown here for the demo. A real app sends this to your trusted "
            f"device. Valid for {otc.TTL_SECONDS // 60} minutes, once only, and only for "
            f"{_rupees(amount_paise)}."
        )
        st.text_input("Enter the code", key="otc_code", max_chars=otc.OTP_DIGITS, autocomplete="off")
        c1, c2 = st.columns(2)
        with c1:
            st.button("Verify code", on_click=_check_code, key="otc_verify", type="primary")
        with c2:
            st.button("Send a new code", on_click=_send_code, key="otc_resend")
    if ss.get("otc_error"):
        st.error(ss["otc_error"])


def _show_handoff() -> None:
    ss = st.session_state
    if ss.get("handoff_error"):
        st.warning(ss["handoff_error"])
    package = ss.get("handoff_package")
    if package:
        st.success("🔐 Card details signed by Payments and encrypted with QShield for the Wallet team.")
        if ss.get("handoff_code"):
            st.markdown("**Handoff code for Wallet**")
            st.code(ss["handoff_code"], language=None)
        else:
            st.caption("Ready for Wallet in this browser session (open the Wallet tab). "
                       "To reach Wallet on another device, connect Supabase.")
        st.caption("The CVV is never sent or stored. The Wallet team opens this in the Wallet tab "
                   "with their private key.")
    config, _ = load_config()
    fp = payments_fingerprint(config) if config else None
    if fp:
        with st.expander("Payments fingerprint (read this to Wallet over a phone call)"):
            st.code(fp, language=None)
