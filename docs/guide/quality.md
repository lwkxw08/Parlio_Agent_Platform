---
title: Quality & simulate
route: /quality
summary: Score every call, spot questions the assistant couldn't answer, test changes with scripted callers before publishing, keep a regression pack, let the assistant propose its own fixes, and clone your voice.
keywords: quality, qa score, scoring, alerts, suggested faqs, simulate, test callers, scenarios, a/b, regression pack, publish checks, auto-improve, voice cloning
---

## Recent scores

Each call gets a QA score (0–100) shortly after it ends, from signals like: did it collect the required fields, did it answer accurately, did it interrupt, did it hand off when it should. **Rescore** re-runs it. Click a call to see the reasons.

## Scoring & alerts

**Alert when overall score is below** a threshold triggers the "low QA score" notification rule so you can review that call. **Minimum caller turns to score** avoids scoring very short calls (wrong numbers, hang-ups).

## Suggested FAQs & rules

Questions callers asked that the assistant couldn't answer, clustered, with a drafted answer. **Apply to assistant** adds it to the Studio draft; **Dismiss** hides it. **Rebuild from recent calls** refreshes the list.

## Scripted test callers

Try a change before your customers do. Create a scenario: a **Persona**, a **Goal**, the **Caller lines** in order, what the **Reply must mention** / **must avoid**, whether a **Handoff to a human** is expected, and a **Minimum QA score**. Run it against the live config or paste **Draft instructions** to test a change — add **Variant B** to compare two wordings side by side. The results show every reply and which checks passed.

## Regression pack

**Keep failures as regression tests** saves a failing scenario (or a flagged real call) as a permanent test. The pack runs on every Studio publish; if the pass rate would drop, publish is blocked until you confirm (*Publish checks*).

## Auto-improve

After a batch of scores or simulations, ParlioTec drafts FAQ or rule changes that would fix the failures, re-runs the failing scenarios against the draft, and proposes only changes that make things better. **Approve & publish** applies them; **Reject** discards.

## Owner voice cloning

Upload a 30–90 second clean recording of your own voice (with your consent recorded) and **Use for assistant** to have it answer in your voice. Available on Scale and above.
