#!/bin/bash
# =============================================================================
# AgentOps — SSM Parameter Store Setup (Idempotent)
# =============================================================================
# Creates all secrets in AWS SSM Parameter Store.
# After this, agent-service reads them automatically via load_ssm_parameters().
#
# IMPORTANT: Passwords and keys are written to temporary files and uploaded
#            with `--value file://` so that special characters, newlines,
#            and quotes are preserved exactly.
#
# Usage:
#   export GOOGLE_CLIENT_ID="..."
#   export GOOGLE_CLIENT_SECRET="..."
#   export MICROSOFT_CLIENT_ID="..."
#   export MICROSOFT_CLIENT_SECRET="..."
#   export DOMAIN=athithya.site
#   bash src/infra/scripts/ssm-put.sh
# =============================================================================
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-south-1}"
PREFIX="/agentops"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "============================================"
echo "  AgentOps — SSM Parameter Setup"
echo "  Region: ${AWS_REGION}"
echo "============================================"

# ── JWT Signing Key (EC P-256, PEM format) ──────────────────────
JWT_PEM="${TMPDIR}/jwt-private.pem"
python3 -c "
from joserfc.jwk import ECKey
key = ECKey.generate_key('P-256')
with open('${JWT_PEM}', 'w') as f:
    f.write(key.as_pem().decode())
"
echo "  [CREATE] /agentops/jwt-private-key-pem (SecureString)"
aws ssm put-parameter \
    --name "${PREFIX}/jwt-private-key-pem" \
    --type SecureString \
    --value "file://${JWT_PEM}" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

echo "  [CREATE] /agentops/jwt-kid (String)"
aws ssm put-parameter \
    --name "${PREFIX}/jwt-kid" \
    --type String \
    --value "agentops-jwt-key" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

# ── Session Secret (random URL-safe string) ──────────────────────
SESSION_SECRET_FILE="${TMPDIR}/session-secret"
python3 -c "import secrets; open('${SESSION_SECRET_FILE}','w').write(secrets.token_urlsafe(64))"
echo "  [CREATE] /agentops/session-secret (SecureString)"
aws ssm put-parameter \
    --name "${PREFIX}/session-secret" \
    --type SecureString \
    --value "file://${SESSION_SECRET_FILE}" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

# ── Google OAuth ─────────────────────────────────────────────────
: "${GOOGLE_CLIENT_ID:?GOOGLE_CLIENT_ID must be set}"
: "${GOOGLE_CLIENT_SECRET:?GOOGLE_CLIENT_SECRET must be set}"

echo "${GOOGLE_CLIENT_ID}" > "${TMPDIR}/google-client-id"
echo "${GOOGLE_CLIENT_SECRET}" > "${TMPDIR}/google-client-secret"

echo "  [CREATE] /agentops/google-client-id (SecureString)"
aws ssm put-parameter \
    --name "${PREFIX}/google-client-id" \
    --type SecureString \
    --value "file://${TMPDIR}/google-client-id" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

echo "  [CREATE] /agentops/google-client-secret (SecureString)"
aws ssm put-parameter \
    --name "${PREFIX}/google-client-secret" \
    --type SecureString \
    --value "file://${TMPDIR}/google-client-secret" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

# ── Microsoft OAuth ──────────────────────────────────────────────
: "${MICROSOFT_CLIENT_ID:?MICROSOFT_CLIENT_ID must be set}"
: "${MICROSOFT_CLIENT_SECRET:?MICROSOFT_CLIENT_SECRET must be set}"

echo "${MICROSOFT_CLIENT_ID}" > "${TMPDIR}/microsoft-client-id"
echo "${MICROSOFT_CLIENT_SECRET}" > "${TMPDIR}/microsoft-client-secret"

echo "  [CREATE] /agentops/microsoft-client-id (SecureString)"
aws ssm put-parameter \
    --name "${PREFIX}/microsoft-client-id" \
    --type SecureString \
    --value "file://${TMPDIR}/microsoft-client-id" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

echo "  [CREATE] /agentops/microsoft-client-secret (SecureString)"
aws ssm put-parameter \
    --name "${PREFIX}/microsoft-client-secret" \
    --type SecureString \
    --value "file://${TMPDIR}/microsoft-client-secret" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

# Microsoft tenant ID — fail fast, never default to "common"
: "${MICROSOFT_TENANT_ID:?MICROSOFT_TENANT_ID must be set (Azure tenant GUID)}"
echo "  [CREATE] /agentops/ms-tenant-id (String)"
aws ssm put-parameter \
    --name "${PREFIX}/ms-tenant-id" \
    --type String \
    --value "${MICROSOFT_TENANT_ID}" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

# ── Domain ───────────────────────────────────────────────────────
: "${DOMAIN:?DOMAIN must be set (e.g., athithya.site or localhost:8000)}"
echo "  [CREATE] /agentops/domain (String)"
aws ssm put-parameter \
    --name "${PREFIX}/domain" \
    --type String \
    --value "${DOMAIN}" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

# ── Admin Access Control ─────────────────────────────────────────
echo "  [CREATE] /agentops/admin-allowed-google-domains (StringList)"
aws ssm put-parameter \
    --name "${PREFIX}/admin-allowed-google-domains" \
    --type StringList \
    --value "gmail.com" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

echo "  [CREATE] /agentops/admin-allowed-microsoft-tenants (StringList)"
aws ssm put-parameter \
    --name "${PREFIX}/admin-allowed-microsoft-tenants" \
    --type StringList \
    --value "${MICROSOFT_TENANT_ID}" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

echo ""
echo "============================================"
echo "  All SSM parameters created successfully."
echo "  Agent-service will load them at startup."
echo "============================================"