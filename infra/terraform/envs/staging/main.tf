terraform {
  required_version = ">= 1.6"
  backend "s3" {} # configured per env via -backend-config
}

locals {
  env = "staging"
}

module "network"   { source = "../../modules/network"   env = local.env }
module "k8s"       { source = "../../modules/k8s"       env = local.env }
module "data"      { source = "../../modules/data"      env = local.env }
module "livekit"   { source = "../../modules/livekit"   env = local.env }

module "telephony" {
  source        = "../../modules/telephony"
  env           = local.env
  sip_edge_fqdn = var.sip_edge_fqdn
}

module "dashboard" {
  source     = "../../modules/dashboard"
  account_id = var.cloudflare_account_id
  api_url    = var.api_url
  production_branch = "staging"
  project_name      = "parlio-dashboard-staging"
}

variable "sip_edge_fqdn"         { type = string }
variable "cloudflare_account_id" { type = string }
variable "api_url"               { type = string }
