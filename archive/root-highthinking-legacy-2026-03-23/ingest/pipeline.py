"""
数据入库管线（流水线并发版本）

架构：
  OCR 线程池（40 并发）  -->  embed_queue  -->  多 Embed 消费线程
  论文1..N OCR ──> Queue(maxsize=EMBED_QUEUE_SIZE) ──> EMBED_CONCURRENCY 个 Embed 线程
  每个 Embed 线程独立 API 客户端，分块+向量化+写 Chroma（Chroma 支持并发 upsert）。

约定：
  - 分块以「一篇文献」为单位：队列里每条是单篇论文的 markdown，消费线程内对该篇做 chunk_document → embed → add_chunks，不会跨篇混合。
  - OCR 单次 API 请求：config.OCR_PAGES_PER_BATCH 页图片（当前为 3 页）一次调用。
支持：
  - --start / --end 分批处理
  - skip_parsed：已解析的 PDF 跳过 OCR，直接进 Embed 队列
  - 已入库 DOI（Chroma 中已有）：跳过 embedding，重启不重复入库
"""

import json
import logging
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue

from tqdm import tqdm

import config
from ingest.pdf_parser import parse_pdf, extract_doi_from_filename
from ingest.chunker import chunk_document
from ingest.embedder import embed_texts, get_embedding_client
from ingest.vector_store import (
    get_or_create_collection,
    add_chunks,
    get_collection_count,
    get_indexed_dois,
)

logger = logging.getLogger(__name__)

# 中间结果缓存目录
CACHE_DIR = os.path.join(os.path.dirname(config.PAPERS_DIR), "cache")
PARSED_CACHE_DIR = os.path.join(CACHE_DIR, "parsed_markdown")

# 队列哨兵，表示 OCR 阶段全部完成
_SENTINEL = None


def ensure_cache_dirs():
    """确保缓存目录存在"""
    os.makedirs(PARSED_CACHE_DIR, exist_ok=True)


def get_parsed_cache_path(pdf_filename: str) -> str:
    """获取 PDF 解析结果的缓存路径"""
    stem = Path(pdf_filename).stem
    return os.path.join(PARSED_CACHE_DIR, f"{stem}.md")


# ── OCR 单篇处理（在线程池中执行） ──────────────────────────────

def _ocr_one_paper(
    pdf_file: str,
    papers_dir: str,
    parse_method: str,
    skip_parsed: bool,
) -> dict:
    """
    OCR 处理单篇论文，返回解析结果。
    在 OCR 线程池中并发执行。

    Returns:
        {"pdf_file": str, "markdown": str, "doi": str, "cached": bool, "error": str}
    """
    result = {
        "pdf_file": pdf_file,
        "markdown": "",
        "doi": extract_doi_from_filename(pdf_file),
        "cached": False,
        "error": "",
    }

    try:
        pdf_path = os.path.join(papers_dir, pdf_file)
        cache_path = get_parsed_cache_path(pdf_file)

        # 检查缓存
        if skip_parsed and os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                result["markdown"] = f.read()
            result["cached"] = True
            return result

        # 调用 OCR API
        markdown_text = parse_pdf(pdf_path, method=parse_method)

        # 保存缓存
        if markdown_text and markdown_text.strip():
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(markdown_text)

        result["markdown"] = markdown_text

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"OCR 失败: {pdf_file}: {e}")

    return result


# ── Embed+写库 消费线程 ──────────────────────────────────────

def _embed_consumer(
    embed_queue: Queue,
    collection,
    stats: dict,
    stats_lock: threading.Lock,
    pbar: tqdm,
):
    """
    从队列中取出 OCR 结果，执行分块 -> 向量化 -> 写库。
    每线程独立 embedding_client，避免连接池争用；Chroma 支持多线程 upsert。
    """
    embedding_client = get_embedding_client()
    while True:
        item = embed_queue.get()
        if item is _SENTINEL:
            embed_queue.task_done()
            break

        pdf_file = item["pdf_file"]
        markdown_text = item["markdown"]
        doi = item["doi"]

        try:
            if not markdown_text or not markdown_text.strip():
                with stats_lock:
                    stats["failed"] += 1
                pbar.update(1)
                embed_queue.task_done()
                continue

            # 分块（传入 embedding_func，超过 4000 token 的 section 做语义分块）
            def _embed_func(texts):
                return embed_texts(texts, client=embedding_client)

            chunks = chunk_document(
                markdown_text=markdown_text,
                doi=doi,
                title="",
                embedding_func=_embed_func,
            )

            if not chunks:
                logger.warning(f"分块结果为空: {pdf_file}")
                with stats_lock:
                    stats["failed"] += 1
                pbar.update(1)
                embed_queue.task_done()
                continue

            # 向量化
            chunk_texts = [c.text for c in chunks]
            embeddings = embed_texts(chunk_texts, client=embedding_client)

            # 过滤掉零向量（来自空文本或 400 错误的占位）
            valid_pairs = [
                (c, e) for c, e in zip(chunks, embeddings)
                if any(v != 0.0 for v in e[:3])  # 快速检查前 3 维
            ]
            if not valid_pairs:
                logger.warning(f"所有 chunk 向量化失败: {pdf_file}")
                with stats_lock:
                    stats["failed"] += 1
                pbar.update(1)
                embed_queue.task_done()
                continue

            valid_chunks, valid_embeddings = zip(*valid_pairs)

            # 写入 Chroma
            add_chunks(list(valid_chunks), list(valid_embeddings), collection=collection)

            with stats_lock:
                stats["total_chunks"] += len(valid_chunks)
                stats["embedded"] += 1

            logger.debug(f"入库完成: {pdf_file} -> {len(chunks)} chunks")

        except Exception as e:
            logger.error(f"Embed/写库失败: {pdf_file}: {e}")
            with stats_lock:
                stats["failed"] += 1

        pbar.update(1)
        embed_queue.task_done()


