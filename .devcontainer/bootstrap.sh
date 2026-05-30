#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

sudo apt-get update -qq
sudo apt-get install -y -qq \
  ca-certificates curl unzip vim make tree jq python3-pip python3-venv

# AWS CLI v2
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp/awscli
sudo /tmp/awscli/aws/install --update
rm -rf /tmp/awscliv2.zip /tmp/awscli

# OpenTofu latest(tofu commands rarely change)
curl -fsSL https://get.opentofu.org/install-opentofu.sh | sh -s -- --install-method deb

# Python tooling pinned to current stable releases
python3 -m pip install --break-system-packages -q \
  pytest==9.0.3 \
  pre-commit==4.2.0

pre-commit install --install-hooks