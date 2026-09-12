# Parlio infrastructure (Terraform)

One root module per environment under `envs/`; shared modules under `modules/`. Environments are
identical apart from `terraform.tfvars` (sizing, domains) and secrets (injected via CI/`TF_VAR_*`).

| Env        | Cloud (default)           | Purpose                                             |
|------------|---------------------------|-----------------------------------------------------|
| dev        | Hetzner / single VPS k3s  | Always-on test number + PR previews of the dashboard|
| staging    | AWS eu-west-2 (London)    | Production replica, staging Telnyx numbers          |
| production | AWS eu-west-2 (London)    | Live tenants; sovereign-uk-strict adds UK-only nodes|

Modules:

- `network`    VPC/subnets/security groups (SIP 5060/5061, RTP range, WebRTC range)
- `k8s`        EKS (staging/prod) or k3s on a VPS (dev), node groups incl. optional isolated pool
- `data`       Postgres (RDS or Supabase project), Redis, S3/R2 bucket for recordings (UK region)
- `livekit`    Helm release of `infra/helm/parlio` with LiveKit cluster mode + SIP bridge
- `telephony`  Telnyx: SIP connection (FQDN -> SIP edge), numbers, messaging profile; secondary
               carrier block (Twilio/Gamma) is a drop-in for failover
- `dashboard`  Cloudflare Pages project for `apps/web` (preview deployments per branch)

```sh
cd envs/dev && terraform init && terraform plan
```
