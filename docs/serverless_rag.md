# Serverless RAG for Company Policy Documents

## Overview

Lightweight serverless RAG for internal company policy documents using
Amazon S3 for storage and in-process NumPy cosine similarity for retrieval.
No vector database. No Lambda required for retrieval (runs inline in the
agent-service container).

**Optimized for:**
- <1,000 document chunks
- Low operational overhead
- Low cost (~$0.03/month)
- Agentic workflows (LangGraph + DSPy)

---

## Why This Architecture

For corpora under ~1,000 chunks, ANN indexes provide no measurable benefit
while adding operational complexity. Brute-force cosine similarity over
1024-dimensional vectors completes in <5ms. The actual bottlenecks are
LLM inference latency, chunk quality, and prompt quality—not retrieval speed.

A single JSON file in S3 replaces the entire vector database stack:
no Qdrant, no OpenSearch, no pgvector, no S3 Vectors, no dense-service.

---

## Architecture

```
Policy docs (markdown) ──► index.py ──► Bedrock Titan v2 ──► embeddings.json ──► S3
                                                                                    │
User query ──► agent-service ──► Bedrock Titan v2 (query embedding)               │
                   │                                                                 │
                   ├── Load embeddings.json from S3 (cached in memory) ◄───────────┘
                   ├── NumPy cosine similarity (in-process, <5ms)
                   ├── Select top-K chunks
                   └── Inject into Bedrock Claude prompt ──► Grounded response
```

**Storage layout:**
- `s3://bucket/embeddings.json` — Single file, 59 chunks, 1024-dim vectors, ~1.3 MB

---

## Retrieval Strategy

1. Agent receives user query
2. Generate query embedding via Bedrock Titan v2
3. Load embeddings.json from S3 (cached in memory after first call)
4. Compute cosine similarity against all 59 chunks via NumPy
5. Select top-K results
6. Inject chunk text into Claude prompt
7. Stream grounded response to user

**Memory:** 59 chunks × 1024 dims × 4 bytes (float32) ≈ 240 KB.
Well within any container memory limit.

---

## Cost

| Component | Monthly Cost |
|-----------|-------------|
| S3 storage (1.3 MB) | ~$0.00003 |
| Bedrock Titan v2 (query embeddings) | ~$0.0001/query |
| Retrieval compute | $0 (in-process, no extra service) |
| **Total** | **~$0.03/month** |

No idle costs. No minimums. No instances.

---

## When to Upgrade

Move to dedicated vector infrastructure when:

- Corpus exceeds 10,000 chunks (latency >50ms)
- Metadata filtering needed pre-retrieval (not post)
- Hybrid lexical + vector search required
- Multi-tenant isolation needed

At that point, consider:
- pgvector (if already using PostgreSQL)
- OpenSearch Serverless (if already in AWS ecosystem)
- S3 Vectors (if staying serverless and sub-100ms latency acceptable)

---

## Key Principle

For small internal document corpora:

> Simplicity is a scalability feature.

A single JSON file in S3 with in-process NumPy retrieval is the correct
engineering choice. It costs nearly nothing, has zero operational overhead,
and is trivially debuggable. Upgrade when the data demands it, not before.
