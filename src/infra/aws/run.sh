#!/usr/bin/env bash
# src/infra/aws/run.sh
# Production-ready, idempotent wrapper to manage OpenTofu (tofu) lifecycle:
#  - --plan      : init backend, fmt/validate (auto-fix), produce a plan file (dry-run)
#  - --create    : init backend, fmt/validate (auto-fix), plan, then apply -auto-approve
#  - --destroy   : init backend, then destroy (requires --yes-delete)
#  - --validate  : init backend and validate backend / prereqs
#  - --find-version / --rollback-state <versionId> : state management helpers.
#  --env staging --rollback-state mJy09P8lI1XBnjVKjgtHja_rNDhZUOMF --yes-delete
#
# Usage:
#   bash src/infra/aws/run.sh --plan  --env staging
#   bash src/infra/aws/run.sh --create --env staging
#   bash src/infra/aws/run.sh --destroy --env staging --yes-delete
#   bash src/infra/aws/run.sh --env staging --find-version
#
# Notes / invariants:
#  - AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION are used (fallback ap-south-1).
#  - Script does NOT commit formatted changes to git; it only auto-formats files in-place.
#  - State bucket is versioned (ENABLED) and encrypted (AES256).
#  - State locking is done natively via S3 using `use_lockfile=true` (no DynamoDB required).
#  - Script exits non-zero on any infrastructure mutation failure.

set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
STACK_DIR="$SCRIPT_DIR"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../../../../" && pwd -P)"

# ----------------------------------------------------------------------
# Environment variable defaults (can be overridden before calling script)
# ----------------------------------------------------------------------
export TF_VAR_region="${TF_VAR_region:-ap-south-1}"
export TF_VAR_environment="${TF_VAR_environment:-staging}"
export TF_VAR_name_prefix="${TF_VAR_name_prefix:-agentops-staging}"

# ---- VPC ----
export TF_VAR_vpc_cidr_block="${TF_VAR_vpc_cidr_block:-10.20.0.0/16}"
export TF_VAR_azs="${TF_VAR_azs:-[\"ap-south-1a\",\"ap-south-1b\"]}"
export TF_VAR_public_subnet_cidrs="${TF_VAR_public_subnet_cidrs:-[\"10.20.1.0/24\",\"10.20.2.0/24\"]}"
export TF_VAR_private_subnet_cidrs="${TF_VAR_private_subnet_cidrs:-[\"10.20.11.0/24\",\"10.20.12.0/24\"]}"

# ---- Tags ----
export TF_VAR_tags="${TF_VAR_tags:-{\"Project\":\"agentops\",\"Stack\":\"staging\"}}"

# ---- S3 & ECR ----
export TF_VAR_bucket_name="${TF_VAR_bucket_name:-agentops-staging-embeddings-bucket}"
export TF_VAR_force_destroy="${TF_VAR_force_destroy:-true}"
export TF_VAR_agent_repository_name="${TF_VAR_agent_repository_name:-agentops-staging-agent-service}"
export TF_VAR_mcp_repository_name="${TF_VAR_mcp_repository_name:-agentops-staging-mcp-server}"

# ---- GitHub CI/CD ----
export TF_VAR_github_repository="${TF_VAR_github_repository:-Athithya-Sakthivel/AgentOps}"

# ---- Cloudflare (sensitive – override with real token) ----
# Try to fetch token from cloudflare module output, but allow override
if [ -z "${TF_VAR_cloudflare_tunnel_token:-}" ]; then
  export TF_VAR_cloudflare_tunnel_token="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_token 2>/dev/null || echo "")"
fi

# ---- RDS (disabled in staging to save cost) ----
export TF_VAR_create_rds="${TF_VAR_create_rds:-false}"
export TF_VAR_db_password="${TF_VAR_db_password:-}"                     # leave empty – Terraform generates random password

# ---- Budget & Alerts ----
export TF_VAR_monthly_budget_amount="${TF_VAR_monthly_budget_amount:-100}"
export TF_VAR_alert_emails="${TF_VAR_alert_emails:-[\"athithya651@gmail.com\"]}"
export TF_VAR_alarm_sns_topic_arn="${TF_VAR_alarm_sns_topic_arn:-}"    # optional – set if you have an SNS topic

# ---- ECS Flag (deploy ECS cluster and services) ----
export TF_VAR_enable_ecs="${TF_VAR_enable_ecs:-true}"

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
usage() {
  cat <<USAGE >&2
Usage:
  $(basename "$0") --plan|--create|--destroy|--validate|--find-version|--rollback-state <versionId> --env <prod|staging> [--yes-delete]

Modes:
  --plan             : init backend, fmt/validate, create plan file
  --create           : init backend, fmt/validate, plan, apply -auto-approve
  --destroy|--delete : init backend, destroy -auto-approve
  --validate         : validate backend and prerequisites
  --find-version     : list remote state object versions
  --rollback-state   : restore specified state object version

Flags:
  --env <prod|staging>
  --yes-delete

Notes:
  Requires aws, tofu, python3 in PATH.
  Uses TF_VAR_* environment variables exclusively (no .tfvars files).
USAGE
  exit 2
}

