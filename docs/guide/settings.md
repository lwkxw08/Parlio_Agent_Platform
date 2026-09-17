---
title: Settings
route: /settings
summary: Organisation settings — branding and white-label, custom domain, client accounts for agencies, the compliance pack, data-region pinning, and security (2FA, sessions, SSO, SCIM).
keywords: settings, organisation, branding, logo, white label, custom domain, dns, agency, client accounts, reseller, compliance pack, dpa, sub-processors, region, security, two-factor, 2fa, sessions, sso, saml, oidc, scim
---

## Branding

**Brand name**, **Logo URL**, **Icon URL**, **Primary** and **Accent colour**, **Support email** and **Support URL** change how the dashboard, emails and chat widget look for your users. Agencies use this to present Parlio under their own name.

## Custom domain

Serve the dashboard and chat widget from your own domain (e.g. `app.yourbrand.co.uk`). Add the DNS records shown (a CNAME plus the **Published TXT value** for verification) and press **Verify domain**. Certificates are issued automatically once verified.

## Client accounts

For agencies and resellers: **Add a client** creates a separate organisation under yours, with its own assistant, numbers and users. You can switch into a client to configure it, and each client sees only its own data. Billing can be per client or consolidated to you.

## Compliance pack

Downloadable Data Processing Agreement, security overview and the **Sub-processors** list (the vendors Parlio uses to run the service, with region). Under **Assistants & region pinning** you can see where each assistant's data is processed; UK-only processing is available on the Sovereign tier.

## Security

- **Two-factor authentication** – enrol your own authenticator here, and **Require 2FA for** *nobody*, *owners & admins* or *everyone*.
- **Sign-in session length** – hours before users must sign in again. **Sessions** lists active sign-ins with **Revoke**.
- **Single sign-on** – connect your **Identity provider** (Google Workspace, Microsoft Entra, Okta, or any OIDC/SAML) with the **Email domains**, **Issuer / entity ID**, **Client ID** and **Metadata URL**. Users on those domains sign in through it.
- **SCIM user provisioning** – lets your identity provider create and remove Parlio users automatically; generate the SCIM token here.
