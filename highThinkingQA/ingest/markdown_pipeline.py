"""
Markdown 文献入库管线

从指定目录读取 .md 原文，按标题结构分块（可配置 1/2/3 级），
超长块用语义分块兜底，使用本地 Qwen3-Embedding-8B (4096 维) 向量化后写入 Chroma。

用法（建议在 qwenvllm conda 环境中运行）:
  python ingest/markdown_pipeline.py \\
    --md-dir /home/cqy/纯lfp的md格式/parsed_markdown \\
    --model-path /home/cqy/qwen3_embedding_8b \\
    --vectordb-dir /home/cqy/纯lfp的md格式/vectordb \\
    --header-level 2 \\
    --max-papers 10
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

from tqdm import tqdm

import config
from ingest.chunker import chunk_document, count_tokens
from ingest.local_embedder import (
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL_PATH,
    embed_texts_local,
    make_embed_func,
)
from ingest.vector_store import add_chunks, get_collection_count, get_indexed_dois, get_or_create_collection

logger = logging.getLogger(__name__)

_DOI_IN_TEXT_RE = re.compile(
    r"(?:DOI|doi)\s*[：:]\s*(10\.\S+)",
    re.IGNORECASE,
)


def extract_doc_id_from_filename(filename: str) -> str:
    """从 md 文件名提取文档 ID（优先 DOI 格式，否则用 stem）。"""
    stem = Path(filename).stem
    if stem.startswith("10.") and "_" in stem:
        prefix, suffix = stem.split("_", 1)
        return f"{prefix}/{suffix}"
    return stem


def extract_doi_from_markdown(text: str) -> str:
    match = _DOI_IN_TEXT_RE.search(text[:4000])
    if not match:
        return ""
    doi = match.group(1).strip().rstrip(").,;]")
    return doi


def extract_title_from_markdown(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("<!--"):
            return stripped[:200]
    return ""


def _get_collection(chroma_persist_dir: str, collection_name: str):
    """使用自定义路径获取 Chroma collection（不依赖全局 config 缓存）。"""
    import chromadb

    os.makedirs(chroma_persist_dir, exist_ok=True)
    client = chromadb.PersistentClient(path=chroma_persist_dir)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def run_markdown_pipeline(
    *,
    md_dir: str,
    vectordb_dir: str,
    model_path: str = DEFAULT_MODEL_PATH,
    collection_name: str = "lfp_markdown_qwen3_4096",
    header_level: int = 3,
    dimensions: int = DEFAULT_DIMENSIONS,
    max_chunk_tokens: int | None = None,
    semantic_min_tokens: int | None = None,
    semantic_max_tokens: int | None = None,
    embed_batch_size: int = 4,
    max_input_tokens: int = 8192,
    skip_indexed: bool = True,
    start: int = 0,
    end: int | None = None,
    max_papers: int | None = None,
) -> dict:
    md_path = Path(md_dir)
    if not md_path.is_dir():
        raise FileNotFoundError(f"Markdown 目录不存在: {md_dir}")

    md_files = sorted(p.name for p in md_path.glob("*.md"))
    if start:
        md_files = md_files[start:]
    if end is not None:
        md_files = md_files[: max(0, end - start)]
    if max_papers is not None:
        md_files = md_files[:max_papers]

    if max_chunk_tokens is not None:
        config.MAX_CHUNK_TOKENS = max_chunk_tokens
    if semantic_min_tokens is not None:
        config.SEMANTIC_CHUNK_MIN_TOKENS = semantic_min_tokens
    if semantic_max_tokens is not None:
        config.SEMANTIC_CHUNK_MAX_TOKENS = semantic_max_tokens

    collection = _get_collection(vectordb_dir, collection_name)
    indexed_dois = get_indexed_dois(collection) if skip_indexed else set()
    embed_func = make_embed_func(
        model_path=model_path,
        dimensions=dimensions,
        batch_size=embed_batch_size,
        max_input_tokens=max_input_tokens,
    )

    stats = {
        "total_files": len(md_files),
        "embedded": 0,
        "skipped_indexed": 0,
        "skipped_empty": 0,
        "failed": 0,
        "total_chunks": 0,
        "elapsed_sec": 0.0,
    }
    t0 = time.time()

    for md_file in tqdm(md_files, desc="Ingest markdown"):
        file_path = md_path / md_file
        try:
            markdown = file_path.read_text(encoding="utf-8", errors="ignore").strip()
            if not markdown:
                stats["skipped_empty"] += 1
                continue

            doc_id = extract_doi_from_markdown(markdown) or extract_doc_id_from_filename(md_file)
            if skip_indexed and doc_id in indexed_dois:
                stats["skipped_indexed"] += 1
                continue

            title = extract_title_from_markdown(markdown)
            chunks = chunk_document(
                markdown,
                doi=doc_id,
                title=title,
                embedding_func=embed_func,
                max_header_level=header_level,
            )
            if not chunks:
                stats["skipped_empty"] += 1
                continue

            chunk_texts = [c.text for c in chunks]
            embeddings = embed_texts_local(
                chunk_texts,
                model_path=model_path,
                dimensions=dimensions,
                batch_size=embed_batch_size,
                max_input_tokens=max_input_tokens,
            )

            valid_pairs = [
                (chunk, emb)
                for chunk, emb in zip(chunks, embeddings)
                if any(v != 0.0 for v in emb)
            ]
            if not valid_pairs:
                stats["skipped_empty"] += 1
                continue

            valid_chunks, valid_embeddings = zip(*valid_pairs)
            add_chunks(list(valid_chunks), list(valid_embeddings), collection=collection)
            indexed_dois.add(doc_id)
            stats["embedded"] += 1
            stats["total_chunks"] += len(valid_chunks)
            logger.info(
                "已入库 %s: %s chunks, ~%s tokens",
                md_file,
                len(valid_chunks),
                sum(c.token_count or count_tokens(c.text) for c in valid_chunks),
            )
        except Exception as exc:
            stats["failed"] += 1
            logger.exception("处理失败 %s: %s", md_file, exc)

    stats["elapsed_sec"] = round(time.time() - t0, 1)
    stats["collection_count"] = get_collection_count(collection)
    return stats


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Markdown 文献结构分块 + 本地 Qwen3 向量化入库")
    parser.add_argument(
        "--md-dir",
        default="/home/cqy/纯lfp的md格式/parsed_markdown",
        help="Markdown 文献目录",
    )
    parser.add_argument(
        "--vectordb-dir",
        default="/home/cqy/纯lfp的md格式/vectordb",
        help="Chroma 持久化目录",
    )
    parser.add_argument(
        "--model-path",
        default=DEFAULT_MODEL_PATH,
        help="本地 Qwen3-Embedding 模型目录",
    )
    parser.add_argument(
        "--collection-name",
        default="lfp_markdown_qwen3_4096",
        help="Chroma collection 名称",
    )
    parser.add_argument(
        "--header-level",
        type=int,
        choices=[1, 2, 3],
        default=3,
        help="结构分块使用的最大标题级别：1=#, 2=##, 3=###",
    )
    parser.add_argument(
        "--dimensions",
        type=int,
        default=DEFAULT_DIMENSIONS,
        help="向量维度（Qwen3-8B 最大 4096）",
    )
    parser.add_argument("--max-chunk-tokens", type=int, default=4000, help="结构块 token 上限")
    parser.add_argument("--semantic-min-tokens", type=int, default=2000, help="语义分块最小块")
    parser.add_argument("--semantic-max-tokens", type=int, default=4000, help="语义分块最大块")
    parser.add_argument("--embed-batch-size", type=int, default=4, help="本地模型推理 batch 大小")
    parser.add_argument("--max-input-tokens", type=int, default=8192, help="单条文本最大 token")
    parser.add_argument("--start", type=int, default=0, help="从第 N 篇开始（0-based）")
    parser.add_argument("--end", type=int, default=None, help="处理到第 N 篇（不含）")
    parser.add_argument("--max-papers", type=int, default=None, help="最多处理篇数")
    parser.add_argument("--no-skip-indexed", action="store_true", help="不跳过已入库 DOI")
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    stats = run_markdown_pipeline(
        md_dir=args.md_dir,
        vectordb_dir=args.vectordb_dir,
        model_path=args.model_path,
        collection_name=args.collection_name,
        header_level=args.header_level,
        dimensions=args.dimensions,
        max_chunk_tokens=args.max_chunk_tokens,
        semantic_min_tokens=args.semantic_min_tokens,
        semantic_max_tokens=args.semantic_max_tokens,
        embed_batch_size=args.embed_batch_size,
        max_input_tokens=args.max_input_tokens,
        skip_indexed=not args.no_skip_indexed,
        start=args.start,
        end=args.end,
        max_papers=args.max_papers,
    )
    print("入库完成:", stats)
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
