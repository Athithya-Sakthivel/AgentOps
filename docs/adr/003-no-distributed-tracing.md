# ADR 003: Structured Logging over Distributed Tracing

## Context
The system has 2 services with sequential request paths. A single request
touches at most 3 components: API Gateway → agent-service → mcp-server.

## Decision
Use structured JSON logs with correlation IDs (`run_id`) and custom
CloudWatch metrics. Do not deploy OpenTelemetry Collector or X-Ray
distributed tracing.

Metrics are emitted directly via `boto3.put_metric_data()` to CloudWatch,
not through an OpenTelemetry Collector. At 2-service scale, OTEL adds
configuration complexity, compute overhead, and a new failure mode
without proportional benefit.

## Correlation ID Propagation
`run_id` is generated at the WebSocket handler and passed through every
layer: LangGraph state → MCP client → mcp-server. A single CloudWatch
Logs Insights query reconstructs the full request path:

```sql
fields @timestamp, @logGroup, level, message, duration_ms
| filter run_id = 'run-abc123'
| sort @timestamp asc
| limit 200