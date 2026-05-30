# Serverless RAG for Company Policy Documents

## Overview

Lightweight serverless RAG system for internal company policy documents using Amazon S3 and AWS Lambda. No vector database required.

**Optimized for:**
- <1,000 document chunks
- Low operational overhead
- Low cost
- Agentic workflows

---

## Why This Architecture

For corpora under ~1,000 chunks, ANN indexes provide little benefit while adding complexity. Retrieval latency is negligible with brute-force search. The actual bottlenecks are LLM inference, chunk quality, and prompt quality.

Serverless RAG architectures using Lambda, S3, and API Gateway automate scaling, reduce management overhead, and implement a cost-effective, pay-per-use model. For small-scale deployments, dedicated vector databases introduce unnecessary operational complexity.

---

## Architecture

```
User → API Gateway → Lambda → LLM → Response
                         ↓
                      S3 (embeddings + chunks)
```

**Storage layout:**
- `s3://bucket/raw/` - Original PDFs
- `s3://bucket/chunks/` - Chunked text JSON
- `s3://bucket/embeddings/` - Vector embeddings

Source: Build secure RAG applications with AWS serverless data lakes

---

## S3 Vectors Option

Amazon S3 Vectors is the first cloud object store with native support to store and query vector data, generally available as of December 2025. It provides:

- Serverless operation with no infrastructure to provision
- Metadata filtering with up to 50 keys
- Up to 90% cost reduction compared to specialized vector databases
- Sub-100ms query latency for frequent queries
- Up to 2 billion vectors per single index, 20 trillion vectors per bucket

S3 Vectors is natively integrated with Amazon Bedrock Knowledge Bases, making it suitable for RAG applications requiring cost-optimized vector storage without infrastructure management.

---

## Retrieval Strategy

1. User submits question
2. Lambda generates query embedding
3. Loads embeddings file from S3
4. Computes cosine similarity against all chunks
5. Selects top-K chunks
6. Injects into LLM prompt

**Memory calculation:** 1,000 embeddings × 1536 dimensions × 4 bytes = ~6 MB, well within Lambda limits.

Source: AWS Lambda Pricing

---

## Cost Comparison

| Component | Cost |
|-----------|------|
| S3 storage (1,000 vectors) | Pay per GB stored |
| Lambda (10K queries/month) | Free tier covers 1M requests |
| S3 Vectors query | Pay per query, no idle minimums |

S3 Vectors is the only option with zero idle cost, making it suitable for development, testing, and cost-sensitive production workloads. OpenSearch Serverless has a minimum monthly cost of approximately $700 even when idle, representing a common cost surprise for teams. AWS claims S3 Vectors can reduce total vector storage and query costs by up to 90% compared to specialized vector database solutions.

Sources:
- Amazon S3 Pricing
- AWS Lambda Pricing

---

## When to Upgrade

Move to dedicated vector infrastructure when:

- Corpus exceeds 10K-100K chunks
- Retrieval latency becomes measurable (>100ms)
- Hybrid lexical/vector search required
- Multi-tenant isolation needed

For use cases requiring faster query performance (10ms latency) or advanced search capabilities such as hybrid search and aggregations, you can migrate vector data from an S3 vector index to OpenSearch Serverless.

---

## Recommended Stack

- AWS Lambda (compute)
- Amazon S3 (storage)
- Amazon Bedrock or OpenAI (embeddings + LLM)
- LangGraph (optional agent orchestration)

Source: AWS serverless RAG reference architecture

---

## Key Principle

For small internal document corpora, simplicity is a scalability feature. A minimal, debuggable, serverless system is the correct engineering choice. S3 Vectors provides the foundation for such an approach, delivering serverless vector search at up to 90% lower cost than traditional vector databases while maintaining sub-second query performance.