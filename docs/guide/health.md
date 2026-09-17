---
title: Health
route: /health
summary: Is everything working? Service health, your own signals (answer rate, latency, failures), SIP trunk diagnostics, forwarding checks, open alerts, the daily synthetic test call and fault reports.
keywords: health, status, alerts, synthetic call, test call, sip diagnostics, forwarding diagnosis, latency, answer rate, fault report, provider report, outage
---

## Service health

The state of Parlio's platform components (telephony, voice, dashboard, messaging). A platform incident also shows a banner at the top of every page and on the public status page.

## Signals

Your business's own health over the last 24 hours and 7 days: answer rate, time to first word, failed calls, QA average and connector sync failures — with a traffic-light against normal ranges.

## Synthetic test calls

Parlio places one scripted test call a day to every business (a "book an appointment" style conversation) and after each deploy or configuration change. These appear here, not on your Calls page, and are not real customers. A failure raises an alert with the transcript. **Run test call now** triggers one on demand.

## SIP trunks and forwarding

For each connection on *Telephony*: registration state, last OPTIONS/INVITE result and audio quality (MOS, jitter, loss). **Diagnose** runs the full check and classifies the fault (your PBX, your provider, the network, or Parlio) with an evidence pack. **Diagnose forwarding** places a call to your forwarded number and reports whether the divert is working.

## Open alerts

Anything currently wrong, with what to do about it. Alerts clear themselves when the signal recovers or when a synthetic call passes.

## Fault reports

When a diagnosis points at your telephony provider, Parlio drafts a report you can **Copy** and send them, with timestamps, SIP call IDs and the evidence they will ask for.
