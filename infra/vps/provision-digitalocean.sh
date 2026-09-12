#!/usr/bin/env bash
# Create the dev VM on DigitalOcean London. Requires DIGITALOCEAN_TOKEN and an SSH public key path.
# Usage: provision-digitalocean.sh ~/.ssh/parlio.pub [size]
set -euo pipefail
PUB=$(cat "${1:?ssh public key path}"); SIZE=${2:-s-2vcpu-4gb}
API=https://api.digitalocean.com/v2; H=(-H "Authorization: Bearer $DIGITALOCEAN_TOKEN" -H "Content-Type: application/json")
KEY=$(curl -fsS -X POST $API/account/keys "${H[@]}" -d "$(jq -n --arg k "$PUB" '{name:"parlio-devin",public_key:$k}')" | jq .ssh_key.id)
USERDATA=$(cat <<'CI'
#cloud-config
package_update: true
packages: [ca-certificates, curl, git, gettext-base]
runcmd:
  - curl -fsSL https://get.docker.com | sh
  - systemctl enable --now docker
CI
)
ID=$(curl -fsS -X POST $API/droplets "${H[@]}" -d "$(jq -n --arg u "$USERDATA" --arg s "$SIZE" --argjson k "$KEY" \
  '{name:"parlio-dev-lon1",region:"lon1",size:$s,image:"ubuntu-24-04-x64",ssh_keys:[$k],tags:["parlio","dev"],monitoring:true,user_data:$u}')" | jq .droplet.id)
rule() { jq -n --arg p "$1" --arg r "$2" '{protocol:$p,ports:$r,sources:{addresses:["0.0.0.0/0","::/0"]}}'; }
RULES=$(jq -s . <(rule tcp 22) <(rule tcp 80) <(rule tcp 443) <(rule tcp 7881) <(rule udp 7882) \
  <(rule udp 5060) <(rule tcp 5060) <(rule udp 10000-10500))
curl -fsS -X POST $API/firewalls "${H[@]}" -d "$(jq -n --argjson r "$RULES" --argjson id "$ID" \
  '{name:"parlio-dev",droplet_ids:[$id],inbound_rules:$r,outbound_rules:[{protocol:"tcp",ports:"all",destinations:{addresses:["0.0.0.0/0","::/0"]}},{protocol:"udp",ports:"all",destinations:{addresses:["0.0.0.0/0","::/0"]}}]}')" >/dev/null
sleep 30
curl -fsS $API/droplets/$ID "${H[@]}" | jq -r '.droplet.networks.v4[] | select(.type=="public") | .ip_address'
