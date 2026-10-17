"""Payment UI integration tests for geolocation gating."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def set_demo_scenario(app, scenario):
    app.selectbox(key="geo_scenario").set_value(scenario).run()
    assert not app.exception


def fill_and_pay(app, card="4111111111111111"):
    app.text_input(key="pay_name").set_value("Demo User")
    app.text_input(key="pay_card").set_value(card)
    app.text_input(key="pay_expiry").set_value("12/30")
    app.text_input(key="pay_cvv").set_value("123")
    next(button for button in app.button if button.label == "Pay (test mode)").click().run()
    assert not app.exception


def test_same_city_payment_continues_through_existing_mock_flow():
    app = AppTest.from_file(APP).run()
    fill_and_pay(app)
    assert app.session_state["geo_last_assessment"].risk_level == "LOW"
    assert app.session_state["geo_last_assessment"].decision == "ALLOW"
    assert app.session_state["payment_result"].status == "succeeded"
    assert app.session_state["payment_result"].simulated is True
    assert app.session_state["payment_token"] is not None


def test_berlin_is_blocked_before_provider_success_token_or_handoff():
    app = AppTest.from_file(APP).run()
    set_demo_scenario(app, "international")
    original_attempt = app.session_state["payment_attempt_id"]

    fill_and_pay(app)

    assessment = app.session_state["geo_last_assessment"]
    assert assessment.risk_level == "HIGH"
    assert assessment.decision == "BLOCK"
    assert assessment.reason_code == "LOCATION_FAR_HARD_BLOCK"
    assert app.session_state["payment_result"] is None
    assert app.session_state["payment_token"] is None
    assert app.session_state["payment_ledger"] == {}
    assert app.session_state["handoff_package"] is None
    assert app.session_state["handoff_code"] is None
    assert app.session_state["geo_blocked_attempt_id"] == original_attempt
    assert app.session_state["payment_attempt_id"] != original_attempt
    assert any("Transaction Blocked for Your Protection" in item.value for item in app.error)
    assert app.session_state["geo_risk_audit"][-1]["decision"] == "BLOCK"


def test_unknown_location_is_blocked_and_never_fails_open():
    app = AppTest.from_file(APP).run()
    set_demo_scenario(app, "unknown_location")
    fill_and_pay(app)

    assessment = app.session_state["geo_last_assessment"]
    assert assessment.risk_level == "UNKNOWN"
    assert assessment.decision == "BLOCK"
    assert assessment.distance_km is None
    assert app.session_state["payment_result"] is None
    assert app.session_state["payment_ledger"] == {}


def test_unknown_branch_is_blocked_even_with_same_city_scenario():
    app = AppTest.from_file(APP).run()
    app.selectbox(key="geo_account_id").set_value("demo-acct-unmapped").run()
    assert not app.exception
    fill_and_pay(app)

    assessment = app.session_state["geo_last_assessment"]
    assert assessment.risk_level == "UNKNOWN"
    assert assessment.decision == "BLOCK"
    assert assessment.reason_code == "BRANCH_UNMAPPED"
    assert app.session_state["payment_result"] is None
    assert app.session_state["payment_ledger"] == {}
