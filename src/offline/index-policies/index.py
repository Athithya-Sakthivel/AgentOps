"""
Index markdown policy documents into S3 as a single embeddings JSON file.

Design goals:
- No vector database
- Brute-force cosine similarity at query time (<5ms for <10K chunks)
- Single JSON file in S3 — simple, debuggable, cheap
- Amazon Titan Embeddings v2 (1024-dim float32)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

MARKDOWN_EXTS: set[str] = {".md", ".markdown", ".mdown"}

AWS_REGION: str = os.getenv("AWS_REGION", "ap-south-1")
BEDROCK_REGION: str = os.getenv("BEDROCK_EMBED_REGION", AWS_REGION)
BEDROCK_MODEL_ID: str = os.getenv("BEDROCK_MODEL_ID", "amazon.titan-embed-text-v2:0")

S3_BUCKET: str = os.getenv("S3_BUCKET", "").strip()
S3_KEY: str = os.getenv("S3_KEY", "embeddings.json").strip()

BATCH_SIZE: int = max(1, int(os.getenv("BATCH_SIZE", "32")))
MAX_CHUNK_CHARS: int = max(256, int(os.getenv("MAX_CHUNK_CHARS", "1800")))

RETRY_ATTEMPTS: int = max(1, int(os.getenv("RETRY_ATTEMPTS", "3")))
RETRY_SLEEP_SECONDS: float = float(os.getenv("RETRY_SLEEP_SECONDS", "1.0"))


HEADING_RE: re.Pattern[str] = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


# ---------------------------------------------------------------------------
# Markdown parsing and chunking
# ---------------------------------------------------------------------------


def extract_policy_name(text: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else fallback


def parse_markdown_sections(text: str) -> list[dict[str, Any]]:
    """Parse markdown into a hierarchy of sections with heading paths."""
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []

    heading_path: list[str] = []
    current_level = 0
    current_body_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(current_body_lines).strip()
        if body:
            sections.append(
                {
                    "heading_path": list(heading_path),
                    "content": body,
                    "level": current_level,
                }
            )
        current_body_lines.clear()

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            flush()
            hashes, title = match.groups()
            level = len(hashes)
            title = title.strip()

            if level == 1:
                heading_path = [title]
            else:
                heading_path = heading_path[: level - 1]
                heading_path.append(title)

            current_level = level
            continue

        current_body_lines.append(line)

    flush()
    return sections


def split_text(text: str, max_chars: int) -> list[str]:
    """Split text into chunks no larger than max_chars. Prefers paragraph boundaries."""
    cleaned = text.strip()
    if not cleaned:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if not paragraphs:
        return [cleaned[:max_chars]]

    chunks: list[str] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if buffer:
            chunks.append("\n\n".join(buffer).strip())
            buffer.clear()

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            flush_buffer()
            for i in range(0, len(paragraph), max_chars):
                part = paragraph[i : i + max_chars].strip()
                if part:
                    chunks.append(part)
            continue

        candidate = "\n\n".join([*buffer, paragraph]).strip()
        if candidate and len(candidate) <= max_chars:
            buffer.append(paragraph)
        else:
            flush_buffer()
            buffer.append(paragraph)

    flush_buffer()
    return chunks


def extract_tags(content: str) -> list[str]:
    tags: set[str] = set()
    low = content.lower()

    if any(w in low for w in ("refund", "money back", "reimburse")):
        tags.add("refunds")
    if any(w in low for w in ("return", "replacement", "reverse pickup")):
        tags.add("returns")
    if any(w in low for w in ("cancel", "cancellation")):
        tags.add("cancellation")
    if any(w in low for w in ("delivery", "shipping", "courier", "dispatch")):
        tags.add("delivery")
    if any(w in low for w in ("payment", "upi", "card", "cash on delivery", "cod", "wallet")):
        tags.add("payments")
    if any(w in low for w in ("warranty", "repair", "service center", "defect", "defective")):
        tags.add("warranty")
    if any(w in low for w in ("complaint", "grievance", "escalat", "nodal")):
        tags.add("escalation")

    return sorted(tags)


def build_chunks(md_text: str, source_path: Path) -> list[dict[str, Any]]:
    policy_name = extract_policy_name(md_text, fallback=source_path.stem.replace("_", " ").title())
    sections = parse_markdown_sections(md_text)

    chunks: list[dict[str, Any]] = []
    for section in sections:
        heading_path = section["heading_path"]
        content = section["content"].strip()
        if len(content) < 20:
            continue

        section_title = heading_path[-1] if heading_path else ""
        heading_path_str = " > ".join(heading_path) if heading_path else policy_name

        for local_index, chunk_text in enumerate(split_text(content, MAX_CHUNK_CHARS)):
            if len(chunk_text.strip()) < 20:
                continue

            chunks.append(
                {
                    "chunk_id": hashlib.sha256(
                        f"{source_path}\x1f{local_index}\x1f{heading_path_str}".encode()
                    ).hexdigest()[:16],
                    "policy_name": policy_name,
                    "source_path": str(source_path),
                    "section_title": section_title,
                    "heading_path": heading_path_str,
                    "chunk_index": local_index,
                    "chunk_text": chunk_text,
                    "tags": extract_tags(chunk_text),
                    "embed_text": (
                        f"Policy: {policy_name}\nSection: {heading_path_str}\n{chunk_text}"
                    ),
                }
            )

    return chunks


# ---------------------------------------------------------------------------
# AWS clients and embedding helpers
# ---------------------------------------------------------------------------


def get_bedrock_client() -> boto3.client:
    return boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)


def get_s3_client() -> boto3.client:
    return boto3.client("s3", region_name=AWS_REGION)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts one at a time using Titan Embeddings v2."""
    if not texts:
        return []

    bedrock = get_bedrock_client()
    embeddings: list[list[float]] = []

    for text in texts:
        body = json.dumps({"inputText": text})

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
                embeddings.append([float(x) for x in embedding])
                break
            except (ClientError, BotoCoreError, RuntimeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "Embedding attempt %s/%s failed: %s",
                    attempt,
                    RETRY_ATTEMPTS,
                    exc,
                )
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_SLEEP_SECONDS * attempt)
        else:
            raise RuntimeError(
                f"Failed to embed text after {RETRY_ATTEMPTS} attempts: {last_error}"
            )

    return embeddings


