"""
文本分块模块
两级分块策略：
  1. 以三级标题 (###) 作为 section 分界线切分
  2. 不超过 2048 tokens 的 section 保持完整
  3. 超过 2048 tokens 的 section 使用基于 Embedding 相似度的语义分块

每个 chunk 携带元数据: {doi, title, section_name, page_range, chunk_index}
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

import tiktoken
import numpy as np

import config

logger = logging.getLogger(__name__)

# 预加载 tiktoken 编码器
_encoder = tiktoken.get_encoding(config.TIKTOKEN_ENCODING)


@dataclass
class Chunk:
    """一个文本块及其元数据"""
    text: str
    doi: str = ""
    title: str = ""
    section_name: str = ""
    chunk_index: int = 0
    total_chunks: int = 0
    token_count: int = 0

    def to_metadata(self) -> dict:
        """转为 Chroma 存储的元数据字典"""
        return {
            "doi": self.doi,
            "title": self.title,
            "section_name": self.section_name,
            "chunk_index": self.chunk_index,
            "total_chunks": self.total_chunks,
            "token_count": self.token_count,
        }


def count_tokens(text: str) -> int:
    """计算文本的 token 数"""
    return len(_encoder.encode(text))


def split_into_sections(markdown_text: str) -> list[dict]:
    """
    按 Markdown 标题 (一级到三级) 切分为 section。

    每遇到一个 #/##/### 标题就切出一个新 section。
    标题前的内容（如果有）作为 "preamble"。

    Args:
        markdown_text: Markdown 格式的论文文本

    Returns:
        section 列表，每项包含 {"title": str, "content": str, "level": int}
    """
    # 匹配 1-3 级标题
    pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

    sections = []
    last_end = 0
    last_title = "Preamble"
    last_level = 0

    for match in pattern.finditer(markdown_text):
        # 当前标题之前的文本属于上一个 section
        content = markdown_text[last_end:match.start()].strip()
        if content:
            sections.append({
                "title": last_title,
                "content": content,
                "level": last_level,
            })

        last_title = match.group(2).strip()
        last_level = len(match.group(1))
        last_end = match.end()

    # 最后一个 section
    content = markdown_text[last_end:].strip()
    if content:
        sections.append({
            "title": last_title,
            "content": content,
            "level": last_level,
        })

    return sections


def semantic_split(
    text: str,
    embedding_func,
    min_tokens: int = None,
    max_tokens: int = None,
) -> list[str]:
    """
    基于 Embedding 相似度的语义分块。
    将文本按句子切分，计算相邻句子的 embedding 余弦相似度，
    在相似度低谷处（语义转折点）切分。

    硬性保证：每个返回的 chunk 都 <= max_tokens。

    Args:
        text: 要切分的长文本
        embedding_func: 接受 list[str] 返回 list[list[float]] 的 embedding 函数
        min_tokens: 最小块大小
        max_tokens: 最大块大小

    Returns:
        切分后的文本块列表，每块 <= max_tokens
    """
    if min_tokens is None:
        min_tokens = config.SEMANTIC_CHUNK_MIN_TOKENS
    if max_tokens is None:
        max_tokens = config.SEMANTIC_CHUNK_MAX_TOKENS

    # 按句子切分（英文句号/问号/叹号、中文句号/问号/叹号、双换行）
    sentences = re.split(r"(?<=[.!?。！？])\s+|\n\n+", text)
    sentences = [s.strip() for s in sentences if s.strip()]

    # 如果切不出多个句子，用硬切兜底（不能原样返回超长文本）
    if len(sentences) <= 1:
        if count_tokens(text) <= max_tokens:
            return [text]
        return _token_split(text, max_tokens)

    # 对超长句子做预处理：硬切到 max_tokens 以内
    safe_sentences = []
    for s in sentences:
        s_tokens = count_tokens(s)
        if s_tokens <= max_tokens:
            safe_sentences.append(s)
        else:
            safe_sentences.extend(_token_split(s, max_tokens))
    sentences = safe_sentences

    # 计算每个句子的 token 数
    sentence_tokens = [count_tokens(s) for s in sentences]

    # 如果整段文本所有句子合起来也不超上限，直接返回
    if sum(sentence_tokens) <= max_tokens:
        return [text]

    # 获取句子 embeddings，找语义转折点
    candidates = set()
    try:
        embeddings = embedding_func(sentences)
        embeddings = np.array(embeddings)

        # 计算相邻句子的余弦相似度
        similarities = []
        for i in range(len(embeddings) - 1):
            a, b = embeddings[i], embeddings[i + 1]
            cos_sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8)
            similarities.append(cos_sim)

        # 候选切分点：相似度低于均值的位置
        if similarities:
            mean_sim = np.mean(similarities)
            candidates = {
                i for i, sim in enumerate(similarities) if sim < mean_sim
            }

    except Exception as e:
        logger.warning(f"语义分块 embedding 调用失败，回退到均匀切分: {e}")
        # candidates 为空，下面的贪心合并退化为纯 max_tokens 切分

    # 基于候选切分点，贪心合并句子（硬性保证 <= max_tokens）
    chunks = []
    current_sentences = []
    current_tokens = 0

    for i, (sentence, tokens) in enumerate(zip(sentences, sentence_tokens)):
        # 加入当前句子会超过上限 → 先 flush 已有内容
        if current_tokens + tokens > max_tokens and current_sentences:
            chunks.append(" ".join(current_sentences))
            current_sentences = []
            current_tokens = 0

        current_sentences.append(sentence)
        current_tokens += tokens

        # 在语义转折点切分（如果已积累足够内容）
        if i in candidates and current_tokens >= min_tokens:
            chunks.append(" ".join(current_sentences))
            current_sentences = []
            current_tokens = 0

    # 处理剩余句子
    if current_sentences:
        tail_text = " ".join(current_sentences)
        # 尾部太短 → 尝试合并到上一个 chunk（但不能超限）
        if chunks and current_tokens < min_tokens // 2:
            merged = chunks[-1] + " " + tail_text
            if count_tokens(merged) <= max_tokens:
                chunks[-1] = merged
            else:
                chunks.append(tail_text)
        else:
            chunks.append(tail_text)

    return chunks


def chunk_document(
    markdown_text: str,
    doi: str = "",
    title: str = "",
    embedding_func=None,
) -> list[Chunk]:
    """
    将解析后的 Markdown 论文文本切分为 chunks。

    策略：
    1. 按三级标题切分为 sections
    2. 不超过 4000 tokens 的 section 保持完整
    3. 超过 4000 tokens 的 section 用语义分块（embedding 相似度找断点），
       若无 embedding_func 则用均匀切分兜底

    Args:
        markdown_text: Markdown 格式的论文文本
        doi: 论文 DOI
        title: 论文标题
        embedding_func: embedding 函数（用于语义分块兜底）

    Returns:
        Chunk 列表
    """
    sections = split_into_sections(markdown_text)

    if not sections:
        # 如果没有识别到标题结构，整篇作为一个 section
        sections = [{"title": "Full Text", "content": markdown_text, "level": 0}]

    chunks = []
    chunk_index = 0

    for section in sections:
        section_text = section["content"]
        section_title = section["title"]
        tokens = count_tokens(section_text)

        if tokens == 0:
            continue

        if tokens <= config.MAX_CHUNK_TOKENS:
            # 不超过阈值，保持 section 完整
            chunks.append(Chunk(
                text=section_text,
                doi=doi,
                title=title,
                section_name=section_title,
                chunk_index=chunk_index,
                token_count=tokens,
            ))
            chunk_index += 1
        else:
            # 超过阈值，需要二次切分
            if embedding_func is not None:
                # 使用语义分块
                sub_texts = semantic_split(section_text, embedding_func)
            else:
                # 没有 embedding 函数，回退到均匀切分
                sub_texts = _fallback_split(section_text)

            for sub_text in sub_texts:
                sub_tokens = count_tokens(sub_text)
                chunks.append(Chunk(
                    text=sub_text,
                    doi=doi,
                    title=title,
                    section_name=section_title,
                    chunk_index=chunk_index,
                    token_count=sub_tokens,
                ))
                chunk_index += 1

    # 回填 total_chunks
    total = len(chunks)
    for c in chunks:
        c.total_chunks = total

    # 尝试从第一个 section 提取论文标题
    if not title and sections:
        first_section = sections[0]
        if first_section["level"] == 1:
            for c in chunks:
                c.title = first_section["title"]

    return chunks


def _fallback_split(text: str) -> list[str]:
    """
    均匀切分兜底：按段落切分，然后合并到目标大小。
    对单个超长段落（> MAX_CHUNK_TOKENS）会先做硬切分，避免产出超限 chunk。
    """
    paragraphs = text.split("\n\n")
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    # 先把超长段落做硬切分
    safe_paragraphs = []
    for para in paragraphs:
        para_tokens = count_tokens(para)
        if para_tokens <= config.MAX_CHUNK_TOKENS:
            safe_paragraphs.append(para)
        else:
            safe_paragraphs.extend(_hard_split_paragraph(para))

    # 合并短段落
    chunks = []
    current_parts = []
    current_tokens = 0

    for para in safe_paragraphs:
        para_tokens = count_tokens(para)
        if current_tokens + para_tokens > config.MAX_CHUNK_TOKENS and current_parts:
            chunks.append("\n\n".join(current_parts))
            current_parts = []
            current_tokens = 0
        current_parts.append(para)
        current_tokens += para_tokens

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def _hard_split_paragraph(text: str) -> list[str]:
    """
    硬切分超长段落：先尝试按句子边界切分，实在找不到边界则按 token 切。
    保证每个返回片段 <= MAX_CHUNK_TOKENS。
    """
    max_tokens = config.MAX_CHUNK_TOKENS

    # 尝试按句子边界切分（英文句号/问号/叹号/中文句号）
    sentences = re.split(r'(?<=[.!?。！？])\s*', text)
    sentences = [s for s in sentences if s.strip()]

    if len(sentences) > 1:
        # 按句子贪心合并
        chunks = []
        current = []
        current_tokens = 0
        for sent in sentences:
            sent_tokens = count_tokens(sent)
            if sent_tokens > max_tokens:
                # 单个句子超长，先 flush 已有内容，再硬切该句子
                if current:
                    chunks.append(" ".join(current))
                    current = []
                    current_tokens = 0
                chunks.extend(_token_split(sent, max_tokens))
                continue
            if current_tokens + sent_tokens > max_tokens and current:
                chunks.append(" ".join(current))
                current = []
                current_tokens = 0
            current.append(sent)
            current_tokens += sent_tokens
        if current:
            chunks.append(" ".join(current))
        return chunks

    # 只有一个"句子"或无法按句子切分，直接按 token 硬切
    return _token_split(text, max_tokens)


def _token_split(text: str, max_tokens: int) -> list[str]:
    """
    按 token 数硬切文本，每段 <= max_tokens。
    """
    tokens = _encoder.encode(text)
    parts = []
    for i in range(0, len(tokens), max_tokens):
        parts.append(_encoder.decode(tokens[i:i + max_tokens]))
    return parts