# ── 主管线 ──────────────────────────────────────────────────

def run_pipeline(
    papers_dir: str = None,
    parse_method: str = "vlm_api",
    skip_parsed: bool = True,
    max_papers: int = None,
    start: int = 0,
    end: int = None,
) -> dict:
    """
    运行流水线并发数据入库管线。

    Args:
        papers_dir: PDF 论文目录
        parse_method: PDF 解析方式
        skip_parsed: 是否跳过已解析/已入库的 PDF
        max_papers: 最多处理的论文数量（优先于 start/end）
        start: 起始索引（0-based，含）
        end: 结束索引（不含），None 表示到最后

    Returns:
        处理统计信息
    """
    if papers_dir is None:
        papers_dir = config.PAPERS_DIR

    ensure_cache_dirs()

    # 收集所有 PDF 文件（排序保证顺序稳定）
    all_pdf_files = sorted([
        f for f in os.listdir(papers_dir)
        if f.lower().endswith(".pdf")
    ])

    total_available = len(all_pdf_files)

    # 切片选取
    if max_papers:
        pdf_files = all_pdf_files[:max_papers]
    else:
        pdf_files = all_pdf_files[start:end]

    logger.info(
        f"总共 {total_available} 篇论文, "
        f"本次处理第 {start}-{start + len(pdf_files)} 篇 "
        f"(共 {len(pdf_files)} 篇)"
    )

    # 初始化
    collection = get_or_create_collection()
    indexed_dois = get_indexed_dois(collection)
    if indexed_dois:
        logger.info(f"已入库 DOI 数量: {len(indexed_dois)}，将跳过这些论文的 embedding")

    stats = {
        "total_available": total_available,
        "total_selected": len(pdf_files),
        "range": f"{start}-{start + len(pdf_files)}",
        "ocr_parsed": 0,
        "ocr_cached": 0,
        "embedded": 0,
        "skipped_indexed": 0,  # 已在 Chroma 中，跳过 embedding
        "failed": 0,
        "total_chunks": 0,
    }
    stats_lock = threading.Lock()

    # 队列：OCR 线程 -> 多 Embed 消费线程
    embed_queue = Queue(maxsize=config.EMBED_QUEUE_SIZE)
    embed_concurrency = config.EMBED_CONCURRENCY

    # 进度条（tqdm.update 线程安全）
    pbar = tqdm(total=len(pdf_files), desc="入库进度")

    # 启动多个 Embed 消费线程，每线程独立 API 客户端
    embed_threads = [
        threading.Thread(
            target=_embed_consumer,
            args=(embed_queue, collection, stats, stats_lock, pbar),
            daemon=True,
        )
        for _ in range(embed_concurrency)
    ]
    for t in embed_threads:
        t.start()

    # OCR 并发处理
    ocr_concurrency = config.OCR_CONCURRENCY
    with ThreadPoolExecutor(max_workers=ocr_concurrency) as ocr_pool:
        futures = {
            ocr_pool.submit(
                _ocr_one_paper, pdf_file, papers_dir, parse_method, skip_parsed
            ): pdf_file
            for pdf_file in pdf_files
        }

        for future in as_completed(futures):
            pdf_file = futures[future]
            try:
                result = future.result()

                with stats_lock:
                    if result["error"]:
                        stats["failed"] += 1
                        pbar.update(1)
                        continue
                    elif result["cached"]:
                        stats["ocr_cached"] += 1
                    else:
                        stats["ocr_parsed"] += 1

                # 已在向量库中的 DOI 不再 embedding，避免重启重复入库
                if result["doi"] and result["doi"] in indexed_dois:
                    with stats_lock:
                        stats["skipped_indexed"] += 1
                    pbar.update(1)
                    continue

                # 放入 Embed 队列
                embed_queue.put(result)

            except Exception as e:
                logger.error(f"OCR 线程异常: {pdf_file}: {e}")
                with stats_lock:
                    stats["failed"] += 1
                pbar.update(1)

    # 所有 OCR 完成，每个 Embed 消费者一个哨兵
    for _ in range(embed_concurrency):
        embed_queue.put(_SENTINEL)
    for t in embed_threads:
        t.join()
    pbar.close()

    stats["collection_total"] = get_collection_count(collection)
    logger.info(
        f"入库完成: 选取 {stats['total_selected']} 篇, "
        f"OCR新解析 {stats['ocr_parsed']} 篇, "
        f"缓存跳过 {stats['ocr_cached']} 篇, "
        f"已入库跳过 {stats['skipped_indexed']} 篇, "
        f"本次入库 {stats['embedded']} 篇, "
        f"共 {stats['total_chunks']} chunks, "
        f"失败 {stats['failed']} 篇, "
        f"向量库总量 {stats['collection_total']} chunks"
    )

    return stats


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    max_papers = None
    if len(sys.argv) > 1:
        max_papers = int(sys.argv[1])

    stats = run_pipeline(max_papers=max_papers)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
