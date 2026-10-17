"""Pay tab: payments above Rs 10,000 need the one-time code, through the real Streamlit app."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app.py")
BIG = 12_000.0


def click(app, label):
    next(b for b in app.button if b.label == label).click().run()


def pay_button(app):
    return next(b for b in app.button if b.label == "Pay (test mode)")


def set_amount(app, rupees):
    app.number_input(key="pay_amount").set_value(rupees).run()


def fill_and_pay(app, card="4111111111111111"):
    app.text_input(key="pay_name").set_value("Test User")
    app.text_input(key="pay_card").set_value(card)
    app.text_input(key="pay_expiry").set_value("12/30")
    app.text_input(key="pay_cvv").set_value("123")
    pay_button(app).click().run()


def verify(app, code=None):
    code = code or app.session_state["otc_issued"]["otp"]
    app.text_input(key="otc_code").set_value(code).run()
    click(app, "Verify code")


def test_small_payment_needs_no_code():
    app = AppTest.from_file(APP).run()
    assert not any(b.label == "Send verification code" for b in app.button)
    assert not pay_button(app).disabled


def test_large_payment_locks_pay_button_until_verified():
    app = AppTest.from_file(APP).run()
    set_amount(app, BIG)
    assert any(b.label == "Send verification code" for b in app.button)
    assert pay_button(app).disabled
    click(app, "Send verification code")
    assert pay_button(app).disabled  # a code was sent, but not yet entered
    verify(app)
    assert not app.exception
    assert not pay_button(app).disabled
    assert any("Verified" in s.value for s in app.success)


def test_exactly_ten_thousand_needs_no_code():
    app = AppTest.from_file(APP).run()
    set_amount(app, 10_000.0)
    assert not any(b.label == "Send verification code" for b in app.button)


def test_wrong_code_is_refused_and_pay_stays_locked():
    app = AppTest.from_file(APP).run()
    set_amount(app, BIG)
    click(app, "Send verification code")
    real = app.session_state["otc_issued"]["otp"]
    wrong = "000000" if real != "000000" else "111111"
    verify(app, wrong)
    assert any("Wrong code" in e.value for e in app.error)
    assert pay_button(app).disabled


def test_changing_the_amount_after_verifying_locks_pay_again():
    app = AppTest.from_file(APP).run()
    set_amount(app, BIG)
    click(app, "Send verification code")
    verify(app)
    assert not pay_button(app).disabled
    set_amount(app, 90_000.0)  # the code was for Rs 12,000
    assert pay_button(app).disabled


def test_verified_large_payment_goes_through_then_next_one_needs_a_new_code():
    app = AppTest.from_file(APP).run()
    set_amount(app, BIG)
    click(app, "Send verification code")
    verify(app)
    fill_and_pay(app)
    assert app.session_state["payment_result"].status == "succeeded"
    assert app.session_state["payment_result"].amount_paise == 1_200_000
    # a new payment = a new attempt: the old verification does not carry over
    assert app.session_state["otc_verified"] is None
    assert pay_button(app).disabled
