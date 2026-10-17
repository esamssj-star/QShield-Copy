"""Small payment-flow helper that prevents blocked attempts reaching a provider."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.geo_risk import RiskAssessment
from core.risk_gate import payment_is_allowed


def invoke_payment_provider(
    assessment: RiskAssessment,
    provider: Callable[..., Any],
    **provider_arguments: Any,
) -> Any | None:
    """Call the payment operation only for ALLOW or ALLOW_WITH_WARNING.

    Returning None means the provider was not called. Callers should check the
    assessment decision first so this cannot be confused with a provider result.
    """
    if not payment_is_allowed(assessment):
        return None
    return provider(**provider_arguments)
