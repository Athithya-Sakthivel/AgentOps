# Cloudflare Terraform Stack — AgentOps

This stack manages the Cloudflare-side infrastructure for AgentOps. It creates one named Cloudflare Tunnel, a single DNS CNAME record for the root domain, and a small set of zone settings. It does not create any ECS resources, load balancers, or origin certificates.

## Architecture

```
Browser → Cloudflare (athithya.site) → Tunnel → cloudflared (systemd on EC2) → localhost:8000 (agent-service)
```

No AWS load balancer is required. TLS terminates at Cloudflare. The agent-service serves both the frontend (static HTML/JS) and the API (WebSocket, REST, OIDC) from a single port.

## Public Hostname

The deployment is exposed under:

- `https://athithya.site`

A wildcard DNS record (`*.athithya.site`) is included for future subdomain use (e.g., `api.athithya.site`, `admin.athithya.site`).

## Tunnel Model

The tunnel name is `agentops-tunnel`. Cloudflare Tunnel resolves to a tunnel target of the form `<UUID>.cfargotunnel.com`. This stack creates DNS CNAME records that point the published hostnames at that tunnel target. The actual routing to the agent-service happens in the `cloudflared` configuration on the EC2 host (via user-data), not in Terraform.

## What This Stack Creates

### DNS Records

- `athithya.site` → tunnel CNAME
- `*.athithya.site` → tunnel CNAME (wildcard for future subdomains)

### Zone Settings

- `ssl = strict`
  AgentOps uses Cloudflare Tunnel, which provides a valid certificate end-to-end. Strict mode ensures traffic is encrypted all the way to the origin.
- `always_use_https = on`
  HTTP requests are redirected to HTTPS.
- `tls_1_3 = on`
  TLS 1.3 is enabled for the zone.

### Bot Protections

- `enable_bot_fight_mode = true`
  Bot Fight Mode blocks unneccessary API traffic. Enabled by default.
- `enable_js_detections = true`
  JavaScript detections help filter automated traffic without blocking legitimate users.

## What It Does Not Create

This stack does not create:

- Cloudflare Pages
- Separate DNS records for subdomains (the wildcard covers them)
- ECS or EC2 resources
- Public load balancers
- Origin certificates

## Runtime Model

The outputs from this stack are used by the `cloudflared` systemd service on the EC2 instance. The `cloudflared` configuration (set via user-data) routes all traffic on `athithya.site` and `*.athithya.site` to `http://localhost:8000`.

## Inputs

### Required

- `CLOUDFLARE_ACCOUNT_ID`
- Cloudflare zone apex provided via `TF_VAR_domain` (or `DOMAIN` env var)

### Required for Authentication

Use one of:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_GLOBAL_API_KEY` with `CLOUDFLARE_EMAIL`

### Optional

- `TF_VAR_tunnel_name` (default: `agentops-tunnel`)
- `TF_VAR_enable_always_use_https` (default: `true`)
- `TF_VAR_enable_tls_1_3` (default: `true`)
- `TF_VAR_enable_bot_fight_mode` (default: `true`)
- `TF_VAR_enable_js_detections` (default: `true`)

## Execution

Set environment variables:

```bash
export CLOUDFLARE_ACCOUNT_ID="4f75c52006dba7aa4096a71f1ed30223"
export CLOUDFLARE_GLOBAL_API_KEY="<your-api-key>"
export CLOUDFLARE_EMAIL="athithya651@gmail.com"
export TF_VAR_domain="athithya.site"
export TF_VAR_tunnel_name="agentops-tunnel"
```

Plan:
```bash
bash src/infra/cloudflare/run.sh --plan
```

Apply:
```bash
bash src/infra/cloudflare/run.sh --apply
```

Destroy:
```bash
bash src/infra/cloudflare/run.sh --destroy
```

## Outputs

- `cloudflare_tunnel_id`
- `cloudflare_tunnel_name`
- `cloudflare_tunnel_token`
- `root_url`

## Runtime Exports

Use the tunnel token in the EC2 user-data script:

```bash
export CLOUDFLARE_TUNNEL_TOKEN="$(tofu -chdir=src/infra/cloudflare output -raw cloudflare_tunnel_token)"
```

The `user-data.sh` script installs `cloudflared` and runs:

```bash
cloudflared service install "${CLOUDFLARE_TUNNEL_TOKEN}"
```

## Idempotency

This stack is intended to be safely rerun. Existing DNS records and zone settings are imported into state if they already exist, and the named tunnel is reused when it already exists.

## SSL Configuration

Cloudflare SSL mode is set to **Strict**. Since AgentOps uses Cloudflare Tunnel (not a self-managed origin certificate), the tunnel provides a valid certificate end-to-end. Strict mode ensures maximum security without compatibility issues.

## Redirect URIs for OIDC

With this setup, the OIDC redirect URIs are:

| Provider | Redirect URI |
|----------|-------------|
| Google | `https://athithya.site/auth/callback/google` |
| Microsoft | `https://athithya.site/auth/callback/microsoft` |
```