if [ $# -lt 1 ]; then
  usage
fi

MODE=""
ENVIRONMENT=""
YES_DELETE=false
ROLLBACK_VERSION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --plan|--create|--destroy|--delete|--validate|--find-version)
      if [ -n "$MODE" ]; then
        echo "ERROR: only one mode may be specified" >&2
        usage
      fi
      MODE="$1"
      [ "$MODE" = "--delete" ] && MODE="--destroy"
      shift
      ;;
    --rollback-state)
      if [ -n "$MODE" ]; then
        echo "ERROR: only one mode may be specified" >&2
        usage
      fi
      MODE="--rollback-state"
      shift
      if [ $# -eq 0 ]; then
        echo "ERROR: --rollback-state requires <versionId>" >&2
        usage
      fi
      ROLLBACK_VERSION="$1"
      shift
      ;;
    --env)
      shift
      if [ $# -eq 0 ]; then
        echo "ERROR: --env requires prod or staging" >&2
        usage
      fi
      case "$1" in
        prod|staging)
          ENVIRONMENT="$1"
          shift
          ;;
        *)
          echo "ERROR: invalid environment: $1" >&2
          usage
          ;;
      esac
      ;;
    --yes-delete)
      YES_DELETE=true
      shift
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage
      ;;
  esac
done

if [ -z "$MODE" ] || [ -z "$ENVIRONMENT" ]; then
  usage
fi

for cmd in aws tofu python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "ERROR: required command not found: $cmd" >&2
    exit 10
  }
done

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

retry() {
  local tries=${1:-6}
  local delay=${2:-1}
  shift 2
  local i=0 rc=0
  while [ "$i" -lt "$tries" ]; do
    set +e
    "$@"
    rc=$?
    set -e
    [ "$rc" -eq 0 ] && return 0
    i=$((i + 1))
    sleep "$delay"
    delay=$((delay * 2))
  done
  return "$rc"
}

PLAN_DIR="${STACK_DIR}/.plans"
mkdir -p "$PLAN_DIR"

PLAN_FILE="${PLAN_DIR}/${ENVIRONMENT}.tfplan"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || true)"
if [ -z "$ACCOUNT_ID" ] || [ "$ACCOUNT_ID" = "None" ]; then
  echo "ERROR: unable to determine AWS account id" >&2
  exit 20
fi

STATE_BUCKET="agentops-opentofu-state-${ACCOUNT_ID}-xyz"
STATE_KEY="${ENVIRONMENT}/terraform.tfstate"

exec_and_log() {
  local label="$1"
  shift
  log "CMD START: ${label}: $*"
  set +e
  "$@"
  local rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    echo "ERROR: command failed: ${label} (rc=${rc})" >&2
    exit "$rc"
  fi
  log "CMD OK: ${label}"
}

ensure_bucket_exists_and_versioning() {
  local bucket="$1"
  local region="$2"

  if aws s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
    log "s3: bucket ${bucket} exists"
  else
    log "s3: creating bucket ${bucket} (region=${region})"
    if [ "$region" = "us-east-1" ]; then
      exec_and_log "s3-create-bucket" aws s3api create-bucket --bucket "$bucket"
    else
      exec_and_log "s3-create-bucket" aws s3api create-bucket --bucket "$bucket" --create-bucket-configuration LocationConstraint="$region"
    fi
    retry 6 2 aws s3api head-bucket --bucket "$bucket"
    log "s3: created bucket ${bucket}"
  fi

  exec_and_log "s3-put-versioning" aws s3api put-bucket-versioning --bucket "$bucket" --versioning-configuration Status=Enabled

  set +e
  aws s3api put-bucket-encryption --bucket "$bucket" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null 2>&1
  set -e

  set +e
  aws s3api put-public-access-block --bucket "$bucket" \
    --public-access-block-configuration '{"BlockPublicAcls":true,"IgnorePublicAcls":true,"BlockPublicPolicy":true,"RestrictPublicBuckets":true}' >/dev/null 2>&1
  set -e
}

