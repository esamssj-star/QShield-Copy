"""Payment page: a fake test-card checkout backed by the MOCK provider."""

import secrets

import streamlit as st

from core import dynamic_credential as otc
from core.mock_provider import MAX_AMOUNT_PAISE, MIN_AMOUNT_PAISE, TEST_CARDS, PaymentInputError, charge
from core.token_store import TokenError, get_store
from core.wallet_handoff import HandoffError, build_handoff, load_config, payments_fingerprint
import relay


MERCHANT_ID = "qshield-demo-merchant"  # placeholder until there are real merchants
CURRENCY = "INR"


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


def _submit() -> None:
    """Runs when the form is submitted. Uses the card details once, then wipes
    them from session state so the full number never lingers in server memory."""
    ss = st.session_state
    name, number, expiry, cvv = ss["pay_name"], ss["pay_card"], ss["pay_expiry"], ss["pay_cvv"]
    amount_paise = _amount_paise()
    ss["pay_card"] = ss["pay_expiry"] = ss["pay_cvv"] = ""  # wipe first, use local copies below
    ss["payment_error"] = None
    ss["payment_token"] = None
    ss["payment_result"] = None  # a failed attempt must not leave an old success on screen
    if not name.strip():
        ss["payment_error"] = "Enter the name on the card."
        return
    # Server-side gate: even if the button were somehow pressed, a large payment never
    # reaches the provider without a credential that was verified for this exact attempt + amount.
    if otc.requires_verification(amount_paise) and not _is_verified(amount_paise):
        ss["payment_error"] = "Payments above ₹10,000 need the verification code first."
        return
    try:
        result = charge(
            card_number=number, expiry=expiry, cvv=cvv, amount_paise=amount_paise,
            idempotency_key=ss["payment_attempt_id"], ledger=ss["payment_ledger"],
        )
    except PaymentInputError as exc:
        ss["payment_error"] = str(exc)
        return
    ss["payment_result"] = result
    ss["payment_attempt_id"] = secrets.token_urlsafe(16)  # next submit = a NEW payment
    ss["otc_verified"] = ss["otc_issued"] = ss["otc_error"] = None  # the old credential does not carry over
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

    result = st.session_state["payment_result"]
    if result is not None:
        if result.status == "succeeded":
            st.success(f"{result.message} {_rupees(result.amount_paise)} on {result.brand} •••• {result.last4}")
        else:
            st.error(f"{result.message} {result.brand} •••• {result.last4}")
        st.caption(f"Provider reference: `{result.provider_ref}` · {result.created_at} · SIMULATED")
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


def _show_verification(amount_paise: int) -> None:
    """Step-up check for payments above ₹10,000: a one-time code tied to this payment."""
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
