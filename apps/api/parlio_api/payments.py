"""Phase 12: mid-call payments and caller verification.

Payments
    The assistant never handles card data. It asks the caller for consent, we create a
    provider-hosted payment page (Stripe Checkout in ``payment`` mode, or a simulated page) and
    text the link via the Phase 5 SMS path. Requests are idempotent per
    ``(tenant, call, amount, description)`` so a retried tool call or a duplicate LLM turn cannot
    create two links. Provider webhooks flip the request to ``paid``/``expired``.

    Card-by-phone (DTMF) capture is exposed as a ``CardCaptureProvider`` seam only: it has to run
    on a telephony provider that strips digits from the media path before it reaches us (e.g.
    Twilio <Pay>, or a PCI proxy such as PCI Pal in front of the SIP trunk). No adapter ships yet
    - our current carrier path (Telnyx -> LiveKit SIP) would carry the tones through our media
    servers, which is out of PCI scope by design.

Verification
    Tenants store normalised **hashes** of a contact's date of birth / postcode / account
    reference (never the raw values). Answers spoken on a call are hashed the same way and compared
    server-side; the worker only ever learns matched / failed / locked, and transcripts and logs
    never see the answer.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from parlio_api.billing import verify_stripe_webhook
from parlio_api.messaging import MessageService
from parlio_api.store import CallStore, TenantDoc
from parlio_voice.models import SmsTrigger, VerificationField

log = logging.getLogger("parlio.api.payments")

PAYMENT_KIND = "payment_request"
IDENTITY_KIND = "contact_identity"
VERIFY_KIND = "verification_attempt"

MIN_STRIPE_EXPIRY_MINUTES = 30  # Stripe Checkout sessions must live >= 30 minutes


# -- payments -----------------------------------------------------------------------------------


class PaymentStatus(StrEnum):
    PENDING = "pending"  # link created, SMS not (yet) sent
    SENT = "sent"  # link texted to the caller
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"  # SMS delivery failed
    REFUNDED = "refunded"


class PaymentRequest(BaseModel):
    id: str = Field(default_factory=lambda: f"pay-{uuid4().hex[:10]}")
    tenant_id: str
    company_id: str
    assistant_id: str | None = None
    call_id: str | None = None
    contact_id: str | None = None
    to: str  # E.164 the link was/will be texted to
    amount_pence: int
    currency: str = "gbp"
    description: str
    status: PaymentStatus = PaymentStatus.PENDING
    url: str
    provider: str
    provider_ref: str  # checkout session id / simulated id
    payment_ref: str | None = None  # provider payment intent / charge once paid
    idempotency_key: str
    consent: bool = True
    verified_caller: bool = False
    requested_by: str = "ai"  # "ai" or agent email
    sms_message_id: str | None = None
    sms_status: str | None = None  # sent / skipped / failed - link stays usable regardless
    refunded_pence: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    paid_at: datetime | None = None

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=PAYMENT_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )

    @classmethod
    def from_doc(cls, d: TenantDoc) -> PaymentRequest:
        return cls.model_validate(d.data)

    @property
    def amount_display(self) -> str:
        sym = {"gbp": "£", "eur": "€", "usd": "$"}.get(self.currency.lower(), "")
        return f"{sym}{self.amount_pence / 100:.2f}"


class HostedPayment(BaseModel):
    url: str
    ref: str
    expires_at: datetime | None = None


class PaymentProvider(Protocol):
    """Creates provider-hosted payment pages; card data never touches Parlio."""

    name: str

    async def create_link(
        self,
        req: PaymentRequest,
        *,
        success_url: str,
        cancel_url: str,
    ) -> HostedPayment: ...

    async def refund(self, payment_ref: str, amount_pence: int | None) -> str: ...

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]: ...


class SimulatedPayments:
    name = "simulated"

    def __init__(self, dashboard_url: str = "http://localhost:3000") -> None:
        self.base = dashboard_url.rstrip("/")
        self.refunds: list[tuple[str, int | None]] = []

    async def create_link(
        self, req: PaymentRequest, *, success_url: str, cancel_url: str
    ) -> HostedPayment:
        ref = f"sim_cs_{uuid4().hex[:12]}"
        return HostedPayment(
            url=f"{self.base}/pay/{ref}",
            ref=ref,
            expires_at=req.expires_at,
        )

    async def refund(self, payment_ref: str, amount_pence: int | None) -> str:
        self.refunds.append((payment_ref, amount_pence))
        return f"sim_re_{uuid4().hex[:8]}"

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]:
        return dict(json.loads(payload))


class StripePayments:
    """Stripe Checkout (mode=payment) via REST - hosted page, card entry on stripe.com."""

    name = "stripe"

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._webhook_secret = webhook_secret
        self._http = client or httpx.AsyncClient(
            base_url="https://api.stripe.com/v1", auth=(secret_key, ""), timeout=15
        )

    async def create_link(
        self, req: PaymentRequest, *, success_url: str, cancel_url: str
    ) -> HostedPayment:
        data: dict[str, str] = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": req.id,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": req.currency,
            "line_items[0][price_data][unit_amount]": str(req.amount_pence),
            "line_items[0][price_data][product_data][name]": req.description[:120],
            "metadata[payment_id]": req.id,
            "metadata[tenant_id]": req.tenant_id,
            "payment_intent_data[metadata][payment_id]": req.id,
        }
        if req.call_id:
            data["metadata[call_id]"] = req.call_id
        if req.expires_at:
            data["expires_at"] = str(int(req.expires_at.timestamp()))
        r = await self._http.post(
            "/checkout/sessions", data=data, headers={"Idempotency-Key": req.idempotency_key}
        )
        r.raise_for_status()
        s = r.json()
        exp = s.get("expires_at")
        return HostedPayment(
            url=s["url"],
            ref=s["id"],
            expires_at=datetime.fromtimestamp(exp, UTC) if exp else req.expires_at,
        )

    async def refund(self, payment_ref: str, amount_pence: int | None) -> str:
        data = {"payment_intent": payment_ref}
        if amount_pence is not None:
            data["amount"] = str(amount_pence)
        r = await self._http.post("/refunds", data=data)
        r.raise_for_status()
        return str(r.json()["id"])

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]:
        return verify_stripe_webhook(self._webhook_secret, payload, signature)


class CardCaptureProvider(Protocol):
    """PCI-scoped DTMF card capture on the telephony leg (Twilio <Pay>, PCI Pal, ...).

    Implementations must keep DTMF/audio containing PANs off Parlio's media servers and return
    only a provider token. None ship yet; ``PaymentService.card_capture`` is ``None`` unless one
    is configured platform-side, and the assistant only offers card-by-phone when it is.
    """

    name: str

    async def start(self, call_id: str, req: PaymentRequest) -> str: ...

    async def result(self, capture_ref: str) -> str | None: ...


def payment_idempotency_key(
    tenant_id: str,
    call_id: str | None,
    to: str,
    amount_pence: int,
    description: str,
    *,
    contact_id: str | None = None,
    currency: str = "gbp",
) -> str:
    """Same tenant + call + customer + amount/currency/description => same link (retry-safe)."""
    raw = "|".join(
        [
            tenant_id,
            call_id or "",
            contact_id or "",
            to,
            currency.lower(),
            str(amount_pence),
            description.strip().lower(),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class PaymentError(ValueError):
    pass


# -- verification -------------------------------------------------------------------------------


def normalise_answer(field: VerificationField, value: str) -> str:
    v = value.strip().lower()
    if field is VerificationField.POSTCODE:
        return re.sub(r"[^a-z0-9]", "", v)
    if field is VerificationField.DOB:
        digits = re.sub(r"\D", "", v)
        # Accept DDMMYYYY, YYYYMMDD or ISO; store as YYYYMMDD.
        if len(digits) == 8:
            if digits[:2] in {"19", "20"}:
                return digits
            return digits[4:] + digits[2:4] + digits[:2]
        return digits
    return re.sub(r"[^a-z0-9]", "", v)


class ContactIdentity(BaseModel):
    """Salted hashes of a contact's verification answers; raw values are never stored."""

    id: str  # contact_id
    tenant_id: str
    hashes: dict[str, str] = Field(default_factory=dict)  # field -> hmac hex
    salt: str = Field(default_factory=lambda: uuid4().hex)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=IDENTITY_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
        )

    def digest(self, field: VerificationField, value: str) -> str:
        return hmac.new(
            self.salt.encode(), f"{field}:{normalise_answer(field, value)}".encode(), hashlib.sha256
        ).hexdigest()

    def set(self, field: VerificationField, value: str) -> None:
        self.hashes[field.value] = self.digest(field, value)
        self.updated_at = datetime.now(UTC)

    def check(self, field: VerificationField, value: str) -> bool:
        stored = self.hashes.get(field.value)
        return bool(stored) and hmac.compare_digest(stored or "", self.digest(field, value))

    @property
    def fields(self) -> list[VerificationField]:
        return [VerificationField(f) for f in self.hashes]


