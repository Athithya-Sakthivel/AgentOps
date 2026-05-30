## `004-no-vector-db.md`

# ADR 004: S3 + Brute-Force Search over Vector Database

## Context
The agent-service searches company policy documents (~50 documents,
~400 chunks, 1536-dimensional embeddings) to ground LLM responses.
A previous project used Qdrant with a separate dense-service—this
system intentionally avoids that complexity.

## Decision
Store pre-computed embeddings as a single JSON file in S3 (~2.5 MB).
Load and cache in memory at startup. Compute cosine similarity via
NumPy against all chunks in-process. Use Bedrock (Titan Embeddings)
for one-time embedding generation and on-the-fly query vectors.

## Options Considered

| Option | Monthly Cost | Latency | Complexity |
|--------|-------------|---------|------------|
| Qdrant (self-hosted) | ~$40 | <5ms | High (separate service, gRPC) |
| OpenSearch Serverless | ~$150 | <10ms | Medium (managed, OCU minimum) |
| pgvector | $0 (existing RDS) | <20ms | Low (adds extension) |
| S3 + brute-force | ~$0.03 | <5ms | Lowest (in-process) |

## Rationale
- At 400 chunks, brute-force cosine similarity completes in <5ms.
  ANN indexing provides no measurable latency benefit.
- The embeddings file is 2.5 MB. In-memory caching eliminates per-query
  network calls.
- No additional service to deploy, monitor, or pay for.

## Consequences
- **Positive:** Eliminated dedicated vector database (~$40/month savings).
  Deterministic retrieval (identical results every time). Easy debugging
  (embeddings are inspectable JSON).
- **Negative:** Linear scaling with corpus size (acceptable to ~10,000 chunks).
  No built-in metadata filtering (application code handles this).

## When to Revisit
- Corpus exceeds 10,000 chunks (latency >50ms expected).
- Metadata filtering needed before retrieval.
- Hybrid search (lexical + vector) required.