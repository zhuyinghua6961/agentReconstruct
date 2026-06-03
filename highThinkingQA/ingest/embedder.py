"""
向量化模块
调用阿里云百炼 text-embedding-v4 (2048维) 进行批量向量化。
使用 OpenAI 兼容 API 格式。

安全措施：
  - 禁用 SDK 内置重试，避免与手动重试叠加导致连接雪崩
  - 全局信号量限制同时在飞的 API 请求数
  - 文本截断安全网，防止超过 8192 token 输入上限
  - 400 错误（InvalidParameter）不重试，直接跳过该 batch 中的问题文本
"""

import logging
import random
import threading
import time
from typing import Optional

import tiktoken

import config
from agent_core.openai_compat import OpenAICompatibleEmbeddingClient
from agent_core.thinking import resolve_auth_mode
from agent_core.upstream_auth_logging import (
    log_upstream_auth_failure,
    log_upstream_auth_success_once,
)

logger = logging.getLogger(__name__)

# 百炼 text-embedding-v4 单次最多 10 条
BATCH_SIZE = config.HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE

# 全局信号量：限制跨所有线程的并发 API 请求数，防止连接雪崩
_embed_semaphore = threading.Semaphore(config.HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS)

# token 编码器（与 chunker 一致）
_encoder = tiktoken.get_encoding(config.TIKTOKEN_ENCODING)


def _require_api_key(*, api_key: str, env_name: str) -> str:
    value = str(api_key or "").strip()
    if value:
        return value
    raise RuntimeError(f"{env_name} is not configured")


def _embedding_auth_mode() -> str:
    return str(getattr(config, "HIGHTHINKINGQA_EMBEDDING_AUTH_MODE", "bearer") or "bearer").strip()


def get_embedding_client() -> OpenAICompatibleEmbeddingClient:
    """
    获取 Embedding API 客户端。
    max_retries=0 禁用 SDK 内置重试（我们自己做重试控制）。
    """
    auth_mode = _embedding_auth_mode()
    api_key = str(config.EMBEDDING_API_KEY or "").strip()
    if resolve_auth_mode(auth_mode) != "none":
        api_key = _require_api_key(api_key=api_key, env_name="HIGHTHINKINGQA_EMBEDDING_API_KEY")
    return OpenAICompatibleEmbeddingClient(
        api_key=api_key,
        auth_mode=auth_mode,
        base_url=config.EMBEDDING_BASE_URL,
        max_retries=0,
        timeout_seconds=30.0,
    )


def _truncate_text(text: str, max_tokens: int = None) -> str:
    """
    如果文本超过 max_tokens，截断到安全长度。
    作为防御性兜底（正常情况下 chunker 已保证 <= 2048 tokens）。
    """
    if max_tokens is None:
        max_tokens = config.HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS
    tokens = _encoder.encode(text)
    if len(tokens) <= max_tokens:
        return text
    logger.warning(
        f"文本过长 ({len(tokens)} tokens)，截断到 {max_tokens} tokens"
    )
    return _encoder.decode(tokens[:max_tokens])


