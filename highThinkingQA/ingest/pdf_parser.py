"""
PDF 解析模块
通过阿里云百炼 API 调用 qwen-vl-ocr 将 PDF 解析为结构化文本。

流程：
  1. 用 PyMuPDF (fitz) 将 PDF 每页渲染为 PNG 图片
  2. 将页面分批（每批 OCR_PAGES_PER_BATCH 页），多图合并为一次 API 请求
  3. 每次请求创建独立的 API 客户端实例
  4. 拼接所有批次的输出

限流策略：
  - 全局信号量控制同时在飞的请求数（config.OCR_MAX_CONCURRENT_REQUESTS）
  - 429/Connection error 时指数退避重试
"""

import base64
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from openai import OpenAI

import config

logger = logging.getLogger(__name__)

# ── 全局 OCR 限流信号量 ──
_ocr_semaphore: Optional[threading.Semaphore] = None
_semaphore_lock = threading.Lock()


def _get_ocr_semaphore() -> threading.Semaphore:
    """惰性初始化全局 OCR 信号量"""
    global _ocr_semaphore
    if _ocr_semaphore is None:
        with _semaphore_lock:
            if _ocr_semaphore is None:
                _ocr_semaphore = threading.Semaphore(config.OCR_MAX_CONCURRENT_REQUESTS)
                logger.info(
                    f"OCR 全局信号量初始化: 最大并发请求 = {config.OCR_MAX_CONCURRENT_REQUESTS}"
                )
    return _ocr_semaphore


def pdf_to_page_images(pdf_path: str, dpi: int = 200) -> list[bytes]:
    """
    将 PDF 每页渲染为 PNG 图片。

    Args:
        pdf_path: PDF 文件路径
        dpi: 渲染分辨率

    Returns:
        每页的 PNG 图片 bytes 列表
    """
    doc = fitz.open(pdf_path)
    images = []
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)

    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(matrix=matrix)
        img_bytes = pix.tobytes("png")
        images.append(img_bytes)

    doc.close()
    return images


def _call_ocr_batch(
    image_batch: list[bytes],
    batch_start_page: int,
    batch_end_page: int,
    pdf_name: str = "",
) -> str:
    """
    将多页图片合并为一次 OCR API 请求。
    每次调用创建独立的 API 客户端实例。

    Args:
        image_batch: PNG 图片 bytes 列表（最多 OCR_PAGES_PER_BATCH 张）
        batch_start_page: 批次起始页码（从 1 开始，用于日志）
        batch_end_page: 批次结束页码（含，用于日志）
        pdf_name: PDF 文件名（用于日志）

    Returns:
        该批次所有页面的文本内容
    """
    # 每次请求创建独立 client
    client = OpenAI(
        api_key=config.OCR_API_KEY,
        base_url=config.OCR_BASE_URL,
    )

    # 构建多图 content
    content = []
    for img_bytes in image_batch:
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{img_base64}",
            },
        })

    content.append({
        "type": "text",
        "text": (
            "请仅输出图像中的所有文本内容。"
            "保留文献的原始格式结构，包括标题层级、段落、列表、表格、公式等。"
        ),
    })

    response = client.chat.completions.create(
        model=config.OCR_MODEL,
        messages=[{"role": "user", "content": content}],
    )

    # 记录 token 使用量
    if hasattr(response, "usage") and response.usage:
        u = response.usage
        logger.info(
            f"  [{pdf_name}] 页 {batch_start_page}-{batch_end_page} "
            f"token 用量: input={u.prompt_tokens}, output={u.completion_tokens}, "
            f"total={u.total_tokens}"
        )

    return response.choices[0].message.content


