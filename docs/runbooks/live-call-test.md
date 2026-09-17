# Live-call test script (Part F — human-in-the-loop)

Everything else in Part F is automated (see `docs/runbooks/e2e-and-load.md`). The items below
need a real phone, a real SIP endpoint or a real SMS handset, so a person runs them and
engineering checks the evidence afterwards. Tick each row only after the evidence is confirmed —
an automated pass is **not** evidence for these.

Before you start (Parlio Demo Plumbing on the droplet):
- Studio → Recording: "Record calls" on; "Record transferred calls" on for step 5.
- Integrations → Notifications: "Text me after every call" on, owner mobile set.
- Integrations → Calendar tab → "Appointment reminders" (below Bookings): policy enabled with offsets that will fire during the test (e.g. 1h and 15 min),
  or book a slot ~1h15 ahead.
- Studio (Assistant) → "Call screening" section: Screen = "Unknown callers only", tick "Reject withheld".
- Transfers: a department whose destination is a colleague's real mobile who is ready to answer.

Demo number: **020 4620 6823** (+442046206823). Say a fake but consistent postcode/address so the
ticket can be identified in the dashboard.

| # | What you do | What you should experience | Evidence engineering checks |
|---|---|---|---|
| 1 | **Forwarding.** Divert your mobile to the demo number (`**21*02046206823#`, `##21#` to cancel). Ring your mobile from another phone. | Answer within ~1 ring; consent line + greeting; assistant hears you and replies in ~1–1.5 s. Ask for a plumber callback, give name/number/postcode; hang up. | Calls: new inbound call, caller = your other phone, outcome "ticketed", contact name; Tickets: one callback ticket, no duplicate; recording plays (caller + assistant tracks); transcript readable; Health → latency: answer < 2 s, turn p95 < 2.5 s. |
| 2 | **Owner SMS summary.** Right after call 1. | One text to the owner mobile within ~30 s: name, reason, callback number/address. No second text. | Integrations → Notifications log: one `call.completed` SMS `sent`; Telnyx messaging log; Billing usage SMS +1. |
| 3 | **PBX / SIP mode.** From the PBX (or a softphone registered to it) dial the DDI routed to Parlio; if Telephony is in BYO-registration mode, dial the number Parlio registered on your trunk. | Same answer/greeting behaviour as 1; audio clean both ways for 60 s (no clipping/echo). | Telephony → trunk: registration "up", last INVITE ok, MOS ≥ 4.0; Calls: trunk mode shown on the call; no `sip` alert on Ops. |
| 4 | **Screening.** Call from a withheld number (prefix `141`), then from an unknown number you have not used before. | Withheld: rejected before the greeting (busy / short notice). Unknown: asked who is calling and why, then helped normally. | Calls: withheld shows "Blocked", unknown shows "Screened" and then the normal outcome; no minutes billed for the blocked call. |
| 5 | **Warm transfer to a real person.** Call in, ask for the department from the prep step. | "Putting you through to …" hold music/silence < 10 s, colleague's phone rings, you both hear each other, assistant leaves; hang up after 30 s of talking. Then repeat with the colleague **not** answering. | Transfers: `answered` with human duration ≈ 30 s and "Recorded"; call detail has a third "Team member" track. Second attempt: `no_answer` → assistant offers a callback and a ticket is created (no dropped call). |
| 6 | **Booking + SMS reminders.** Call in and book the earliest slot offered (must be inside the reminder offsets). | Booking confirmed on the call. Reminder text arrives at the offset. Reply **1** → confirmation text. On a second booking reply **2** → "we'll call you" text. Then text **STOP**, book again — no reminder; **START** restores. | Bookings: status confirmed / reschedule-requested; Tickets: one reschedule ticket; Inbox: the reply thread; reminders after STOP show `suppressed`; calendar (Google or Microsoft) has the events. |
| 7 | **AI call back with a resolution.** On the ticket from 1, "Send to AI call back" with a typed answer, then **Dial now**. | Your phone rings from 020 4620 6823; assistant states who it is and delivers the answer, does **not** re-ask your details, closes. | Calls: Outbound pill, your number; ticket closed with the attempt note; no new ticket. |
| 8 | **Web chat + WhatsApp/SMS inbound** (optional). Message the demo number by SMS; open the chat widget. | Reply within ~5 s, correct opening hours / FAQ answer; "speak to a human" hands off. | Inbox threads for each channel; handoff shows in Live/alerts. |

## After the session
Engineering runs `parlio-e2e --keep` against the droplet to confirm read models still agree, then
records: date, tester, rows passed/failed, call ids, and the latency report (`/v1/observability/latency`)
in the PR or session notes. Anything failing becomes a ticket against the relevant runbook.

## Known limits
- Cold (REFER) transfers leave the platform and cannot be recorded — keep warm transfers.
- Reminders only fire for bookings whose offsets are still in the future; book far enough ahead.
- Outlook needs `PARLIO_MICROSOFT_CLIENT_ID/SECRET` on the droplet; Google needs the OAuth connection.
