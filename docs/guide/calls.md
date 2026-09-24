---
title: Calls
route: /calls
summary: Every inbound and outbound call with transcript, summary, recording, collected details and the reasons behind what the assistant did; filters and search across what was said.
keywords: calls, call log, transcript, recording, summary, search, filters, outbound, screened, blocked, missed, share, feedback, mark read, explain
---

## Call list

One row per call: time, caller (name once known, otherwise number), duration, outcome pill (Answered, Transferred, Ticket, Booking, Voicemail, No answer, Screened, Blocked, Outbound) and whether it is unread. Use the filters for period, outcome, department, assistant, location, new vs returning and direction (**Outbound** shows calls the assistant made).

## Search what was said

Type a phrase (a name, "boiler", "refund") to find calls where the transcript or summary contains it. Results highlight the moment; opening one jumps the recording to that point.

## Call detail

- **Summary** – what the caller wanted, what happened and any follow-up, written after the call.
- **Transcript** – conversation-style, speaker by speaker, with timestamps that seek the recording.
- **Recording** – caller and assistant tracks; a third "Team member" track appears for recorded warm transfers.
- **Extracted details** – a card on the Overview tab, under the summary, showing every field the assistant captured (name, phone, address, service, urgency…) and, separately, any required field it did **not** capture, so you can see at a glance what a follow-up needs to collect.
- **Details** – the contact record, ticket or booking created, messages sent.
- **Block caller** – on an inbound call, adds the caller's number to the assistant's *Blocked numbers* in one click (admins only; **Unblock caller** reverses it). Future calls from that number are ended immediately.
- **Why it did that** – the reasoning behind transfers, tickets and refusals (which rule or FAQ applied), so you can fix the configuration rather than guess.
- **QA score** – from the *Quality* page's scoring, with the signals that lowered it.
- **Share** – a link you can send to a colleague without a login; **Feedback** – thumbs up/down with a note, which feeds *Quality → Suggested FAQs & rules*.

## Contact and status

Callers are matched to *Contacts* by number. A returning caller is greeted by name if the assistant knows it; their status (Prospect, Customer, VIP, Blocked) is shown on the call.
