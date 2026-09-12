"""Carrier abstraction. Telnyx is the primary carrier; additional providers register here
so a secondary trunk (e.g. Twilio, Gamma) can be added for failover without touching business logic.
"""

from __future__ import annotations

from parlio_api.telephony.base import CarrierHealth, PhoneNumber, TelephonyProvider
from parlio_api.telephony.telnyx import TelnyxProvider

PROVIDERS: dict[str, type[TelephonyProvider]] = {"telnyx": TelnyxProvider}

__all__ = ["PROVIDERS", "CarrierHealth", "PhoneNumber", "TelephonyProvider", "TelnyxProvider"]
