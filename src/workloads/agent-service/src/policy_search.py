"""
In-process policy search using Bedrock Titan + S3 + NumPy.

Replaces Qdrant + dense-embedder. Embeddings are loaded from S3 once
and cached in memory. Cosine similarity is computed on the fly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import boto3
import numpy as np
from config import settings

log = logging.getLogger(__name__)

# In-memory cache
_embeddings_cache: list[dict[str, Any]] | None = None


def _bedrock_client():
    return boto3.client("bedrock-runtime", region_name=settings.bedrock_region)


def _s3_client():
    return boto3.client("s3", region_name=settings.aws_region)


def _embed_query(query: str) -> np.ndarray:
    """Generate embedding using Bedrock Titan."""
    client = _bedrock_client()
    body = json.dumps({"inputText": query})
    response = client.invoke_model(
        modelId=settings.bedrock_embed_model,
        body=body,
        accept="application/json",
        contentType="application/json",
    )
    payload = json.loads(response["body"].read())
    return np.array(payload["embedding"], dtype=np.float32)


def _load_embeddings() -> list[dict[str, Any]]:
    """Load embeddings from S3. Cached forever after first load."""
    global _embeddings_cache
    if _embeddings_cache is not None:
        return _embeddings_cache

    s3 = _s3_client()
    response = s3.get_object(Bucket=settings.embeddings_bucket, Key=settings.embeddings_key)
    loaded: list[dict[str, Any]] = json.loads(response["Body"].read())
    _embeddings_cache = loaded
    log.info(
        "Loaded %d policy embeddings from s3://%s/%s",
        len(loaded),
        settings.embeddings_bucket,
        settings.embeddings_key,
    )
    return loaded


def search_policies(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Return top-K policy chunks for a query."""
    query_vec = _embed_query(query)
    documents = _load_embeddings()

    if not documents:
        return []

    doc_matrix = np.array([doc["embedding"] for doc in documents], dtype=np.float32)

    # Cosine similarity
    scores = np.dot(doc_matrix, query_vec) / (
        np.linalg.norm(doc_matrix, axis=1) * np.linalg.norm(query_vec) + 1e-10
    )

    top_indices = np.argsort(-scores)[:top_k]

    results = []
    for idx in top_indices:
        doc = documents[idx].copy()
        doc["similarity"] = float(scores[idx])
        results.append(doc)

    return results


def warmup_cache() -> None:
    """Load embeddings into memory (blocking - use warmup_cache_async for async)."""
    _load_embeddings()


async def warmup_cache_async() -> None:
    """Load embeddings into memory without blocking the event loop."""
    await asyncio.to_thread(_load_embeddings)
