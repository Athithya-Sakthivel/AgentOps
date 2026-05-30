region      = "ap-south-1"
environment = "prod"
name_prefix = "agentops-prod"

vpc_cidr_block = "10.30.0.0/16"

azs = [
  "ap-south-1a",
  "ap-south-1b",
]

public_subnet_cidrs = [
  "10.30.1.0/24",
  "10.30.2.0/24",
]

tags = {
  Project = "agentops"
  Stack   = "prod"
}