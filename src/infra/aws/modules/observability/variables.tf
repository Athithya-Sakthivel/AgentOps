variable "name_prefix" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "retention_in_days" {
  type    = number
  default = 7
}

variable "alarm_sns_topic_arn" {
  type    = string
  default = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}