validate_backend() {
  local bucket="$1"
  local key="$2"
  local region="$3"

  aws sts get-caller-identity --query Account --output text >/dev/null

  if ! aws s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
    echo "ERROR: state bucket ${bucket} not found" >&2
    return 1
  fi

  local vs
  vs="$(aws s3api get-bucket-versioning --bucket "$bucket" --query Status --output text 2>/dev/null || true)"
  if [ "$vs" != "Enabled" ]; then
    echo "ERROR: bucket ${bucket} versioning not Enabled (status=${vs})" >&2
    return 2
  fi

  if ! aws s3api get-bucket-encryption --bucket "$bucket" >/dev/null 2>&1; then
    echo "ERROR: bucket ${bucket} encryption not configured" >&2
    return 3
  fi

  exec_and_log "tofu-init-validate" bash -c "cd \"$STACK_DIR\" && tofu init -backend-config \"bucket=${bucket}\" -backend-config \"key=${key}\" -backend-config \"region=${region}\" -backend-config \"use_lockfile=true\" -input=false"
  log "Validation OK"
}

fmt_auto_fix_if_needed() {
  if (cd "$STACK_DIR" && tofu fmt -check -recursive); then
    log "Formatting OK"
    return 0
  fi

  (cd "$STACK_DIR" && tofu fmt -recursive)

  if (cd "$STACK_DIR" && tofu fmt -check -recursive); then
    log "Formatting fixed"
  else
    echo "ERROR: formatting still failing after auto-fix" >&2
    exit 30
  fi
}

validate_config() {
  exec_and_log "tofu-validate" bash -c "cd \"$STACK_DIR\" && tofu validate -no-color"
}

build_plan() {
  # No .tfvars file used – all variables come from environment
  exec_and_log "tofu-plan" bash -c "cd \"$STACK_DIR\" && tofu plan -out=\"$PLAN_FILE\" -input=false"
  log "Plan written to ${PLAN_FILE}"
}

apply_plan_auto() {
  if [ ! -f "$PLAN_FILE" ]; then
    echo "ERROR: plan file not found: ${PLAN_FILE}" >&2
    exit 40
  fi
  exec_and_log "tofu-apply-plan" bash -c "cd \"$STACK_DIR\" && tofu apply -input=false -auto-approve \"$PLAN_FILE\""
}

destroy_auto() {
  # No .tfvars file – rely on environment variables
  exec_and_log "tofu-destroy" bash -c "cd \"$STACK_DIR\" && tofu destroy -input=false -auto-approve"
}


