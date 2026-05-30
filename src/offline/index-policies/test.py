"""
Retrieve policy chunks using brute-force cosine similarity.

Loads embeddings.json from S3 (cached in memory), embeds the query
with Titan Embeddings v2, computes cosine similarity against all chunks
via NumPy, and returns top-K results.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from typing import Any

import boto3
import numpy as np
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

AWS_REGION: str = os.getenv("AWS_REGION", "ap-south-1")
BEDROCK_REGION: str = os.getenv("BEDROCK_EMBED_REGION", AWS_REGION)
BEDROCK_MODEL_ID: str = os.getenv("BEDROCK_MODEL_ID", "amazon.titan-embed-text-v2:0")
S3_BUCKET: str = os.getenv("S3_BUCKET", "").strip()
S3_KEY: str = os.getenv("S3_KEY", "embeddings.json").strip()
TOP_K: int = max(1, int(os.getenv("TOP_K", "5")))
RETRY_ATTEMPTS: int = max(1, int(os.getenv("RETRY_ATTEMPTS", "3")))
RETRY_SLEEP_SECONDS: float = float(os.getenv("RETRY_SLEEP_SECONDS", "1.0"))

# In-memory cache for embeddings
_embeddings_cache: list[dict[str, Any]] | None = None


def get_bedrock_client() -> boto3.client:
    return boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)


def get_s3_client() -> boto3.client:
    return boto3.client("s3", region_name=AWS_REGION)


def embed_query(query: str) -> np.ndarray:
    """Embed a single query using Titan Embeddings v2."""
    bedrock = get_bedrock_client()
    body = json.dumps({"inputText": query})

    last_error: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = bedrock.invoke_model(
                modelId=BEDROCK_MODEL_ID,
                body=body,
                accept="application/json",
                contentType="application/json",
            )
            payload = json.loads(response["body"].read())
            embedding = payload.get("embedding")
            if not isinstance(embedding, list):
                raise RuntimeError("Bedrock returned unexpected embedding payload")
            return np.array(embedding, dtype=np.float32)
        except (ClientError, BotoCoreError, RuntimeError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "Query embedding attempt %s/%s failed: %s",
                attempt,
                RETRY_ATTEMPTS,
                exc,
            )
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS * attempt)

    raise RuntimeError(f"Failed to embed query after {RETRY_ATTEMPTS} attempts: {last_error}")


def load_embeddings() -> list[dict[str, Any]]:
    """Load embeddings from S3. Cached in memory after first call."""
    global _embeddings_cache

    if _embeddings_cache is not None:
        return _embeddings_cache

    if not S3_BUCKET:
        raise ValueError("S3_BUCKET is required")

    s3 = get_s3_client()
    response = s3.get_object(Bucket=S3_BUCKET, Key=S3_KEY)
    _embeddings_cache = json.loads(response["Body"].read())

    if _embeddings_cache is None:
        raise RuntimeError("Failed to load embeddings from S3")

    logger.info("Loaded %s embeddings from s3://%s/%s", len(_embeddings_cache), S3_BUCKET, S3_KEY)
    return _embeddings_cache


def cosine_similarity(query_vec: np.ndarray, doc_vecs: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between query vector and all document vectors."""
    query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    doc_norms = doc_vecs / (np.linalg.norm(doc_vecs, axis=1, keepdims=True) + 1e-10)
    return np.dot(doc_norms, query_norm)


def retrieve(query: str, top_k: int = TOP_K) -> list[dict[str, Any]]:
    """Full retrieval pipeline: embed → load → similarity → top-K."""
    query_vec = embed_query(query)
    documents = load_embeddings()

    doc_matrix = np.array([doc["embedding"] for doc in documents], dtype=np.float32)
    scores = cosine_similarity(query_vec, doc_matrix)

    if top_k >= len(scores):
        top_indices = list(range(len(scores)))
    else:
        top_indices = np.argpartition(-scores, top_k)[:top_k]
        top_indices = top_indices[np.argsort(-scores[top_indices])]

    results: list[dict[str, Any]] = []
    for idx in top_indices:
        doc = dict(documents[idx])
        doc["distance"] = float(1.0 - scores[idx])
        doc["similarity"] = float(scores[idx])
        results.append(doc)

    return results


def format_hit(hit: dict[str, Any], rank: int) -> str:
    policy_name = hit.get("policy_name", "")
    heading_path = hit.get("heading_path", "")
    similarity = hit.get("similarity", 0)
    chunk_text = hit.get("chunk_text", "")

    preview = chunk_text.replace("\n", " ").strip()
    if len(preview) > 300:
        preview = preview[:300] + "..."

    return (
        f"[{rank}] chunk_id={hit.get('chunk_id')}\n"
        f"    policy: {policy_name}\n"
        f"    section: {heading_path}\n"
        f"    similarity: {similarity:.4f}\n"
        f"    text: {preview}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Retrieve policy chunks via brute-force cosine similarity"
    )
    parser.add_argument("query", help="User question")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    hits = retrieve(args.query, top_k=args.top_k)

    print(f"\nQUERY: {args.query}\n")
    print(f"TOP MATCHES (brute-force cosine similarity, {len(hits)} results):\n")
    for i, hit in enumerate(hits, start=1):
        print(format_hit(hit, i))
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
