# Attribute names follow the official team-telnyx/telnyx provider; validate with `terraform validate`
# once the provider is initialised (needs TELNYX_API_KEY).
terraform {
  required_providers {
    telnyx = {
      source  = "team-telnyx/telnyx"
      version = "~> 0.1"
    }
  }
}

variable "env" { type = string }
variable "sip_edge_fqdn" { type = string } # e.g. sip.dev.parlio.example -> LiveKit SIP bridge
variable "numbers_to_order" {
  type    = number
  default = 1
}

# Telnyx FQDN connection: inbound INVITEs go to our SIP edge; outbound uses credential auth.
resource "telnyx_fqdn_connection" "parlio" {
  connection_name = "parlio-${var.env}"
  inbound = {
    ani_number_format          = "+E.164"
    dnis_number_format         = "+e164"
    codecs                     = ["OPUS", "G722", "PCMA", "PCMU"]
    sip_region                 = "Europe"
    sip_subdomain              = "parlio-${var.env}"
    sip_subdomain_receive_settings = "only_my_connections"
  }
  outbound = {
    localization = "GB"
  }
}

resource "telnyx_fqdn" "sip_edge" {
  connection_id = telnyx_fqdn_connection.parlio.id
  fqdn          = var.sip_edge_fqdn
  dns_record_type = "a"
  port          = 5060
}

resource "telnyx_messaging_profile" "parlio" {
  name    = "parlio-${var.env}"
  enabled = true
}

output "connection_id"       { value = telnyx_fqdn_connection.parlio.id }
output "messaging_profile_id" { value = telnyx_messaging_profile.parlio.id }
