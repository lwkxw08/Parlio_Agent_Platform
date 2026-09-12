# Single-VM deployment

Runs the full voice stack (Caddy TLS, LiveKit, LiveKit SIP, Redis, Postgres, MinIO, Core API,
voice worker) on one Ubuntu box with Docker. Used for the dev/staging test number; production
moves to Kubernetes (`infra/helm`, `infra/terraform`). Provider-agnostic — the only cloud-specific
step is creating the VM (`provision-digitalocean.sh`).

```sh
# on the server
git clone https://github.com/lwkxw08/Parlio_Agent_Platform.git /opt/parlio && cd /opt/parlio/infra/vps
cp .env.example .env && $EDITOR .env      # PUBLIC_HOST, DASHBOARD_URL, vendor keys
./deploy.sh
```

Endpoints: `https://api.<PUBLIC_HOST>/healthz`, `wss://lk.<PUBLIC_HOST>`, SIP `sip:<ip>:5060`.
Firewall: 22, 80, 443, 7881/tcp, 7882/udp, 5060/udp+tcp, 10000-10500/udp.
Then follow `infra/local/sip/README.md` to create the inbound trunk + dispatch rule with `lk`.
Moving providers: new VM → same script → `pg_dump` restore → update `PUBLIC_HOST`/DNS.
