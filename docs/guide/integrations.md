---
title: Integrations
route: /integrations
summary: Notifications (including the text-me-after-every-call summary), SMS and WhatsApp messaging, calendars and bookings, SMS appointment reminders, and connected apps such as CRMs and Zapier.
keywords: integrations, notifications, owner sms, text me, email, slack, calendar, google calendar, outlook, microsoft 365, booking link, bookings, reminders, appointment reminders, reply 1, connected apps, crm, hubspot, salesforce, pipedrive, zoho, zapier, make, webhook, google sheets, teams, servicem8, api keys, csv export, field mapping, sync log
---

The Integrations page has tabs: **Notifications**, **Messaging**, **Calendar**, and **Connected apps**.

## Text me after every call

Turn this on to receive an SMS summary after each call: who called, what they wanted, what the assistant did and whether anything needs you. Enter the **Owner's mobile** (you can add more than one) and press **Turn on summaries**. **Send a test** sends a sample immediately. Summaries use your SMS number, so each one counts towards your plan's SMS allowance.

## Who gets told, and when

Notification rules. Each rule has a **Name**, a **Channel** (email, SMS or Slack webhook), who to **Send to**, and a trigger such as *every call*, *qualified leads only*, *new ticket*, *urgent keyword*, *booking made* or *low QA score*. **Test** sends a sample; **Delete** removes the rule. Recent deliveries and any failures are listed under *Recent notifications*.

## SMS scenarios

A read-only view of the SMS scenarios configured in the Studio, with a count of how often each was sent. Edit them in *Assistant Studio → SMS scenarios*.

## Sent messages

Every SMS and WhatsApp message the platform has sent or received for your business, with delivery status. Failed messages show the carrier reason.

## Connected calendars

Calendars the assistant checks for availability and books into. Connect more than one if different people or locations have their own diary. **Disconnect** removes a calendar; existing bookings are kept.

## Booking rules

Shown under each bookable calendar. The assistant only offers and books appointments that follow these rules, so set them to match how you actually work (for example: 1-hour appointments, on the hour or half past, within working hours, finishing by close, 30 minutes travel time between jobs).

- **Standard appointment length** – how long a booking is when no service type is chosen (e.g. 60 min).
- **Gap between appointments** – time kept free before and after every booking for travel or set-up (e.g. 30 min). A slot is only offered if the gap fits around your existing events too.
- **Start times** – *On the hour*, *On the hour or half past*, *Every 15 minutes*, or *Back to back* (no fixed grid).
- **Earliest booking** – the notice a non-emergency booking needs (e.g. 2 hours, or next day onwards).
- **Book up to** – how many days ahead callers can book.
- **Book within the assistant's business hours** – ticked, bookings use the business hours set in Assistant Studio and must **finish by closing time**. Untick it to set separate booking hours per day for this calendar (untick a day to close it). Holidays set in Studio → After hours are respected.
- **Emergency services can be booked any time** – when ticked, services marked *Emergency* may be booked outside hours, off the grid and without notice. Existing bookings and the gap still apply. Untick it to make emergency services follow the same rules as everything else.

The line **In effect** summarises the rules as the assistant will apply them. Press **Save booking rules** to apply; changes take effect on the next call.

## Service types

Create the services callers can book, each with its own length: **Name** (e.g. Boiler service), **Length** in minutes, an optional **Description** that helps the assistant match what the caller asks for, and an **Emergency service** flag. Names must be unique. Use **Edit** and **Remove** to change them, then **Save booking rules**.

- With one or more service types, the assistant asks which service the caller needs, offers slots of that length and records the service on the booking (shown in the **Bookings** table).
- With none, every booking uses the standard appointment length.

## Connect

- **Google Calendar** – press Connect and sign in with the Google account whose calendar you want used. The assistant reads free/busy and creates events on it. Each event is titled with the service and customer name, and its description carries everything the person attending needs: phone number, address (also set as the event location), what the caller asked for, the number they called from and a link to the call's recording and transcript in ParlioTec.
- **Outlook / Microsoft 365** – the same flow with a Microsoft sign-in.
- **Booking link** – if you use Cal.com, Square Appointments, GoHighLevel or another booking page, choose the **Vendor**, paste the **Booking URL** and set the **Slot length**. The assistant offers to text the link rather than booking directly.
- **Demo calendar** – a built-in diary for trying bookings without connecting an account.

Google and Microsoft connections are made once by ParlioTec at platform level; you only need to sign in with your own account.

## More than one team member?

The calendar here is the tenant's **primary** calendar; the booking rules and service types above apply to every booking. With no team members listed under *Team → Team members & scheduling*, every appointment goes into this calendar. Once you add team members, callers are offered any free team member and each booking is written to **that team member's** calendar — this primary calendar only receives bookings if a team member is set to use it (leave their *Calendar ID* blank or `primary`). Add yourself as a team member if you take jobs too. Team members can share this one Google/Microsoft login by giving each their own calendar ID inside it.

## Bookings

Appointments the assistant has made, with the caller's name, phone, address, details of the job, status (confirmed, rescheduled, cancelled, no-show) and a link to the call. Statuses update automatically when a customer replies to a reminder text.

## SMS appointment reminders

Automatic texts before each booking.

- **Enabled** – switch reminders on for all bookings.
- **Offsets** – how long before the appointment to send (for example 24 hours and 1 hour). Add as many as you need.
- **Reminder text** – the message. Placeholders: `{business}`, `{when}`, `{name}`. Keep the "Reply 1 to confirm or 2 to reschedule" line so customers can respond.
- **Reply when they confirm** / **Reply when they want to reschedule** – what we text back. A reschedule request also opens a callback ticket so your team can call them.
- Customers who text STOP are opted out of all reminders; START re-enables them.

Sent reminders and the replies received are listed under *Reminders sent*.

## Sync log

Each time a booking, ticket or call is pushed to a calendar or connected app, the result is logged here. Failed syncs can be retried.

## Connected apps

CRMs, helpdesks and automation tools that receive your calls, tickets, bookings and leads:

- **Outbound webhook** – for Zapier, Make, n8n or your own systems. Each event is signed (HMAC) so you can verify it came from ParlioTec.
- **HubSpot, Salesforce, Pipedrive, Zoho** – contacts and activities are created or updated after each call.
- **Google Sheets** – one row per call.
- **Microsoft Teams** – a card in a channel for each call or ticket.
- **ServiceM8** – jobs and clients for trades businesses.

For each app: **Connect** (OAuth sign-in or API key), **Test** to send a sample record, **Field mapping** to choose which collected fields go to which CRM fields, and the sync log with **Retry**.

## Inbound API keys

Create a key to let your own systems create leads, look up calls or trigger outbound calls through the ParlioTec API. Keys are shown once — copy them when created. Revoke a key at any time.

## CSV export

Download calls, tickets or contacts for a date range as a CSV file.
