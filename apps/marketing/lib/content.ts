export type Feature = { icon: string; title: string; body: string; points?: string[]; tag?: string };

export const PILLARS: Feature[] = [
  {
    icon: "☎",
    title: "Answers every call, naturally",
    body: "An intelligent AI-powered business phone system with a natural British voice that greets callers by your business name, understands what they need and gets it done — 24/7, no hold music, no menus.",
    points: ["Barge-in: callers can interrupt, just like a person", "Male or female UK voices, plus international catalogue", "Speaking-style controls: pace, postcodes, one detail at a time"],
  },
  {
    icon: "📅",
    title: "Books real appointments, by your rules",
    body: "Not a booking link. ParlioTec checks live availability and books straight into Google Calendar or Outlook, obeying the rules you set.",
    points: ["Appointment length per service type you create", "On-the-hour / half-past grid, finish-by-close, travel gap", "Emergency exception for out-of-hours urgent jobs"],
    tag: "Unique",
  },
  {
    icon: "👷",
    title: "Schedules a whole team",
    body: "Add your team members as bookable resources with skills, areas and shifts. Callers are offered any free slot across the team and the job is assigned automatically.",
    points: ["Pooled availability across every team member", "Least-loaded, round-robin, nearest or preferred assignment", "Day/week Schedule board with a lane per team member"],
    tag: "Unique",
  },
  {
    icon: "🔧",
    title: "Or books into your scheduling tool",
    body: "Already run the diary in ServiceM8 or your own job system? Switch the booking backend and ParlioTec reads availability from it and creates the job there.",
    points: ["ServiceM8 adapter included", "Generic signed-webhook contract for in-house systems", "Jobber, simPRO, Commusoft, BigChange on request"],
    tag: "Unique",
  },
  {
    icon: "🤝",
    title: "Warm transfers to real people",
    body: "When a human is needed, the assistant briefs your team member before bridging the caller — or transfers cold when speed matters. Works with your mobile, PBX or SIP trunk.",
    points: ["Departments with descriptions that steer routing", "Urgent-keyword escalation to the on-call team member", "Record the human leg too, with human talk-time analytics"],
  },
  {
    icon: "🎚️",
    title: "Humans in the loop, live",
    body: "Every AI call streams to the Live page. Your team can listen in, whisper guidance the caller never hears, or take over the conversation at any moment — then hand it back to the AI.",
    points: ["Live transcript and audio for every active call", "Whisper: steer the AI mid-call without interrupting the caller", "Take over, hand back, or approve a decision from a tap-link on your phone"],
  },
  {
    icon: "🎫",
    title: "Tickets, callbacks and follow-through",
    body: "Anything not resolved on the call becomes a ticket with SLA timers. Your team claims it, or lets the AI call the customer back with the answer in hand.",
    points: ["Compliant outbound: calling windows, daily caps, do-not-call", "Speed-to-lead call-backs for web enquiries", "SMS confirmations and reminders with reply 1/2"],
  },
  {
    icon: "💬",
    title: "Every channel in one inbox",
    body: "SMS, WhatsApp and website chat handled by the same assistant, with click-to-talk voice in the browser and a human handoff when the visitor asks.",
    points: ["Embeddable chat widget with in-page voice", "Canned replies, notes, assignment, SLA sweeps", "Threads and tickets share one lifecycle"],
  },
  {
    icon: "📈",
    title: "Analytics, trends, forecasts and an AI advisor",
    body: "In-depth analytics built for owners: when demand really arrives, what callers want, where revenue is won and lost. Ask in plain English — “missed calls after 5pm last month?” — and get the chart. The AI advisor then reads your numbers and tells you what to change and what it's worth.",
    points: [
      "Trends: hour × weekday heat-map, month/year comparisons, first-time vs returning, intents and FAQ gaps",
      "Forecasts: expected calls for the week ahead, peak concurrency, staffing guide, calls you would have missed",
      "Advisor: evidence-backed recommendations with expected impact, one-click apply and measured results",
      "Resolution, transfer quality, team utilisation, revenue, missed-revenue, attribution and cost views; weekly digest and scheduled reports",
    ],
  },
  {
    icon: "🔍",
    title: "Search every conversation",
    body: "Full-text search across transcripts and summaries, jumping straight to the moment in the recording. Every call is QA-scored, explained and shareable.",
    points: ["Find the call by what was said, not just who called", "Call explainability: why the assistant did what it did", "Live listen, whisper and take-over from the dashboard"],
  },
  {
    icon: "🏢",
    title: "Multi-location, multi-brand",
    body: "Run several sites, each with its own numbers, greetings and destinations, and roll the numbers up. Agencies get white-label branding and per-client billing.",
    points: ["After-hours personas and bank-holiday calendars", "Site-aware transfers and brand greetings", "Custom domain and logo for resellers"],
  },
  {
    icon: "🔌",
    title: "Connects to what you use",
    body: "HubSpot, Salesforce, Pipedrive, Zoho, Google Sheets, Microsoft Teams, Slack, Zapier/Make webhooks — with field mapping, retries and a sync log.",
    points: ["Contacts auto-promoted from prospect to customer", "VIP rules and caller context passed to the assistant", "CSV export of anything"],
  },
  {
    icon: "🛡",
    title: "UK-first privacy and control",
    body: "Consent announcements, PII redaction, retention policies, GDPR export/delete, audit log and a compliance pack — built in, not bolted on.",
    points: ["Call screening: block withheld numbers and spam", "TOTP 2FA, session revocation, SSO/SCIM seams", "Human approvals for sensitive actions via tap link"],
  },
  {
    icon: "🔁",
    title: "Self-improving with every call",
    body: "The more it hears, the better it gets. Every call is QA-scored; unanswered questions and hesitation moments are clustered into proposed FAQ, rule and wording fixes, replayed against real scenarios, and put to you for approval.",
    points: [
      "Insight engine turns call gaps into suggested FAQs and rules",
      "Sandbox replays drafts against real scenarios — only genuine improvements proposed",
      "Regression pack of past calls gates every publish so quality never slips",
      "You approve; the advisor measures the effect in next week's numbers",
    ],
  },
];

