#!/usr/bin/env bash

export DEBIAN_FRONTEND=noninteractive

sudo apt-get update -qq && sudo apt-get install -y -qq ca-certificates curl unzip vim make tree jq python3-pip python3-venv

# OpenTofu
curl -fsSL https://get.opentofu.org/install-opentofu.sh | sh -s -- --install-method deb

# AWS CLI v2
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp/awscli && sudo /tmp/awscli/aws/install --update
rm -rf /tmp/awscliv2.zip /tmp/awscli

pip install --break-system-packages --upgrade pre-commit boto3 -q && pre-commit install --install-hooks

