variable "cluster_name" {
  type = string
}

variable "instance_type" {
  type    = string
  default = "t4g.small"
}

variable "min_size" {
  type    = number
  default = 2
}

variable "max_size" {
  type    = number
  default = 2
}

variable "desired_capacity" {
  type    = number
  default = 2
}

variable "security_group_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "ecs_instance_profile_name" {
  type = string
}

variable "cloudflare_tunnel_token" {
  type      = string
  sensitive = true
}

variable "cloudflare_hostname" {
  type    = string
  default = "athithya.site"
}

variable "tags" {
  type    = map(string)
  default = {}
}