force_cleanup() {
  # Use environment variable or default to "staging"
  local env="${TF_VAR_environment:-staging}"
  log "Starting forced cleanup for environment: $env"

  local name_prefix="agentops-${env}"
  local cluster_name="${name_prefix}-cluster"
  local asg_name="${cluster_name}-asg"
  local lt_name="${cluster_name}-lt"

  # ----------------------------------------------------------------------
  # 1. ECS Services – force delete to bypass stuck DRAINING state
  # ----------------------------------------------------------------------
  log "Scaling down ECS services..."
  aws ecs update-service --cluster "$cluster_name" --service "${cluster_name}-agent" \
    --desired-count 0 --force-new-deployment 2>/dev/null || true
  aws ecs update-service --cluster "$cluster_name" --service "${cluster_name}-mcp" \
    --desired-count 0 --force-new-deployment 2>/dev/null || true
  sleep 20

  log "Deleting ECS services (force)..."
  aws ecs delete-service --cluster "$cluster_name" --service "${cluster_name}-agent" \
    --force 2>/dev/null || true
  aws ecs delete-service --cluster "$cluster_name" --service "${cluster_name}-mcp" \
    --force 2>/dev/null || true
  sleep 20

  # ----------------------------------------------------------------------
  # 2. Disassociate capacity provider from cluster
  #    This is the missing step that caused DELETE_FAILED for the cp.
  # ----------------------------------------------------------------------
  log "Removing capacity provider from cluster strategy..."
  aws ecs put-cluster-capacity-providers --cluster "$cluster_name" \
    --capacity-providers "[]" --default-capacity-provider-strategy "[]" 2>/dev/null || true
  sleep 10

  # ----------------------------------------------------------------------
  # 3. Now delete the capacity provider (now disassociated)
  # ----------------------------------------------------------------------
  log "Deleting capacity provider..."
  aws ecs delete-capacity-provider --capacity-provider "${cluster_name}-cp" 2>/dev/null || true
  sleep 5

  # ----------------------------------------------------------------------
  # 4. Delete the cluster (now no longer referencing the cp)
  # ----------------------------------------------------------------------
  log "Deleting ECS cluster..."
  aws ecs delete-cluster --cluster "$cluster_name" 2>/dev/null || true
  sleep 5

  # ----------------------------------------------------------------------
  # 5. Auto Scaling Group – force delete even with instances
  # ----------------------------------------------------------------------
  log "Deleting Auto Scaling Group (force)..."
  aws autoscaling delete-auto-scaling-group --auto-scaling-group-name "$asg_name" \
    --force-delete 2>/dev/null || true

  # ----------------------------------------------------------------------
  # 6. Launch Template
  # ----------------------------------------------------------------------
  log "Deleting launch template..."
  aws ec2 delete-launch-template --launch-template-name "$lt_name" 2>/dev/null || true

  # ----------------------------------------------------------------------
  # 7. Internet Gateway – detach then delete
  # ----------------------------------------------------------------------
  local vpc_id
  vpc_id=$(aws ec2 describe-vpcs --filters "Name=tag:Name,Values=${name_prefix}-vpc" \
    --query "Vpcs[0].VpcId" --output text 2>/dev/null)

  if [ -n "$vpc_id" ] && [ "$vpc_id" != "None" ]; then
    local igw_id
    igw_id=$(aws ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$vpc_id" \
      --query "InternetGateways[0].InternetGatewayId" --output text 2>/dev/null)

    if [ -n "$igw_id" ] && [ "$igw_id" != "None" ]; then
      log "Detaching and deleting IGW: $igw_id"
      aws ec2 detach-internet-gateway --internet-gateway-id "$igw_id" --vpc-id "$vpc_id" 2>/dev/null || true
      aws ec2 delete-internet-gateway --internet-gateway-id "$igw_id" 2>/dev/null || true
    fi
  fi

  log "Force cleanup completed."
}

list_state_versions() {
  local bucket="$1"
  local key="$2"

  local json
  if ! json="$(aws s3api list-object-versions \
      --bucket "$bucket" \
      --prefix "$key" \
      --output json 2>/dev/null)"; then
    echo "ERROR: unable to list versions for key: $key in bucket: $bucket" >&2
    return 1
  fi

  python3 -c '
import json
import sys

key = sys.argv[1]

try:
    data = json.load(sys.stdin)
except Exception:
    print(f"No versions found or error listing versions for: {key}")
    sys.exit(0)

rows = []
for v in data.get("Versions", []):
    if v.get("Key") == key:
        rows.append((v.get("VersionId"), v.get("LastModified"), "Version"))

for d in data.get("DeleteMarkers", []):
    if d.get("Key") == key:
        rows.append((d.get("VersionId"), d.get("LastModified"), "DeleteMarker"))

if not rows:
    print(f"No versions found for key: {key}")
    sys.exit(0)

print("{:<36}  {:<30}  {}".format("VersionId", "LastModified", "info"))
for ver, lm, info in rows:
    print("{:<36}  {:<30}  {}".format(ver or "", str(lm or ""), info))
' "$key" <<<"$json"
}

rollback_state_version() {
  local bucket="$1"
  local key="$2"
  local version="$3"
  local found
  found="$(aws s3api list-object-versions --bucket "$bucket" --prefix "$key" --query "Versions[?VersionId=='${version}'] | [0].VersionId" --output text 2>/dev/null || true)"
  if [ -z "$found" ] || [ "$found" = "None" ]; then
    echo "ERROR: versionId ${version} not found for ${key} in ${bucket}" >&2
    return 2
  fi
  exec_and_log "s3-copy-rollback" aws s3api copy-object --bucket "$bucket" --copy-source "${bucket}/${key}?versionId=${version}" --key "$key" --metadata-directive REPLACE
}

init_backend() {
  ensure_bucket_exists_and_versioning "$STATE_BUCKET" "$TF_VAR_region"
  exec_and_log "tofu-init" bash -c "cd \"$STACK_DIR\" && tofu init -backend-config \"bucket=${STATE_BUCKET}\" -backend-config \"key=${STATE_KEY}\" -backend-config \"region=${TF_VAR_region}\" -backend-config \"use_lockfile=true\" -input=false"
}

case "$MODE" in
  --plan)
    init_backend
    fmt_auto_fix_if_needed
    validate_config
    build_plan
    ;;
  --create)
    init_backend
    fmt_auto_fix_if_needed
    validate_config
    build_plan
    apply_plan_auto
    ;;
  --destroy)
    if [ "$YES_DELETE" != true ]; then
      echo "ERROR: destructive action requires --yes-delete" >&2
      exit 3
    fi
    init_backend
    force_cleanup   # no argument – reads TF_VAR_environment internally
    destroy_auto
    ;;
  --validate)
    init_backend   # ensures bucket exists and runs tofu init once
    validate_backend "$STATE_BUCKET" "$STATE_KEY" "$TF_VAR_region"
    ;;
  --find-version)
    list_state_versions "$STATE_BUCKET" "$STATE_KEY"
    ;;
  --rollback-state)
    if [ "$YES_DELETE" != true ]; then
      echo "ERROR: rollback requires --yes-delete" >&2
      exit 3
    fi
    rollback_state_version "$STATE_BUCKET" "$STATE_KEY" "$ROLLBACK_VERSION"
    ;;
  *)
    usage
    ;;
esac