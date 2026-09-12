# network module - populated in Phase 6 (Kubernetes/Terraform hardening). Interface fixed now so env
# roots don't change when the implementation lands.
variable "env" { type = string }
variable "region" {
  type    = string
  default = "eu-west-2"
}
