"""Payment decision gate and minimal, session-only audit records."""
from __future__ import annotations

from core.geo_risk import (
    DEFAULT_POLICY,
    LocationObservation,
    RiskAssessment,
    RiskPolicy,
    assess_transaction,
)

PAYMENT_ALLOWED_DECISIONS = frozenset({"ALLOW", "ALLOW_WITH_WARNING"})


def evaluate_payment_location(
    account_id: str,
    observation: LocationObservation | None,
    policy: RiskPolicy = DEFAULT_POLICY,
) -> RiskAssessment:
    """Perform risk assessment at the payment boundary, before provider invocation."""
    return assess_transaction(account_id, observation, policy)


def payment_is_allowed(assessment: RiskAssessment) -> bool:
    """Fail closed: UNKNOWN, BLOCK, and REVIEW decisions cannot invoke the provider."""
    return assessment.decision in PAYMENT_ALLOWED_DECISIONS


def audit_record(transaction_ref: str, assessment: RiskAssessment) -> dict:
    """Create a minimal record. The caller decides how long to keep it."""
    return {
        "transaction_ref": transaction_ref,
        "risk_level": assessment.risk_level,
        "decision": assessment.decision,
        "distance_km": round(assessment.distance_km, 1)
        if assessment.distance_km is not None
        else None,
        "reason_code": assessment.reason_code,
        "assessed_at": assessment.assessed_at,
        "simulated": assessment.demo_mode,
    }