# ---------------------------------------------------------------------------
# Main indexing flow
# ---------------------------------------------------------------------------


def collect_markdown_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() in MARKDOWN_EXTS else []
    return sorted(
        p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in MARKDOWN_EXTS
    )


def index_policies(input_path: str, s3_bucket: str, s3_key: str) -> int:
    if not s3_bucket:
        raise ValueError("S3_BUCKET is required (set via --s3-bucket or env var)")

    root = Path(input_path)
    if not root.exists():
        raise FileNotFoundError(f"Input path not found: {input_path}")

    files = collect_markdown_files(root)
    if not files:
        logger.info("No markdown files found under %s", root)
        return 0

    # Step 1: Build all chunks
    all_chunks: list[dict[str, Any]] = []
    for file_path in files:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        chunks = build_chunks(text, file_path)
        all_chunks.extend(chunks)

    if not all_chunks:
        logger.info("No indexable chunks found.")
        return 0

    # Step 2: Embed all chunks
    total = len(all_chunks)
    logger.info(
        "Embedding %s chunks from %s markdown files with %s",
        total,
        len(files),
        BEDROCK_MODEL_ID,
    )

    for i in range(0, total, BATCH_SIZE):
        batch = all_chunks[i : i + BATCH_SIZE]
        texts = [chunk["embed_text"] for chunk in batch]
        new_embeddings = embed_texts(texts)

        for chunk, embedding in zip(batch, new_embeddings, strict=False):
            chunk["embedding"] = embedding

        logger.info("Embedded %s/%s chunks", min(i + len(batch), total), total)

    # Step 3: Upload single JSON file to S3
    output: list[dict[str, Any]] = []
    for chunk in all_chunks:
        output.append(
            {
                "chunk_id": chunk["chunk_id"],
                "policy_name": chunk["policy_name"],
                "section_title": chunk["section_title"],
                "heading_path": chunk["heading_path"],
                "chunk_text": chunk["chunk_text"],
                "tags": chunk["tags"],
                "embedding": chunk["embedding"],
            }
        )

    s3 = get_s3_client()
    s3.put_object(
        Bucket=s3_bucket,
        Key=s3_key,
        Body=json.dumps(output, ensure_ascii=False),
        ContentType="application/json",
    )

    file_size_mb = len(json.dumps(output)) / (1024 * 1024)
    print(
        json.dumps(
            {
                "indexed": total,
                "files": len(files),
                "s3_bucket": s3_bucket,
                "s3_key": s3_key,
                "model_id": BEDROCK_MODEL_ID,
                "file_size_mb": round(file_size_mb, 2),
            },
            ensure_ascii=False,
        )
    )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Index policy markdown files → embeddings.json in S3 (brute-force RAG)"
    )
    parser.add_argument("input", help="Path to a .md file or directory containing .md files")
    parser.add_argument("--s3-bucket", default=S3_BUCKET, help="S3 bucket for embeddings file")
    parser.add_argument("--s3-key", default=S3_KEY, help="S3 object key (default: embeddings.json)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    return index_policies(args.input, args.s3_bucket, args.s3_key)


if __name__ == "__main__":
    raise SystemExit(main())
