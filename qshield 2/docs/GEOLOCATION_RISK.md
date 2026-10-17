# QShield geolocation risk screening

## Scope and architecture

The current repository's payment flow is the Python/Streamlit application under qshield 2/. The feature places a testable location risk engine in core/geo_risk.py and a decision gate in core/risk_gate.py. views/payment.py runs the assessment after the existing name and one-time-code checks and before invoking the mock provider in core/mock_provider.py. core/payment_flow.py refuses to invoke a provider unless the decision is ALLOW or ALLOW_WITH_WARNING.

This is a hackathon simulation. The provider only accepts published test-card numbers and never calls a bank or payment processor; no money moves. There is no authenticated bank account system or persistent transaction database. The account-to-branch data and audit entries are synthetic and in Streamlit session memory. This change does not create production-grade fraud prevention.

## Demo accounts and locations

Use the closed expander named “Location risk demo controls” in the Pay tab. Choose one of the synthetic accounts and a location source:

- Simulated demo location: deterministic and suitable for judging. “Same city as selected branch” is LOW; Mysuru is tested as a nearby city; Delhi and Berlin are distant; “Unknown location” deliberately has no coordinates.
- Device location (GPS): requests the browser location only when the location component button is pressed. It relies on browser permission and a secure context (HTTPS or localhost). No third-party reverse geocoder is used. City is not inferred from the coordinates. If location is denied, absent, malformed, or too imprecise, the assessment is UNKNOWN and is blocked under the default policy.

The browser observation contains only the location reading needed for the current session. The minimal audit record does not store exact coordinates, a full account identifier, card data, or a location history. A branch is a risk reference only, not proof of where a user lives or must be located.

## Risk policy

The thresholds live in RiskPolicy in core/geo_risk.py and are illustrative demo settings, not bank-validated fraud thresholds.

- LOW: distance under 100 km; ALLOW.
- MEDIUM: 100 km to less than 500 km; ALLOW_WITH_WARNING.
- HIGH: 500 km or more; BLOCK before the mock provider call.
- UNKNOWN: account branch, branch coordinates, transaction coordinates, or reliable device accuracy cannot be established. Default unknown-location policy is BLOCK. RiskPolicy can be configured to REVIEW; REVIEW still cannot call the provider.

The location accuracy ceiling for browser readings is 5,000 metres. Distances use the Haversine great-circle calculation and retain full precision for risk classification; only display and audit summaries are rounded. Distance is a signal, not proof of fraud.

## Safe transaction order

1. Validate the basic payment form and required one-time code.
2. Resolve the selected synthetic account's branch.
3. Obtain a deterministic demo observation or a browser observation.
4. Assess risk and append a minimal, session-only decision record.
5. Stop for BLOCK or REVIEW. The mock provider, token creation, wallet handoff and success display do not run for a blocked attempt.
6. Only ALLOW and ALLOW_WITH_WARNING can reach the mock provider. The provider continues to validate test card and amount fields.

A blocked attempt clears prior payment-result/token/handoff UI state for that attempt and rotates the attempt reference. The location reset control clears only the geolocation demo state and audit entries; it does not clear the payment ledger or unrelated QShield state.

## Run and test

From the application directory:

    cd "qshield 2"
    pip install -r requirements-dev.txt
    pytest tests/test_geo_risk.py
    pytest
    streamlit run app.py

Use a listed test card only. For the demonstration, first choose the Bengaluru demo account and “Same city as selected branch”; a valid test-card attempt follows the existing mock payment flow. Then start a new attempt and select “Berlin, Germany · international”; the screen should show HIGH risk and BLOCKED, with no provider result, payment token, wallet handoff, or success receipt. “Unknown location” and the two unconfigured synthetic branch cases also block.

## Security and deployment limits

The browser sends its geolocation observation to the Streamlit app, so a modified client could spoof it. The server-side gate is authoritative only within this demo's Python application process, not a substitute for a trusted bank/payment-processor authorization service. There is no persistent risk audit or production authorization endpoint. Never represent branch distance as a sole production fraud signal, do not use real account/card data, and do not claim that the prototype protects or transfers real money.
