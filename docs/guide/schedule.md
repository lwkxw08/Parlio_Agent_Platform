---
title: Schedule
route: /schedule
summary: The team's diary for a day or week — one lane per engineer with bookings, shifts, travel gaps and other calendar events; reassign, move or cancel bookings.
keywords: schedule, diary, calendar view, day view, week view, lanes, engineer schedule, utilisation, reassign booking, reschedule, move booking, cancel booking, travel gap, shifts, read-only
---

## Day and week views

**Day** shows a horizontal timeline with one lane per engineer (a single "Bookings" lane if you have no engineers). **Week** shows seven columns per engineer. Use **‹ ›**, the date picker or **Today** to move; **Refresh** re-reads every calendar (the view is cached for 30 seconds otherwise).

In each lane: shaded areas are the engineer's **shift**, hatched blocks are **other events already in their calendar** (holidays, meetings), thin grey blocks are the **travel gap** from the booking rules, and coloured blocks are **bookings** showing time, customer, service and postcode area. The lane header shows the role and **% booked** of the shift. A "Calendar error" label means that engineer's calendar could not be read — check *Integrations → Calendar*.

## Filters

Narrow the board by **Location**, **Service** or **Engineer**. Filters apply to both views.

## Booking details and actions

Click a booking to see who, when, service, status, assignee, phone, address, notes and a link to the call recording. Owners, admins and agents can:

- **Reassign** to another engineer — checked for a clash first; the event moves to their calendar.
- **Move** to a new date/time — booking rules (hours, grid, notice, gap) and clashes are checked.
- **Cancel booking** — removes the calendar event and stops reminder texts.

When bookings are made in your **scheduling tool**, the board is **read-only** and shows the tool's diary; make changes in the tool and they flow back through its events.

## Mobile

On a phone the day view scrolls sideways; the week view is best on a tablet or desktop.
