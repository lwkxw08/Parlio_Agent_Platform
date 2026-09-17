---
title: Team
route: /team
summary: Invite colleagues and set their role; add the engineers who can be booked, choose how bookings are shared out, and connect your own scheduling tool.
keywords: team, members, invite, roles, owner, admin, agent, viewer, permissions, remove user, engineers, resources, rota, shifts, skills, postcode areas, on call, assignment, least loaded, round robin, nearest, preferred engineer, scheduling tool, ServiceM8, webhook, pooled availability, multiple calendars
---

## Members

Everyone with access to your organisation, their role and last sign-in. Change a role or **Remove** access from the row.

## Invite a teammate

Enter their **Email**, optional **Name** and a **Role**, then **Send invitation** — they get an email link that expires in 7 days.

Roles:

- **Owner** – everything, including billing and deleting the organisation.
- **Admin** – configure the assistant, integrations, telephony and team, but not billing.
- **Agent** – work the Inbox, Tickets and Live pages, view Calls; cannot change configuration.
- **Viewer** – read-only access to calls and analytics.

Two-factor requirements and SSO for the whole team are set under *Settings → Security*.

## Engineers & resources

*Team → Engineers & scheduling tab.* List everyone who can be booked — engineers, technicians, rooms, vans. With **no engineers listed**, the assistant books into the primary calendar exactly as before. Once you add engineers, availability offered to callers is **pooled**: a time is offered when *anyone* who can do that service is free, the caller never has to pick a person, and each booking is assigned to one engineer and written to **their** calendar.

For each engineer (**Add engineer** / **Edit**):

- **Name** and **Role** (Engineer, Plumber, Electrician…).
- **Skills / services** – which service types they can do (matches the service names under *Integrations → Calendar → Service types*). Leave empty for any service.
- **Postcode areas covered** – outward codes such as `M1, M2, SK, WA`. The caller's address is matched to these; leave empty for anywhere.
- **Location** – tie the engineer to one of your *Locations*.
- **Calendar connection** and **Calendar ID** – the Google/Outlook connection to book into, and which calendar within it (`primary`, or a shared calendar's ID so one Workspace/365 login can hold one calendar per engineer).
- **Own working hours** – per-day shifts; untick to use the booking rules' hours. Bookings must start and finish within the shift.
- **On call** – emergency services go to this engineer first, outside hours where the booking rules allow it.
- **Active** – untick (or **Deactivate** in the row) to stop offering their time without deleting them; existing bookings keep the name.

## How bookings are assigned

- **Assignment policy**: **Least loaded that day** (default — fewest jobs on the day), **Round robin** (turns in order), **Nearest area** (postcode areas matching the caller), **Preferred engineer** (returning customers get who they had last time when free).
- **Bookings are made in**: **Calendars** (one event per booking on the assigned engineer's calendar) or **Our scheduling tool** (see below).
- **Emergency services go to the on-call engineer** when one is free.

Reassign, move or cancel a booking from the *Schedule* page; the calendar event moves with it. Reminder texts can include the engineer with `{engineer}` in the template.

## Scheduling tool

If your diary lives in a job-management system, connect it here and switch **Bookings are made in** to the scheduling tool. The assistant then reads availability from the tool and creates jobs in it; the Schedule page shows that diary read-only.

- **ServiceM8** – paste your API key. Staff can be imported as engineers with **Import staff as engineers**; jobs are created with the customer, phone, address, notes and the call link.
- **Your own scheduling system (webhook contract)** – give a **Base URL** and a **Shared secret**; every request is signed with HMAC-SHA256 (`X-Parlio-Timestamp`, `X-Parlio-Signature`). The contract (resources, shifts, availability, jobs, events) is shown under *Webhook contract for your developers* on the page. Your system posts cancellations and moves back to `/v1/public/scheduler/events/{your tenant}`.
- **Test connection** checks credentials; **Schedule page is read-only** keeps edits in the tool. Jobber, simPRO, Commusoft, BigChange and Joblogic can be added on request.