def embed_texts(
    texts: list[str],
    client: Optional[OpenAICompatibleEmbeddingClient] = None,
) -> list[list[float]]:
    """
    批量向量化文本。

    安全措施：
      - 跳过空文本（用零向量占位保持索引对齐）
      - 截断超长文本到 HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS
      - 全局信号量限制并发 API 请求
      - 400 错误不重试，Connection/Rate 错误指数退避重试

    Args:
        texts: 文本列表
        client: OpenAI 客户端（可选，不传则自动创建）

    Returns:
        嵌入向量列表，每个向量 EMBEDDING_DIMENSIONS 维，与输入 texts 一一对应
    """
    if client is None:
        client = get_embedding_client()

    dims = config.EMBEDDING_DIMENSIONS
    all_embeddings: list[list[float]] = []

    # 预处理：记录空文本位置，截断超长文本
    processed_texts = []
    empty_indices = set()
    for idx, text in enumerate(texts):
        if not text or not text.strip():
            empty_indices.add(idx)
            processed_texts.append("")
        else:
            processed_texts.append(_truncate_text(text.strip()))

    # 将非空文本分组
    non_empty = [(i, t) for i, t in enumerate(processed_texts) if i not in empty_indices]

    # 为空文本预置零向量
    result_map: dict[int, list[float]] = {}
    zero_vec = [0.0] * dims
    for idx in empty_indices:
        result_map[idx] = zero_vec
        logger.warning(f"文本为空 (index={idx})，使用零向量占位")

    # 按 batch 调用 API
    for batch_start in range(0, len(non_empty), BATCH_SIZE):
        batch_items = non_empty[batch_start:batch_start + BATCH_SIZE]
        batch_indices = [item[0] for item in batch_items]
        batch_texts = [item[1] for item in batch_items]

        retry_count = 0
        max_retries = config.HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES

        while retry_count < max_retries:
            try:
                with _embed_semaphore:
                    try:
                        response = client.embeddings.create(
                            model=config.EMBEDDING_MODEL,
                            input=batch_texts,
                            dimensions=dims,
                            encoding_format="float",
                        )
                    except Exception as e:
                        log_upstream_auth_failure(
                            logger=logger,
                            service="highThinkingQA",
                            endpoint="embeddings",
                            model=config.EMBEDDING_MODEL,
                            base_url=config.EMBEDDING_BASE_URL,
                            api_key=config.EMBEDDING_API_KEY,
                            auth_mode=_embedding_auth_mode(),
                            exc=e,
                        )
                        raise
                log_upstream_auth_success_once(
                    logger=logger,
                    service="highThinkingQA",
                    endpoint="embeddings",
                    model=config.EMBEDDING_MODEL,
                    base_url=config.EMBEDDING_BASE_URL,
                    api_key=config.EMBEDDING_API_KEY,
                    auth_mode=_embedding_auth_mode(),
                )
                batch_embeddings = [item.embedding for item in response.data]
                for orig_idx, emb in zip(batch_indices, batch_embeddings):
                    result_map[orig_idx] = emb
                break

            except Exception as e:
                error_str = str(e)

                # 400 InvalidParameter（文本过长/过短等）：不可恢复，不重试
                if "400" in error_str or "InvalidParameter" in error_str:
                    logger.error(
                        f"Embedding 参数错误 (batch {batch_start // BATCH_SIZE}), "
                        f"跳过该 batch ({len(batch_texts)} 条): {e}"
                    )
                    # 用零向量填充
                    for orig_idx in batch_indices:
                        result_map[orig_idx] = zero_vec
                    break

                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(
                        f"Embedding 调用失败 (batch {batch_start // BATCH_SIZE}), "
                        f"已重试 {max_retries} 次: {e}"
                    )
                    raise

                # 指数退避 + 抖动，Connection error 用更长退避
                if "Connection" in error_str:
                    wait_time = min(3 * (2 ** retry_count) + random.uniform(0, 2), 60)
                else:
                    wait_time = min(2 ** retry_count + random.uniform(0, 1), 30)

                logger.warning(
                    f"Embedding 调用失败，{wait_time:.1f}s 后重试 "
                    f"({retry_count}/{max_retries}): {e}"
                )
                time.sleep(wait_time)

    # 按原始顺序组装结果
    all_embeddings = [result_map.get(i, zero_vec) for i in range(len(texts))]
    return all_embeddings


def embed_single(text: str, client: Optional[OpenAICompatibleEmbeddingClient] = None) -> list[float]:
    """
    向量化单个文本。

    Args:
        text: 输入文本
        client: OpenAI 客户端

    Returns:
        EMBEDDING_DIMENSIONS 维嵌入向量
    """
    result = embed_texts([text], client=client)
    return result[0]
