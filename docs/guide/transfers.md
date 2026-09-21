---
title: Transfers
route: /handoff
summary: Departments and the people or numbers the assistant transfers callers to, warm versus cold transfers, and what happened on recent transfers.
keywords: transfers, handoff, departments, destinations, warm transfer, cold transfer, extension, on-call, priority, fallback, location, human talk time, escalation
---

## Departments & destinations

A **department** (Accounts, Sales, Emergencies…) is what callers ask for; a **destination** is a person or number inside it. Give each department a short description of what it handles — the assistant uses it to decide where a caller belongs when they don't name a department.

For each destination (**+ Add person**):

- **Name** and **Department**.
- **Type** – *Phone number*, *SIP address*, or *PBX extension* (needs a PBX connection on *Telephony*).
- **Number / address** – in international format for phone numbers (e.g. +447700900123).
- **Priority** – lower rings first. Two destinations with the same priority ring in turn.
- **Fallback if unanswered** – another destination to try next.
- **Hours** – when this person is available. Outside them the assistant skips them (and, when closed, follows *Studio → Closed-hours persona*).
- **On-call** – marks a destination as reachable for emergencies out of hours.
- **Location** – if you run several locations, ties the destination to one so callers to that site reach the right team.

## Settings

Transfer mode, ring timeout and urgent keywords live in *Assistant Studio → No-answer behaviour*. **Warm** transfers mean the assistant introduces the caller to your colleague first and only connects if they accept; **Cold** is a straight blind transfer.

During a warm transfer the caller is placed on silent hold while the assistant briefs your colleague privately. Your colleague accepts by **pressing any key** on their phone; if nothing is pressed within a few seconds (voicemail never presses a key, so voicemail counts as no answer) the assistant tries the next fallback destination and, if nobody accepts, returns to the caller and offers a call back — which lands on *Tickets*. If the caller hangs up while waiting, the transfer is cancelled.

## Transferred calls

Breakdown cards for the selected period: transfers by department and destination, answered versus unanswered, average time to answer, and (when *Record transferred calls* is on) team members' talk time.

## Recent transfers

Each transfer with the caller, department, who answered, outcome and duration, linking to the call detail where the recording (including the team member's track if recorded) can be played.
