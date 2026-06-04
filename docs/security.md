# AgentOps — Security Architecture

**Last updated:** June 2026
**OWASP ZAP DAST scan:** 0 Critical · 0 High · 3 Medium · 3 Low · 4 Informational
**Trivy container scan:** 0 Critical CVEs
**Gitleaks history scan:** 0 secrets exposed

---

## Overview

AgentOps is secured across five independent layers. Every layer has automated verification in CI/CD.

```
┌─────────────────────────────────────────────────────────────┐
│                   LAYER 1 — EDGE (CLOUDFLARE)               │
│  DDoS · Bot management · TLS 1.3 · WAF · Zero inbound ports │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                 LAYER 2 — APPLICATION (FASTAPI)             │
│  OIDC · JWT (ES256) · Admin RBAC · Rate limiting · CORS    │
│  Parameterized queries (no SQL injection)                  │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                  LAYER 3 — CONTAINER (DOCKER)               │
│  Non‑root user · Minimal base image · Trivy CVE scanning   │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                    LAYER 4 — NETWORK (AWS)                  │
│  RDS in private subnets · Security groups · IAM roles      │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────────────────────────────────────┐
│                LAYER 5 — DATA (POSTGRESQL / SSM)           │
│  Secrets in SSM · RDS encryption · No secrets in Git       │
└─────────────────────────────────────────────────────────────┘
```

---

## Layer 1 — Edge Security (Cloudflare)

| Control | Implementation |
|---------|---------------|
| TLS termination | Cloudflare Tunnel (mutual TLS). SSL mode: **Strict**. |
| DDoS protection | Cloudflare's global network absorbs volumetric attacks. |
| Bot management | JavaScript detections + Bot Fight Mode. |
| Zero inbound ports | No ports open on EC2 security groups. All traffic via Tunnel. |
| WAF | Managed rules block SQL injection, XSS, OWASP Top 10 at the edge. |

**Evidence:** ZAP scan — 0 critical/high findings reach the application.

---

## Layer 2 — Application Security (FastAPI + Python)

### Authentication

| Control | Implementation |
|---------|---------------|
| OIDC | Google & Microsoft OAuth 2.0 with PKCE (S256). No passwords stored. |
| JWT | Self‑issued ES256 tokens. 15‑minute expiry. Verified via local JWKS. |
| Admin RBAC | Domain‑based (`sairamtap.edu.in` / specific tenant IDs). |
| Sessions | `HttpOnly`, `SameSite=Lax`, `Secure` (production). |
| Rate limiting | DynamoDB‑backed per‑user counters. Fails open. |

### Injection Prevention

| Vulnerability | Protection | ZAP Result |
|--------------|-----------|:---:|
| SQL Injection | Parameterized queries (asyncpg + SQLAlchemy) | 0 findings |
| XSS | Jinja2 auto‑escaping + `escapeHtml()` + CSP | 0 findings |
| Command Injection | No system commands from user input | N/A |
| Path Traversal | No file serving from user input | N/A |

### API Security

| Control | Implementation |
|---------|---------------|
| CORS | Restricted to `https://athithya.site` in production. |
| Input validation | FastAPI + Pydantic on all endpoints. |
| Error handling | No stack traces or internal details in responses. |

---

## Layer 3 — Container Security (Docker)

| Control | Implementation |
|---------|---------------|
| Non‑root user | Both services run as `appuser` (UID 1000). |
| Minimal image | Python 3.12 slim. No build tools or compilers. |
| CVE scanning | Trivy scans every image in CI/CD for CRITICAL CVEs. |
| Immutable tags | ECR enforces immutable image tags. |

**Evidence:** Latest `agent-service:staging` and `mcp-server:staging` — 0 critical CVEs.

---

## Layer 4 — Network Security (AWS)

| Control | Implementation |
|---------|---------------|
| No public IPs | EC2 in public subnets, security groups have zero inbound rules. |
| RDS isolation | PostgreSQL in private subnets. Only accessible from ECS security group. |
| Least‑privilege IAM | Agent service: SSM read, Bedrock invoke, S3 read, DynamoDB write. MCP server: no IAM role. |
| Security groups | ECS → RDS (5432 only). No other inbound rules. |

---

## Layer 5 — Data & Secrets Management

| Control | Implementation |
|---------|---------------|
| Secrets storage | AWS SSM Parameter Store (encrypted). Never in env vars or `.env` files. |
| No secrets in Git | Gitleaks scans full history on every push. Pre‑commit hook blocks accidents. |
| Database encryption | RDS encrypted at rest. All connections use TLS. |
| JWT key rotation | EC P‑256 key generated at deployment. |
| Session secret | Random 64‑byte URL‑safe string generated at deployment. |

**Evidence:** Gitleaks full‑history scan — 0 secrets found.

---

## CI/CD Security Gates

| Gate | Tool | Trigger | Blocks on Failure |
|------|------|---------|:---:|
| SAST | OpenGrep (OWASP Top 10) | Every push | Yes |
| Secret detection | Gitleaks (full history) | Every push | Yes |
| Container CVE scan | Trivy (CRITICAL) | Every push to main | Yes |
| DAST | OWASP ZAP (authenticated) | Weekly + manual | No (creates issue) |
| Pre‑commit | Ruff + Gitleaks + Basedpyright | Local commits | Yes |

---

## DAST Scan Results (Latest)

| Severity | Count | Details |
|----------|:-----:|---------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 3 | CSP headers (Cloudflare's challenge page — not exploitable) |
| Low | 3 | HSTS (Cloudflare enforces HTTPS), timestamp (Cloudflare challenge), `X-Content-Type-Options` on JWKS |
| Informational | 4 | Modern web app, User‑Agent fuzzer, cache‑control |

**Full report:** `src/reports/dast_scan_latest.json`

---

## Threat Model

| Threat | Mitigation | Residual Risk |
|--------|-----------|:---:|
| SQL Injection | Parameterized queries | None |
| XSS | Auto‑escaping + CSP | None |
| Credential theft | OIDC + JWT (short TTL) | Low |
| Admin account takeover | Domain‑based RBAC + Google OAuth | Low |
| Secret leakage | SSM + Gitleaks + pre‑commit | None |
| DDoS | Cloudflare | None |
| Container escape | Non‑root user + minimal image | Low |
| Data breach | RDS encryption + private subnets + TLS | Low |

---

## Roadmap

- [ ] `Strict-Transport-Security` header in FastAPI middleware
- [ ] `X-Content-Type-Options: nosniff` header
- [ ] CSP header in FastAPI (override Cloudflare default)
- [ ] Cloudflare WAF managed rules for `/admin/*`
- [ ] Dependabot for Python package alerts
- [ ] Automated JWT key rotation