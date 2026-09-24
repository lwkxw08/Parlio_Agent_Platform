---
title: Assistant Studio
route: /assistant
summary: Everything about how your AI receptionist speaks, what it knows, what it collects and how it behaves on every call.
keywords: assistant, studio, greeting, persona, voice, tone, faqs, rules, publish, screening, anonymous, withheld, spam, blocked, after hours, holidays, recording, consent, languages, sms
---

The Studio is where you shape your assistant. Nothing you change takes effect on live calls until you press **Publish** (top right). **Save draft** keeps your work without changing what callers hear. Every publish creates a new version you can restore later (see *Version history*).

If you have more than one assistant (Growth and Scale plans), switch between them with the picker at the top. **New assistant** clones an existing one so you only change what differs.

## Identity & personality

- **Assistant name** – how the assistant introduces itself ("Hi, this is Alex from…").
- **Business name (as spoken)** – the name the assistant says out loud. Write it the way it should be pronounced (for example "A B C Plumbing" rather than "ABC").
- **Tone** and **Formality** – friendly/neutral/professional and casual/standard/formal. These shape every sentence the assistant produces.
- **Pace** and **Speaking speed** – how quickly it speaks. Slower is safer for older customers or when reading back numbers.
- **Website / Phone / Email / Address** – facts the assistant can quote when asked. Leave a field blank if you would rather it didn't share that detail.
- **Timezone** – used for business hours, holidays and the times it quotes for bookings.

## Voice

Pick a voice from the catalogue. Voices are grouped by accent (British first for UK businesses, American, Irish, Australian…) with a preview button on each. The voice engine itself (Cartesia, ElevenLabs) is chosen by ParlioTec, not per business, so you only ever choose the voice. Owner voice cloning is on the *Quality & simulate* page.

## Speaking style

Controls how the assistant reads details back so nothing is misheard:

- **Check phrase after each detail** – what it says after capturing a name, number or postcode ("Let me read that back…").
- **Final confirmation question** – asked once everything is collected.
- **Your own read-back rules** – one per line, e.g. "Read postcodes as two groups" or "Say phone numbers in pairs".

## Interruptions

**Let callers interrupt the assistant** is on by default: the assistant stops talking the moment the caller speaks. Turn it off for noisy lines or callers who talk over announcements – the assistant then finishes each sentence before listening.

## Glossary & pronunciations

A list of words the assistant should know: brand names, product codes, staff names, local place names. For each term you can give **Say as** (how it should be pronounced, e.g. "Saoirse" → "Seer-sha") and **Meaning** (what it is, e.g. "our premium boiler-care plan"). The pronunciation is applied to everything the assistant says; the meaning is added to its instructions so it understands the term when callers use it.

## Business hours

Set the opening times for each day. Inside these hours the assistant uses the main greeting and transfers to your team; outside them the *Closed-hours persona* applies. **Holidays & closures** lets you add dates (Christmas Day, a training day) that are treated as closed even on a normal weekday.

## Key business rules

Plain-English instructions the assistant must always follow, one per line. Good rules are specific: "Never quote prices over the phone", "Always ask for the postcode before offering a visit", "We do not service commercial boilers". Use **Ask AI to draft** to turn a short brief (or your website) into a first set of rules.

## FAQs

Question-and-answer pairs the assistant can answer directly. Group them with a category so the list stays manageable. **Suggest from recent calls** shows questions callers actually asked that the assistant couldn't answer, ready to add. **Import FAQs** accepts pasted text, a web page URL, a CSV or an uploaded document (PDF, Word .docx, text or Markdown, up to 10 MB – a brochure, price list or FAQ sheet) and drafts FAQs from it. Nothing is added until you review the suggestions and click **Apply**; duplicates of existing FAQs are flagged.

## Live website search

When enabled (and your **Website** is set under Identity & personality), the assistant can look up an answer on your own website during the call – opening hours, prices, service areas – and reads the relevant snippet back. It only ever reads pages on your website's domain (plus any **Extra pages** you list on that domain, up to the page limit you set); it never browses the wider web, and if nothing on the site matches it says so rather than guessing.

## Information to collect

The fields the assistant must capture on every call (name, phone, address, issue…). Mark a field *required* and the assistant will not end the call without it, unless the caller refuses. Collected fields appear on the call record, the ticket and in CRM syncs.

## SMS scenarios

Texts the assistant can offer to send during a call — a booking link, directions, your opening hours, a price list. Each scenario has a trigger (when to offer it) and the message text. Requires an SMS-capable number on the *Billing* page. Messages sent appear under *Integrations → Messaging*.

## Languages

**Primary language** is what the assistant speaks by default. Add additional languages and it will switch when a caller speaks one of them; the recording consent announcement is played in the caller's language where available.

## Recording & consent

- **Record calls** – stores a recording you can play from the call detail page. UK GDPR/PECR requires callers to be told, so the **Consent announcement** is played once at the start of each recorded call.
- **Record transferred calls** – also records the team member's side of a warm transfer as its own track, and shows their talk time on the *Transfers* page.
- Recordings follow your retention policy on the *Compliance* page.

## Call screening

Screening asks unknown callers who they are and why they are calling before the assistant helps. Sales pitches, robocalls and silent lines are ended politely.

- **Screen** – *Nobody* (default), *Unknown callers only* (no contact record) or *Every caller*.
- **Reject numbers on your spam list and the platform-wide spam list** – ends known spam calls before answering.
- **Reject withheld / anonymous caller IDs** – tick this to block callers who withhold their number. It is off by default because many genuine customers withhold their number; if you turn it on, consider adding regulars to *Always allow*.
- **Always allow** – numbers that are never screened or blocked.

Screened and rejected calls still appear on the *Calls* page with a "Screened" or "Blocked" label so you can check nothing genuine was lost.

## Blocked numbers

Numbers listed here are ended immediately without answering and logged as blocked. Use international format (e.g. +447700900123). For blocking anonymous callers use *Call screening* instead.

## Closed-hours persona

What callers get outside business hours and on holidays.

- **After-hours greeting** and **Holiday greeting** – the holiday one falls back to the after-hours greeting if blank.
- **Tone in this window** – you may want a shorter, more formal message at night.
- **Transfers when closed** – *Emergencies only* (to on-call destinations), *Never transfer, always take a message*, or *Same as daytime*.

## No-answer behaviour

What happens when a transfer rings out or nobody is available: **Take a message as a ticket** (it appears in the callback queue on *Tickets*), **Offer voicemail**, or **Both**. Also here: **Transfer mode** (*Warm* briefs the human first; *Cold* is a blind transfer), the **Ring timeout**, and **Urgent keywords** — words such as "flood" or "no heating" that trigger immediate escalation to on-call staff regardless of the time.

## Version history

Every publish is kept. **Restore** puts an earlier version back as the live one (it becomes a new version, so nothing is lost).

## Publish checks

Before publishing, ParlioTec replays your regression pack (saved test conversations from *Quality & simulate*). If the pass rate would drop you see "Not published — the regression pack found a problem" with the failing scenarios; you can **Keep editing** or **Publish anyway**.
