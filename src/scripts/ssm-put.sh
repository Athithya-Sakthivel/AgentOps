#!/bin/bash
# =============================================================================
# AgentOps — SSM Parameter Store Setup (Idempotent)
# =============================================================================
# *** NO TRAILING NEWLINES ***
#
# All text values are written with `printf '%s'` (never `echo`) and verified
# that they do NOT end with a newline.  For SecureString parameters the value
# is passed via a temp file that has *exactly* the correct bytes.
#
# Usage:
#   export GOOGLE_CLIENT_ID="..."
#   export GOOGLE_CLIENT_SECRET="..."
#   export MICROSOFT_CLIENT_ID="..."
#   export MICROSOFT_CLIENT_SECRET="..."
#   export MICROSOFT_TENANT_ID="..."
#   export DOMAIN="athithya.site"
#   bash src/scripts/ssm-put.sh
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

# -------------------------------------------------------------------
# Helper: write a string to a file WITHOUT a trailing newline
# -------------------------------------------------------------------
write_no_newline() {
    local file="$1" value="$2"
    printf '%s' "$value" > "$file"
    # Double‑check: last byte must NOT be 0x0a
    if [ "$(od -A n -t x1 "$file" | tr -d ' \n' | tail -c 2)" = "0a" ]; then
        echo "ERROR: trailing newline detected in $file – aborting" >&2
        exit 1
    fi
}

# -------------------------------------------------------------------
# Upload a SecureString from a temp file (guaranteed no newline)
# -------------------------------------------------------------------
put_secure_from_file() {
    local name="$1" file="$2"
    echo "  [CREATE] ${name} (SecureString)"
    aws ssm put-parameter \
        --name "${name}" \
        --type SecureString \
        --value "file://${file}" \
        --overwrite \
        --region "${AWS_REGION}" > /dev/null
}

# -------------------------------------------------------------------
# Upload a plain String parameter directly (no file)
# -------------------------------------------------------------------
put_string() {
    local name="$1" value="$2"
    echo "  [CREATE] ${name} (String)"
    aws ssm put-parameter \
        --name "${name}" \
        --type String \
        --value "${value}" \
        --overwrite \
        --region "${AWS_REGION}" > /dev/null
}

# -------------------------------------------------------------------
# 1. JWT Signing Key (PEM) – PEM *must* end with a newline (part of spec)
# -------------------------------------------------------------------
JWT_PEM="${TMPDIR}/jwt-private.pem"
python3 -c "
from joserfc.jwk import ECKey
key = ECKey.generate_key('P-256')
with open('${JWT_PEM}', 'wb') as f:
    f.write(key.as_pem())      # as_pem() returns bytes ending with \n
"
echo "  [CREATE] /agentops/jwt-private-key-pem (SecureString)"
aws ssm put-parameter \
    --name "${PREFIX}/jwt-private-key-pem" \
    --type SecureString \
    --value "file://${JWT_PEM}" \
    --overwrite \
    --region "${AWS_REGION}" > /dev/null

# -------------------------------------------------------------------
# 2. JWT Key ID
# -------------------------------------------------------------------
put_string "${PREFIX}/jwt-kid" "agentops-jwt-key"

# -------------------------------------------------------------------
# 3. Session Secret – random URL‑safe, no newline
# -------------------------------------------------------------------
SESSION_SECRET_FILE="${TMPDIR}/session-secret"
python3 -c "
import secrets
with open('${SESSION_SECRET_FILE}', 'w') as f:
    f.write(secrets.token_urlsafe(64))
"
# Verify no trailing newline
if [ "$(od -A n -t x1 "${SESSION_SECRET_FILE}" | tr -d ' \n' | tail -c 2)" = "0a" ]; then
    truncate -s -1 "${SESSION_SECRET_FILE}"
fi
put_secure_from_file "${PREFIX}/session-secret" "${SESSION_SECRET_FILE}"

# -------------------------------------------------------------------
# 4. Google OAuth
# -------------------------------------------------------------------
: "${GOOGLE_CLIENT_ID:?GOOGLE_CLIENT_ID must be set}"
: "${GOOGLE_CLIENT_SECRET:?GOOGLE_CLIENT_SECRET must be set}"

write_no_newline "${TMPDIR}/google-client-id" "$GOOGLE_CLIENT_ID"
write_no_newline "${TMPDIR}/google-client-secret" "$GOOGLE_CLIENT_SECRET"
put_secure_from_file "${PREFIX}/google-client-id" "${TMPDIR}/google-client-id"
put_secure_from_file "${PREFIX}/google-client-secret" "${TMPDIR}/google-client-secret"

# -------------------------------------------------------------------
# 5. Microsoft OAuth
# -------------------------------------------------------------------
: "${MICROSOFT_CLIENT_ID:?MICROSOFT_CLIENT_ID must be set}"
: "${MICROSOFT_CLIENT_SECRET:?MICROSOFT_CLIENT_SECRET must be set}"

write_no_newline "${TMPDIR}/microsoft-client-id" "$MICROSOFT_CLIENT_ID"
write_no_newline "${TMPDIR}/microsoft-client-secret" "$MICROSOFT_CLIENT_SECRET"
put_secure_from_file "${PREFIX}/microsoft-client-id" "${TMPDIR}/microsoft-client-id"
put_secure_from_file "${PREFIX}/microsoft-client-secret" "${TMPDIR}/microsoft-client-secret"

# -------------------------------------------------------------------
# 6. Microsoft Tenant ID (must NOT be common – fail fast)
# -------------------------------------------------------------------
: "${MICROSOFT_TENANT_ID:?MICROSOFT_TENANT_ID must be set (Azure tenant GUID)}"
put_string "${PREFIX}/ms-tenant-id" "$MICROSOFT_TENANT_ID"

# -------------------------------------------------------------------
# 7. Domain
# -------------------------------------------------------------------
: "${DOMAIN:?DOMAIN must be set (e.g., athithya.site or localhost:8000)}"
put_string "${PREFIX}/domain" "$DOMAIN"

# -------------------------------------------------------------------
# 8. Admin Access Control – explicit for each type
# -------------------------------------------------------------------
# We want only sairamtap.edu.in to have admin access.
put_string "${PREFIX}/admin-allowed-google-domains" "sairamtap.edu.in"
put_string "${PREFIX}/admin-allowed-microsoft-tenants" "$MICROSOFT_TENANT_ID"

echo ""
echo "============================================"
echo "  All SSM parameters created successfully."
echo "  No trailing newlines in any value."
echo "============================================"