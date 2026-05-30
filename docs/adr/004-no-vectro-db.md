# ADR 004: S3 + Brute-Force Search over Vector Database

## Context
The agent-service searches company policy documents (~6 documents,
~59 chunks, 1024-dimensional embeddings) to ground LLM responses.
A previous project used Qdrant with a separate dense-service—this
system intentionally avoids that complexity.

## Decision
Store pre-computed embeddings as a single JSON file in S3 (~1.3 MB).
Load and cache in memory at agent startup. Compute cosine similarity
via NumPy against all chunks in-process. Use Bedrock Titan Embeddings v2
for one-time embedding generation and on-the-fly query vectors.

No vector database. No S3 Vectors. No pgvector. No OpenSearch.

## Options Considered

| Option | Monthly Cost | Latency | Complexity |
|--------|-------------|---------|------------|
| Qdrant (self-hosted) | ~$40 | <5ms | High (separate service, gRPC, container) |
| OpenSearch Serverless | ~$150 | <10ms | Medium (managed, OCU minimum) |
| pgvector | $0 (existing RDS) | <20ms | Low (adds extension to PostgreSQL) |
| S3 Vectors | ~$1 | <100ms | Low (managed, newer service, network round-trip) |
| **S3 + brute-force NumPy (chosen)** | **~$0.03** | **<5ms** | **Lowest (in-process, no extra service)** |

## Rationale
- At 59 chunks, brute-force cosine similarity completes in <5ms.
  Any form of indexing (ANN, pgvector, S3 Vectors) adds latency
  and infrastructure without measurable benefit.
- The embeddings file is 1.3 MB. In-memory caching eliminates
  per-query network calls after initial load.
- No additional service to deploy, patch, monitor, or pay for.
- Deterministic retrieval—identical results every time. Easy
  debugging (embeddings are inspectable JSON).
- Bedrock Titan v2 requires no Marketplace subscription.

## Consequences
- **Positive:** Eliminated dedicated vector database (~$40-150/month
  savings). In-process retrieval (<5ms). Zero operational overhead.
  Single JSON file in S3—trivially debuggable.
- **Negative:** Linear scaling with corpus size. No built-in
  metadata filtering (application code filters results by tags
  if needed). Requires reload on embedding updates (acceptable—
  policy changes are infrequent).

## When to Revisit
- Corpus exceeds 10,000 chunks (latency >50ms expected).
- Metadata filtering needed before retrieval (not after).
- Hybrid search (lexical + vector) required.
- Multi-tenant isolation becomes necessary.
