module "vpc" {
  source = "./modules/vpc"

  name_prefix         = var.name_prefix
  vpc_cidr_block      = var.vpc_cidr_block
  azs                 = var.azs
  public_subnet_cidrs = var.public_subnet_cidrs
}

module "security_groups" {
  source = "./modules/security-groups"

  name_prefix = var.name_prefix
  vpc_id      = module.vpc.vpc_id
}