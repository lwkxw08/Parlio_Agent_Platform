terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

variable "account_id" { type = string }
variable "project_name" {
  type    = string
  default = "parlio-dashboard"
}
variable "production_branch" {
  type    = string
  default = "main"
}
variable "api_url" { type = string }

# Cloudflare Pages project for apps/web; every non-production branch gets a preview URL.
resource "cloudflare_pages_project" "dashboard" {
  account_id        = var.account_id
  name              = var.project_name
  production_branch = var.production_branch

  build_config {
    build_command   = "npm run build"
    destination_dir = ".open-next/assets"
    root_dir        = "apps/web"
  }

  deployment_configs {
    preview {
      environment_variables = { NEXT_PUBLIC_API_URL = var.api_url }
      compatibility_flags   = ["nodejs_compat"]
    }
    production {
      environment_variables = { NEXT_PUBLIC_API_URL = var.api_url }
      compatibility_flags   = ["nodejs_compat"]
    }
  }
}

output "subdomain" { value = cloudflare_pages_project.dashboard.subdomain }
