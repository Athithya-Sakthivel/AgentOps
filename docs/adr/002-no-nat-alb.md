# ADR 002: Cloudflare Tunnel over NAT Gateway + Application Load Balancer

## Context
The system needs public WebSocket and HTTPS endpoints. The AWS default
uses an Application Load Balancer (ALB, ~$22/month) with AWS WAF (~$8/month).
Private subnets add NAT Gateway (~$32/month) for outbound to AWS APIs.

## Decision
Use Cloudflare Tunnel for all inbound traffic. Run cloudflared as a
systemd service on the EC2 host. Security group has zero inbound rules.
Outbound to AWS APIs uses public subnet + Internet Gateway (free).
Cloudflare free tier provides WAF, DDoS protection, and rate limiting.

## Options Considered

| Option | Monthly Cost | Security Model |
|--------|-------------|----------------|
| ALB + AWS WAF (public subnets) | ~$30 | Security groups + WAF rules |
| ALB + WAF (private subnets + NAT) | ~$62 | No public IPs, NAT for outbound |
| Cloudflare Tunnel (public subnet, no inbound) | $0 | Zero inbound exposure, WAF at edge |

## Rationale
- Cloudflare Tunnel creates encrypted ingress without open ports.
- Public subnet + Internet Gateway handles outbound to Bedrock, DynamoDB,
  and S3 for free. No NAT Gateway required.
- Cloudflare free tier includes managed WAF, DDoS, and rate limiting.
- Eliminates ALB, AWS WAF, and NAT Gateway costs simultaneously.

## Consequences
- **Positive:** $0/month networking cost. Zero inbound attack surface.
  Simplified architecture (fewer AWS resources).
- **Negative:** Dependency on Cloudflare. cloudflared is a host process,
  not managed by ECS (mitigated: systemd auto-restart, user-data reinstall).

## When to Revisit
- Compliance requires single-vendor infrastructure.
- Cloudflare free tier limits become restrictive.