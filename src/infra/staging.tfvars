region      = "ap-south-1"
environment = "staging"
name_prefix = "agentops-staging"

vpc_cidr_block = "10.20.0.0/16"

azs = [
  "ap-south-1a",
  "ap-south-1b",
]

public_subnet_cidrs = [
  "10.20.1.0/24",
  "10.20.2.0/24",
]

tags = {
  Project = "agentops"
  Stack   = "staging"
}