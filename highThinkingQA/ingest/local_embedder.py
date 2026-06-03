"""
本地 Qwen3-Embedding 向量化模块。

使用 transformers 加载本地 Qwen3-Embedding-8B，输出 4096 维向量（last token pooling + L2 normalize）。
接口与 ingest/embedder.py 的 embed_texts 保持一致，便于复用 chunker / pipeline。
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

import numpy as np
import tiktoken
import torch
from transformers import AutoModel, AutoTokenizer

logger = logging.getLogger(__name__)

DEFAULT_MODEL_PATH = "/home/cqy/qwen3_embedding_8b"
DEFAULT_DIMENSIONS = 4096
DEFAULT_BATCH_SIZE = 4
DEFAULT_MAX_INPUT_TOKENS = 8192

_encoder = tiktoken.get_encoding("cl100k_base")

_model_lock = threading.Lock()
_model_bundle: Optional[dict] = None


def _truncate_text(text: str, max_tokens: int = DEFAULT_MAX_INPUT_TOKENS) -> str:
    tokens = _encoder.encode(text)
    if len(tokens) <= max_tokens:
        return text
    logger.warning("文本过长 (%s tokens)，截断到 %s tokens", len(tokens), max_tokens)
    return _encoder.decode(tokens[:max_tokens])


def _last_token_pool(last_hidden_states: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
    if left_padding:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_size = last_hidden_states.shape[0]
    return last_hidden_states[
        torch.arange(batch_size, device=last_hidden_states.device),
        sequence_lengths,
    ]


def get_local_embedding_model(
    model_path: str = DEFAULT_MODEL_PATH,
    device: Optional[str] = None,
) -> dict:
    """懒加载本地 embedding 模型（进程内单例）。"""
    global _model_bundle
    with _model_lock:
        if _model_bundle is not None and _model_bundle["model_path"] == model_path:
            return _model_bundle

        resolved_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        logger.info("加载本地 embedding 模型 path=%s device=%s", model_path, resolved_device)
        tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        model = AutoModel.from_pretrained(
            model_path,
            trust_remote_code=True,
            dtype=torch.bfloat16 if resolved_device == "cuda" else torch.float32,
        ).to(resolved_device)
        model.eval()

        _model_bundle = {
            "model_path": model_path,
            "tokenizer": tokenizer,
            "model": model,
            "device": resolved_device,
        }
        return _model_bundle


def embed_texts_local(
    texts: list[str],
    *,
    model_path: str = DEFAULT_MODEL_PATH,
    dimensions: int = DEFAULT_DIMENSIONS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    device: Optional[str] = None,
) -> list[list[float]]:
    """
    批量向量化文本（本地 Qwen3-Embedding）。

    Args:
        texts: 文本列表
        model_path: 本地模型目录
        dimensions: 输出维度（Qwen3-8B 最大 4096，支持 MRL 截断）
        batch_size: 推理 batch 大小
        max_input_tokens: 单条文本最大 token 数
        device: cuda / cpu，默认自动选择

    Returns:
        嵌入向量列表，与 texts 一一对应
    """
    if not texts:
        return []

    dims = max(1, min(int(dimensions), DEFAULT_DIMENSIONS))
    bundle = get_local_embedding_model(model_path=model_path, device=device)
    tokenizer = bundle["tokenizer"]
    model = bundle["model"]
    resolved_device = bundle["device"]
    zero_vec = [0.0] * dims

    processed: list[tuple[int, str]] = []
    empty_indices: set[int] = set()
    for idx, text in enumerate(texts):
        if not text or not str(text).strip():
            empty_indices.add(idx)
        else:
            processed.append((idx, _truncate_text(str(text).strip(), max_input_tokens)))

    result_map: dict[int, list[float]] = {idx: zero_vec for idx in empty_indices}

    for batch_start in range(0, len(processed), batch_size):
        batch_items = processed[batch_start:batch_start + batch_size]
        batch_indices = [item[0] for item in batch_items]
        batch_texts = [item[1] for item in batch_items]

        batch_dict = tokenizer(
            batch_texts,
            padding=True,
            truncation=True,
            max_length=max_input_tokens,
            return_tensors="pt",
        )
        batch_dict = {k: v.to(resolved_device) for k, v in batch_dict.items()}

        with torch.no_grad():
            outputs = model(**batch_dict)
            embeddings = _last_token_pool(outputs.last_hidden_state, batch_dict["attention_mask"])
            embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
            if dims < embeddings.shape[1]:
                embeddings = embeddings[:, :dims]
                embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)

        vectors = embeddings.float().cpu().numpy()
        for orig_idx, vec in zip(batch_indices, vectors):
            result_map[orig_idx] = vec.tolist()

    return [result_map[i] for i in range(len(texts))]


def make_embed_func(
    *,
    model_path: str = DEFAULT_MODEL_PATH,
    dimensions: int = DEFAULT_DIMENSIONS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
    device: Optional[str] = None,
):
    """返回供 chunk_document / semantic_split 使用的 embedding 函数。"""

    def _embed(texts: list[str]) -> list[list[float]]:
        return embed_texts_local(
            texts,
            model_path=model_path,
            dimensions=dimensions,
            batch_size=batch_size,
            max_input_tokens=max_input_tokens,
            device=device,
        )

    return _embed