def _parse_batch_with_ratelimit(
    image_batch: list[bytes],
    batch_start_page: int,
    batch_end_page: int,
    total_pages: int,
    pdf_name: str = "",
) -> str:
    """
    带限流和重试的批次 OCR。

    Args:
        image_batch: PNG 图片 bytes 列表
        batch_start_page: 批次起始页码
        batch_end_page: 批次结束页码
        total_pages: 论文总页数
        pdf_name: PDF 文件名

    Returns:
        该批次的文本内容
    """
    sem = _get_ocr_semaphore()
    max_retries = config.OCR_MAX_RETRIES
    retry_base = config.OCR_RETRY_BASE

    for attempt in range(max_retries + 1):
        sem.acquire()
        try:
            text = _call_ocr_batch(
                image_batch=image_batch,
                batch_start_page=batch_start_page,
                batch_end_page=batch_end_page,
                pdf_name=pdf_name,
            )
            logger.debug(
                f"  [{pdf_name}] 页 {batch_start_page}-{batch_end_page}/{total_pages} 完成"
            )
            return text

        except Exception as e:
            error_msg = str(e)
            is_rate_limit = "429" in error_msg or "rate limit" in error_msg.lower()
            is_connection = "connection" in error_msg.lower()

            if attempt >= max_retries:
                logger.warning(
                    f"  [{pdf_name}] 页 {batch_start_page}-{batch_end_page} "
                    f"解析失败 ({max_retries + 1}次尝试后放弃): {e}"
                )
                return f"[Pages {batch_start_page}-{batch_end_page}: parse failed]"

            if is_rate_limit:
                wait_time = retry_base * (2 ** attempt)
            elif is_connection:
                wait_time = retry_base * (2 ** attempt)
            else:
                wait_time = 2 ** attempt

            logger.warning(
                f"  [{pdf_name}] 页 {batch_start_page}-{batch_end_page} "
                f"解析失败, {wait_time}s 后重试 ({attempt + 1}/{max_retries + 1}): {e}"
            )
            time.sleep(wait_time)

        finally:
            sem.release()

    return f"[Pages {batch_start_page}-{batch_end_page}: parse failed]"


def parse_pdf_via_vlm_api(pdf_path: str) -> str:
    """
    通过百炼 qwen-vl-ocr API 解析 PDF 为文本。
    每 OCR_PAGES_PER_BATCH 页合并为一次请求，减少 API 调用次数。
    每次请求创建独立的 API 客户端实例。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        解析后的文本（所有批次拼接）
    """
    pdf_name = Path(pdf_path).stem
    batch_size = config.OCR_PAGES_PER_BATCH

    # Step 1: PDF -> 每页 PNG 图片
    page_images = pdf_to_page_images(pdf_path)
    total_pages = len(page_images)
    logger.info(f"[{pdf_name}] PDF 共 {total_pages} 页, 每批 {batch_size} 页")

    # Step 2: 分批调用 OCR
    batch_results = []
    for i in range(0, total_pages, batch_size):
        batch = page_images[i:i + batch_size]
        batch_start = i + 1
        batch_end = min(i + batch_size, total_pages)

        text = _parse_batch_with_ratelimit(
            image_batch=batch,
            batch_start_page=batch_start,
            batch_end_page=batch_end,
            total_pages=total_pages,
            pdf_name=pdf_name,
        )
        batch_results.append(text)

    # Step 3: 拼接所有批次
    return "\n\n---\n\n".join(batch_results)


def parse_pdf(pdf_path: str, method: str = "vlm_api") -> str:
    """
    解析 PDF 文件为结构化文本。

    Args:
        pdf_path: PDF 文件路径
        method: 解析方式（目前仅支持 "vlm_api"）

    Returns:
        解析后的文本
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    logger.info(f"解析 PDF: {pdf_path} (模型: {config.OCR_MODEL})")

    return parse_pdf_via_vlm_api(pdf_path)


def extract_doi_from_filename(filename: str) -> str:
    """
    从文件名中提取 DOI。
    文件名格式如: 10.1002_adfm.201705838.pdf
    其中 _ 替换回 /

    Args:
        filename: PDF 文件名

    Returns:
        DOI 字符串
    """
    name = Path(filename).stem
    parts = name.split("_", 1)
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    return name
