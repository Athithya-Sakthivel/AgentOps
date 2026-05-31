# ----------------------------------------------------------------------
# VARIABLES
# ----------------------------------------------------------------------
variable "name_prefix" {
  description = "Prefix to use for naming all VPC resources"
  type        = string
  # Example: "agentops-staging"
}

variable "vpc_cidr_block" {
  description = "CIDR block for the VPC"
  type        = string
}

variable "azs" {
  description = "List of Availability Zones to create subnets in (e.g. ['ap-south-1a', 'ap-south-1b'])"
  type        = list(string)
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets, one per Availability Zone"
  type        = list(string)
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (e.g. for RDS), one per Availability Zone"
  type        = list(string)
  default     = [] # No private subnets if not provided
}

variable "tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# ----------------------------------------------------------------------
# VPC
# ----------------------------------------------------------------------
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr_block
  enable_dns_support   = true # Enables DNS resolution inside the VPC[reference:0]
  enable_dns_hostnames = true # Allows instances to receive DNS hostnames[reference:1]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-vpc"
  })
}

# ----------------------------------------------------------------------
# INTERNET GATEWAY
# ----------------------------------------------------------------------
resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-igw"
  })
}
# ----------------------------------------------------------------------
# PUBLIC ROUTING
# ----------------------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public-rt"
  })
}

resource "aws_route" "public_internet_access" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

# ----------------------------------------------------------------------
# PUBLIC SUBNETS & ASSOCIATIONS
# ----------------------------------------------------------------------
resource "aws_subnet" "public" {
  count = length(var.public_subnet_cidrs)

  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.azs[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public-${var.azs[count.index]}"
    Type = "public"
  })
}

resource "aws_route_table_association" "public" {
  count = length(var.public_subnet_cidrs)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ----------------------------------------------------------------------
# PRIVATE SUBNETS (if any CIDRs are provided)
# ----------------------------------------------------------------------
resource "aws_subnet" "private" {
  count = length(var.private_subnet_cidrs)

  vpc_id            = aws_vpc.this.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.azs[count.index]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-private-${var.azs[count.index]}"
    Type = "private"
  })
}