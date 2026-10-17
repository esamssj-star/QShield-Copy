"""Dynamic one-time credential: bound to the payment, expires, works once."""
import pytest

from core import dynamic_credential as otc
from core.dynamic_credential import CredentialError, MemoryCredentialStore

D = dict(user_id="user-1", merchant="shop-1", amount_paise=1_200_000, currency="INR", txn_id="txn-1")
T0 = 1_000_000.0


def fresh():
    return MemoryCredentialStore()


def test_threshold_is_strictly_above_ten_thousand_rupees():
    assert not otc.requires_verification(1_000_000)  # exactly Rs 10,000
    assert otc.requires_verification(1_000_001)
    assert not otc.requires_verification(49_900)


def test_issue_gives_six_digits_and_verify_consumes_it():
    store = fresh()
    cred = store.issue(**D, now=T0)
    assert len(cred.otp) == 6 and cred.otp.isdigit()
    assert cred.expires_at == T0 + otp_ttl()
    store.verify(**D, otp=cred.otp, now=T0 + 10)  # no exception = verified
    with pytest.raises(CredentialError, match="already used"):
        store.verify(**D, otp=cred.otp, now=T0 + 11)  # consumed: cannot be replayed


def otp_ttl():
    return otc.TTL_SECONDS


def test_code_fails_if_the_amount_changes():
    store = fresh()
    cred = store.issue(**D, now=T0)
    with pytest.raises(CredentialError, match="details changed"):
        store.verify(**{**D, "amount_paise": 9_000_000}, otp=cred.otp, now=T0 + 1)


@pytest.mark.parametrize("field,value", [("user_id", "user-2"), ("merchant", "shop-2"),
                                         ("currency", "USD")])
def test_code_fails_for_a_different_user_merchant_or_currency(field, value):
    store = fresh()
    cred = store.issue(**D, now=T0)
    with pytest.raises(CredentialError, match="details changed"):
        store.verify(**{**D, field: value}, otp=cred.otp, now=T0 + 1)


def test_code_fails_for_a_different_transaction():
    store = fresh()
    cred = store.issue(**D, now=T0)
    with pytest.raises(CredentialError, match="No code was requested"):
        store.verify(**{**D, "txn_id": "txn-2"}, otp=cred.otp, now=T0 + 1)


def test_expired_code_is_refused():
    store = fresh()
    cred = store.issue(**D, now=T0)
    with pytest.raises(CredentialError, match="expired"):
        store.verify(**D, otp=cred.otp, now=T0 + otp_ttl())  # at the expiry instant


def test_wrong_codes_lock_the_credential_even_for_the_right_code(monkeypatch):
    monkeypatch.setattr(otc, "_new_otp", lambda: "123456")
    store = fresh()
    store.issue(**D, now=T0)
    with pytest.raises(CredentialError, match="2 attempt"):
        store.verify(**D, otp="000001", now=T0 + 1)
    with pytest.raises(CredentialError, match="1 attempt"):
        store.verify(**D, otp="000002", now=T0 + 2)
    with pytest.raises(CredentialError, match="Too many"):
        store.verify(**D, otp="000003", now=T0 + 3)
    with pytest.raises(CredentialError, match="Too many"):
        store.verify(**D, otp="123456", now=T0 + 4)  # right code, but locked


def test_badly_formatted_code_is_not_counted_as_a_guess(monkeypatch):
    monkeypatch.setattr(otc, "_new_otp", lambda: "123456")
    store = fresh()
    store.issue(**D, now=T0)
    for bad in ["", "12", "abcdef", "1234567"]:
        with pytest.raises(CredentialError, match="6-digit"):
            store.verify(**D, otp=bad, now=T0 + 1)
    store.verify(**D, otp=" 123456 ", now=T0 + 2)  # spaces around it are fine


def test_resend_replaces_the_old_code(monkeypatch):
    codes = iter(["111111", "222222"])
    monkeypatch.setattr(otc, "_new_otp", lambda: next(codes))
    store = fresh()
    store.issue(**D, now=T0)
    store.issue(**D, now=T0 + 5)  # resend
    with pytest.raises(CredentialError):
        store.verify(**D, otp="111111", now=T0 + 6)  # old code is dead
    store.verify(**D, otp="222222", now=T0 + 7)


def test_resend_is_limited_per_transaction():
    store = fresh()
    for i in range(otc.MAX_ISSUES):
        store.issue(**D, now=T0 + i)
    with pytest.raises(CredentialError, match="Too many codes"):
        store.issue(**D, now=T0 + 10)


def test_cannot_issue_again_after_a_successful_verification():
    store = fresh()
    cred = store.issue(**D, now=T0)
    store.verify(**D, otp=cred.otp, now=T0 + 1)
    with pytest.raises(CredentialError, match="already verified"):
        store.issue(**D, now=T0 + 2)


def test_the_store_never_keeps_the_code(monkeypatch):
    monkeypatch.setattr(otc, "_new_otp", lambda: "424242")
    store = fresh()
    store.issue(**D, now=T0)
    assert "424242" not in repr(store._records)


def test_a_different_server_key_cannot_verify(monkeypatch):
    monkeypatch.setenv("QSHIELD_OTC_KEY", "a" * 40)
    store = fresh()
    cred = store.issue(**D, now=T0)
    monkeypatch.setenv("QSHIELD_OTC_KEY", "b" * 40)
    with pytest.raises(CredentialError, match="details changed"):
        store.verify(**D, otp=cred.otp, now=T0 + 1)


def test_short_server_key_is_refused(monkeypatch):
    monkeypatch.setenv("QSHIELD_OTC_KEY", "too-short")
    with pytest.raises(CredentialError, match="32 characters"):
        fresh().issue(**D, now=T0)


def test_bad_inputs_are_refused():
    store = fresh()
    with pytest.raises(CredentialError):
        store.issue(**{**D, "amount_paise": 0}, now=T0)
    with pytest.raises(CredentialError):
        store.issue(**{**D, "amount_paise": True}, now=T0)
    with pytest.raises(CredentialError):
        store.issue(**{**D, "txn_id": ""}, now=T0)


def test_old_finished_records_are_forgotten():
    store = fresh()
    store.issue(**D, now=T0)
    store.issue(**{**D, "txn_id": "txn-9"}, now=T0 + otc.TTL_SECONDS + 4000)
    assert "txn-1" not in store._records and "txn-9" in store._records