class VerificationOutcome(StrEnum):
    VERIFIED = "verified"
    FAILED = "failed"
    LOCKED = "locked"  # max attempts reached on this call
    NO_RECORD = "no_record"  # contact has no stored answers for the requested fields


class VerificationAttempt(BaseModel):
    """Redacted audit record: which fields were checked and the outcome - never the answers."""

    id: str = Field(default_factory=lambda: f"vf-{uuid4().hex[:10]}")
    tenant_id: str
    call_id: str
    contact_id: str | None
    fields: list[VerificationField]
    matched: int
    required: int
    outcome: VerificationOutcome
    attempt: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_doc(self) -> TenantDoc:
        return TenantDoc(
            kind=VERIFY_KIND,
            id=self.id,
            tenant_id=self.tenant_id,
            data=self.model_dump(mode="json"),
            created_at=self.created_at,
        )


class VerificationResult(BaseModel):
    outcome: VerificationOutcome
    matched: int
    required: int
    attempts_left: int
    fields_available: list[VerificationField] = Field(default_factory=list)


# -- service ------------------------------------------------------------------------------------


class PaymentService:
    def __init__(
        self,
        store: CallStore,
        provider: PaymentProvider,
        sms: MessageService,
        *,
        dashboard_url: str,
        card_capture: CardCaptureProvider | None = None,
    ) -> None:
        self.store = store
        self.provider = provider
        self.sms = sms
        self.dashboard_url = dashboard_url.rstrip("/")
        self.card_capture = card_capture
        self.on_paid: Callable[[str, str, int], Awaitable[None]] | None = None

    # -- payment links ---

    async def list_requests(self, tenant_id: str, limit: int = 200) -> list[PaymentRequest]:
        docs = await self.store.list_docs(PAYMENT_KIND, tenant_id, limit)
        out = [PaymentRequest.from_doc(d) for d in docs]
        out.sort(key=lambda p: p.created_at, reverse=True)
        return out

    async def get(self, tenant_id: str, payment_id: str) -> PaymentRequest | None:
        d = await self.store.get_doc(PAYMENT_KIND, payment_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return PaymentRequest.from_doc(d)

    async def for_call(self, tenant_id: str, call_id: str) -> list[PaymentRequest]:
        return [p for p in await self.list_requests(tenant_id) if p.call_id == call_id]

    async def _by_key(self, tenant_id: str, key: str) -> PaymentRequest | None:
        for p in await self.list_requests(tenant_id, 1000):
            if p.idempotency_key == key and p.status not in {
                PaymentStatus.CANCELLED,
                PaymentStatus.EXPIRED,
                PaymentStatus.FAILED,
            }:
                return p
        return None

    async def request_link(
        self,
        tenant_id: str,
        company_id: str,
        *,
        to: str,
        amount_pence: int,
        description: str,
        consent: bool,
        call_id: str | None = None,
        assistant_id: str | None = None,
        contact_id: str | None = None,
        currency: str = "gbp",
        max_pence: int | None = None,
        ttl_minutes: int = 30,
        verified_caller: bool = False,
        requested_by: str = "ai",
        send_sms: bool = True,
    ) -> PaymentRequest:
        """Create (or return the existing) hosted pay link and text it to the caller."""
        if not consent:
            raise PaymentError("caller consent is required before sending a payment link")
        if amount_pence <= 0:
            raise PaymentError("amount must be positive")
        if max_pence is not None and amount_pence > max_pence:
            raise PaymentError(f"amount exceeds the assistant's limit of {max_pence / 100:.2f}")
        if not description.strip():
            raise PaymentError("description is required")
        key = payment_idempotency_key(
            tenant_id,
            call_id,
            to,
            amount_pence,
            description,
            contact_id=contact_id,
            currency=currency,
        )
        existing = await self._by_key(tenant_id, key)
        if existing is not None:
            return existing

        ttl = max(ttl_minutes, MIN_STRIPE_EXPIRY_MINUTES if self.provider.name == "stripe" else 1)
        req = PaymentRequest(
            tenant_id=tenant_id,
            company_id=company_id,
            assistant_id=assistant_id,
            call_id=call_id,
            contact_id=contact_id,
            to=to,
            amount_pence=amount_pence,
            currency=currency.lower(),
            description=description.strip(),
            url="",
            provider=self.provider.name,
            provider_ref="",
            idempotency_key=key,
            verified_caller=verified_caller,
            requested_by=requested_by,
            expires_at=datetime.now(UTC) + timedelta(minutes=ttl),
        )
        hosted = await self.provider.create_link(
            req,
            success_url=f"{self.dashboard_url}/pay/done?p={req.id}",
            cancel_url=f"{self.dashboard_url}/pay/cancelled?p={req.id}",
        )
        req.url = hosted.url
        req.provider_ref = hosted.ref
        if hosted.expires_at:
            req.expires_at = hosted.expires_at
        await self.store.put_doc(req.to_doc())

        if send_sms:
            req = await self.send_link(req)
        log.info(
            "payment link %s %s %s for tenant %s (%s)",
            req.id,
            req.amount_display,
            req.status,
            tenant_id,
            self.provider.name,
        )
        return req

    async def send_link(self, req: PaymentRequest) -> PaymentRequest:
        body = f"Secure payment link for {req.amount_display} - {req.description}: {req.url}"
        if req.expires_at:
            body += f" (expires {req.expires_at.astimezone(UTC).strftime('%H:%M')} UTC)"
        msg = await self.sms.send(
            req.tenant_id,
            req.company_id,
            req.to,
            body,
            call_id=req.call_id,
            trigger=SmsTrigger.PAYMENT_LINK,
        )
        req.sms_message_id = msg.id
        req.sms_status = str(msg.status)
        if msg.status == "sent":
            req.status = PaymentStatus.SENT
        await self.store.put_doc(req.to_doc())
        return req

    async def cancel(self, tenant_id: str, payment_id: str) -> PaymentRequest | None:
        p = await self.get(tenant_id, payment_id)
        if p is None:
            return None
        if p.status in {PaymentStatus.PENDING, PaymentStatus.SENT}:
            p.status = PaymentStatus.CANCELLED
            await self.store.put_doc(p.to_doc())
        return p

    async def refund(
        self, tenant_id: str, payment_id: str, amount_pence: int | None = None
    ) -> PaymentRequest:
        p = await self.get(tenant_id, payment_id)
        if p is None:
            raise PaymentError("payment not found")
        if p.status not in {PaymentStatus.PAID, PaymentStatus.REFUNDED} or not p.payment_ref:
            raise PaymentError("only paid requests can be refunded")
        remaining = p.amount_pence - p.refunded_pence
        amt = remaining if amount_pence is None else amount_pence
        if amt <= 0 or amt > remaining:
            raise PaymentError("refund exceeds the remaining amount")
        await self.provider.refund(p.payment_ref, None if amt == p.amount_pence else amt)
        p.refunded_pence += amt
        if p.refunded_pence >= p.amount_pence:
            p.status = PaymentStatus.REFUNDED
        await self.store.put_doc(p.to_doc())
        return p

    async def handle_webhook(self, payload: bytes, signature: str | None) -> PaymentRequest | None:
        event = self.provider.verify_webhook(payload, signature)
        kind = str(event.get("type", ""))
        obj = event.get("data", {}).get("object", {}) if "data" in event else event
        pid = (obj.get("metadata") or {}).get("payment_id") or obj.get("client_reference_id")
        if not pid:
            return None
        d = await self.store.get_doc(PAYMENT_KIND, str(pid))
        if d is None:
            return None
        p = PaymentRequest.from_doc(d)
        if kind in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            p.status = PaymentStatus.PAID
            p.paid_at = datetime.now(UTC)
            intent = obj.get("payment_intent")
            if intent:
                p.payment_ref = str(intent)
            if self.on_paid is not None:
                try:
                    await self.on_paid(p.tenant_id, p.to, p.amount_pence)
                except Exception:
                    log.warning("paid hook failed for %s", p.id, exc_info=True)
        elif kind == "checkout.session.expired":
            if p.status in {PaymentStatus.PENDING, PaymentStatus.SENT}:
                p.status = PaymentStatus.EXPIRED
        elif kind == "checkout.session.async_payment_failed":
            p.status = PaymentStatus.FAILED
        else:
            return p
        await self.store.put_doc(p.to_doc())
        return p

    async def sweep_expired(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        n = 0
        for d in await self.store.list_docs(PAYMENT_KIND, None, 10000):
            p = PaymentRequest.from_doc(d)
            if (
                p.status in {PaymentStatus.PENDING, PaymentStatus.SENT}
                and p.expires_at
                and p.expires_at <= now
            ):
                p.status = PaymentStatus.EXPIRED
                await self.store.put_doc(p.to_doc())
                n += 1
        return n

    # -- verification ---

    async def identity(self, tenant_id: str, contact_id: str) -> ContactIdentity | None:
        d = await self.store.get_doc(IDENTITY_KIND, contact_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return ContactIdentity.model_validate(d.data)

    async def set_identity(
        self, tenant_id: str, contact_id: str, answers: dict[VerificationField, str]
    ) -> ContactIdentity:
        ident = await self.identity(tenant_id, contact_id) or ContactIdentity(
            id=contact_id, tenant_id=tenant_id
        )
        for f, v in answers.items():
            if v.strip():
                ident.set(f, v)
            else:
                ident.hashes.pop(f.value, None)
        await self.store.put_doc(ident.to_doc())
        return ident

    async def clear_identity(self, tenant_id: str, contact_id: str) -> bool:
        if await self.identity(tenant_id, contact_id) is None:
            return False
        return await self.store.delete_doc(IDENTITY_KIND, contact_id)

    async def attempts_for_call(self, tenant_id: str, call_id: str) -> list[VerificationAttempt]:
        docs = await self.store.list_docs(VERIFY_KIND, tenant_id, 5000)
        out = [VerificationAttempt.model_validate(d.data) for d in docs]
        return sorted((a for a in out if a.call_id == call_id), key=lambda a: a.created_at)

    async def verify(
        self,
        tenant_id: str,
        call_id: str,
        contact_id: str | None,
        answers: dict[VerificationField, str],
        *,
        required_matches: int = 1,
        max_attempts: int = 3,
    ) -> VerificationResult:
        """Compare spoken answers against stored hashes. ``answers`` are consumed, never stored."""
        prior = await self.attempts_for_call(tenant_id, call_id)
        attempt_no = len(prior) + 1
        if any(a.outcome is VerificationOutcome.VERIFIED for a in prior):
            return VerificationResult(
                outcome=VerificationOutcome.VERIFIED,
                matched=required_matches,
                required=required_matches,
                attempts_left=max(0, max_attempts - len(prior)),
            )
        if len(prior) >= max_attempts:
            return VerificationResult(
                outcome=VerificationOutcome.LOCKED,
                matched=0,
                required=required_matches,
                attempts_left=0,
            )
        ident = await self.identity(tenant_id, contact_id) if contact_id else None
        fields = list(answers)
        if ident is None or not any(f.value in ident.hashes for f in fields):
            outcome = VerificationOutcome.NO_RECORD
            matched = 0
        else:
            matched = sum(1 for f, v in answers.items() if ident.check(f, v))
            if matched >= required_matches:
                outcome = VerificationOutcome.VERIFIED
            elif attempt_no >= max_attempts:
                outcome = VerificationOutcome.LOCKED
            else:
                outcome = VerificationOutcome.FAILED
        await self.store.put_doc(
            VerificationAttempt(
                tenant_id=tenant_id,
                call_id=call_id,
                contact_id=contact_id,
                fields=fields,
                matched=matched,
                required=required_matches,
                outcome=outcome,
                attempt=attempt_no,
            ).to_doc()
        )
        log.info(
            "verification %s call=%s fields=%s matched=%d/%d attempt=%d",
            outcome,
            call_id,
            [f.value for f in fields],
            matched,
            required_matches,
            attempt_no,
        )
        return VerificationResult(
            outcome=outcome,
            matched=matched,
            required=required_matches,
            attempts_left=max(0, max_attempts - attempt_no),
            fields_available=ident.fields if ident else [],
        )
