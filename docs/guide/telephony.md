---
title: Telephony
route: /telephony
summary: How calls reach ParlioTec — forwarding from your existing number, connecting your phone system (PBX) or registering a SIP handset — plus number routing.
keywords: telephony, forwarding, divert, forward calls, pbx, sip, trunk, registration, ddi, numbers, routing, test call, extension transfers, mobile divert, bt, vodafone, teams
---

## Your connections

Three ways to get calls to your assistant. You can use more than one.

1. **Forward your number** – the simplest. Divert your existing landline or mobile to the ParlioTec number shown on the *Billing → Your numbers* page. Nothing to install. Divert codes for BT, Virgin, Vodafone, EE, O2, Three and Microsoft Teams are on the *Launch guide*.
2. **Connect your phone system (PBX)** – ParlioTec gives you SIP credentials (shown once — copy them). Point your PBX at them, and set the **PBX address** so the assistant can transfer callers to internal extensions. **Allowed source IPs** restricts who may send calls on this connection.
3. **Register a SIP handset or provider** – enter the **Registrar**, **Username / extension**, **Password**, optional **Outbound proxy**, **Transport** (UDP/TCP/TLS) and **DTMF** mode. ParlioTec registers to your provider as if it were a phone, so your existing number rings the assistant with no forwarding.

For any connection: **Max concurrent calls** caps simultaneous calls; **Refresh** re-checks registration; **Test call** places a synthetic call through the connection and reports the result; **Rotate creds** issues new SIP credentials; **Remove** deletes it.

## Numbers (DDIs) and routing

Each number that reaches ParlioTec can be routed to a specific assistant (and, if you have several locations, to a location). Use this when one connection carries several numbers — for example a sales line and a support line answered differently.

## PBX credentials — copy now, shown once

When you create a PBX connection the SIP username and password appear once. Store them in your PBX and press **I have saved these**. If they are lost, use **Rotate creds** to generate new ones.

## Troubleshooting

- Assistant doesn't answer a forwarded call: check the divert is set to "always", and that the call arrives on *Calls* as "No answer" or not at all — the *Health* page's **Diagnose forwarding** explains which.
- Registration shows failed: check username/password and transport; many providers need TLS on port 5061. **Test call** on the *Health → SIP trunks* panel runs OPTIONS/INVITE/audio checks and produces a report you can send to your provider.