export type CompareRow = { label: string; us: string; answering: string; ivr: string; chatbot: string; booking: string };

// "yes" | "no" | "part" | free text
export const COMPARE: CompareRow[] = [
  { label: "Answers 24/7 in a natural British voice", us: "yes", answering: "Business hours / cost per minute", ivr: "Menus only", chatbot: "no", booking: "no" },
  { label: "Books into Google / Outlook by your rules (length, grid, travel gap)", us: "yes", answering: "Takes a message", ivr: "no", chatbot: "part", booking: "Fixed slots" },
  { label: "Tenant-created service types with their own durations", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "part" },
  { label: "Pooled availability across your whole team + auto-assignment", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "part" },
  { label: "Books into your scheduling tool (ServiceM8, webhook)", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "no" },
  { label: "Day/week schedule board with a lane per team member", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "part" },
  { label: "Warm transfer with a spoken briefing to your staff", us: "yes", answering: "part", ivr: "Cold only", chatbot: "no", booking: "no" },
  { label: "Emergency handling: on-call team member or out-of-hours booking", us: "yes", answering: "part", ivr: "no", chatbot: "no", booking: "no" },
  { label: "Calendar events carry the full job brief + link to the recording", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "part" },
  { label: "SMS, WhatsApp and web chat from the same assistant", us: "yes", answering: "no", ivr: "no", chatbot: "part", booking: "no" },
  { label: "Compliant AI call-backs with the resolution in hand", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "no" },
  { label: "In-depth analytics: trends, forecasts and an AI business advisor", us: "yes", answering: "no", ivr: "Call logs", chatbot: "no", booking: "no" },
  { label: "Self-improving: QA every call, proposes and tests its own fixes for your approval", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "no" },
  { label: "Transcript & recording search to the exact moment", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "no" },
  { label: "Live listen / whisper / take-over from the dashboard", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "no" },
  { label: "Grounded in-app help: ask how to do anything, jump to the setting", us: "yes", answering: "no", ivr: "no", chatbot: "no", booking: "no" },
  { label: "UK data handling, consent, redaction, retention, audit", us: "yes", answering: "part", ivr: "part", chatbot: "part", booking: "part" },
];

export const INDUSTRIES = [
  { id: "trades", title: "Plumbers, heating & electrical", body: "Book the boiler service at 90 minutes and the repair at 60, keep 30 minutes travel between jobs, and send genuine emergencies to the on-call team member.", pains: ["Missed calls while on the tools", "Double-booked vans", "Out-of-hours emergencies"] },
  { id: "field", title: "Field service & maintenance", body: "Pooled availability across the whole crew, assignment by area or skill, and bookings straight into ServiceM8 or your job system.", pains: ["Dispatcher bottleneck", "Uneven workloads", "Jobs logged twice"] },
  { id: "clinics", title: "Clinics, dental & practices", body: "Reception that never puts patients on hold, books by appointment type, sends reminders with reply-to-confirm, and records consent properly.", pains: ["Front-desk queues", "No-shows", "Consent & recording rules"] },
  { id: "property", title: "Estate agents & lettings", body: "Viewings booked into negotiators' calendars, maintenance issues ticketed with photos via WhatsApp, landlords called back by the AI with the update.", pains: ["Evening enquiry surge", "Repairs chasing", "Lost leads"] },
  { id: "professional", title: "Solicitors, accountants & advisers", body: "Qualify enquiries, book consultations by service type, route to the right department and keep a searchable record of every conversation.", pains: ["Reception cost", "Unqualified enquiries", "Audit trail"] },
  { id: "agencies", title: "Agencies & resellers", body: "White-label ParlioTec under your brand and domain, run each client as a tenant, and bill them your way.", pains: ["Building your own AI stack", "Per-client set-up", "Reporting to clients"] },
];

export const STEPS = [
  { title: "Tell it about your business", body: "Paste your website or answer a few questions. ParlioTec drafts the greeting, FAQs and rules; you edit in plain English in Assistant Studio." },
  { title: "Connect your diary and team", body: "Link Google or Outlook, set booking rules and service types, add team members with their skills and shifts — or point it at your scheduling tool." },
  { title: "Point your number at it", body: "Divert your existing number, take a new UK number from us, or connect your PBX / SIP trunk. Warm transfers ring your staff as usual." },
  { title: "Watch it work, then improve it", body: "Live transcripts, recordings, QA scores and the AI Advisor show what callers want and what to change. Publish updates safely with the regression pack." },
];
