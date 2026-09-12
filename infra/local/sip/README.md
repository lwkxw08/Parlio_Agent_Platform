# SIP trunk + dispatch setup

Run once per environment with `lk` (LiveKit CLI) against that environment's LiveKit URL.
Secrets come from the environment, never from this repo.

## 1. Inbound trunk (Telnyx -> LiveKit SIP)

`telnyx-inbound-trunk.json` - authenticate Telnyx's SIP signalling IPs (or the credentials
configured on the Telnyx FQDN connection). Copy the current signalling IP ranges from the Telnyx
portal / docs into `allowed_addresses`; numbers are the Telnyx DIDs routed to the connection.

```sh
lk sip inbound create infra/local/sip/telnyx-inbound-trunk.json
```

## 2. Dispatch rule (which room/agent answers)

`dispatch-rule.json` - one room per call (`call-<random>`), explicitly dispatching the
`parlio-voice` agent so only the voice worker joins. The worker reads `sip.trunkPhoneNumber`
to resolve the tenant's assistant.

```sh
lk sip dispatch create infra/local/sip/dispatch-rule.json
```

## 3. Outbound trunk (transfers, callbacks - Phase 3)

`telnyx-outbound-trunk.json` skeleton points at `sip.telnyx.com` with the credential
connection's username/password supplied via env substitution at apply time.

## Carrier failover

Create a second inbound trunk (e.g. Twilio BYOC) with the same numbers; the same dispatch rule
matches both. Number routing is switched at the carrier through the `TelephonyProvider`
abstraction in `apps/api/parlio_api/telephony/`.
