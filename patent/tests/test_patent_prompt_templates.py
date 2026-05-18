from __future__ import annotations

from pathlib import Path

from server.patent.answering import (
    DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT,
    DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE,
)
from server.patent.stages.planning import DEFAULT_PATENT_STAGE1_PROMPT
from server.patent.stages.retrieval import (
    DEFAULT_PATENT_STAGE2_QUERY_PROMPT,
    DEFAULT_PATENT_STAGE2_QUERY_SYSTEM_PROMPT,
    build_stage2_queries_for_claim,
)
from server.patent.models import PatentRetrievalClaim


class _FakeStage2Client:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []
        self.chat = _FakeChat(self)


class _FakeChat:
    def __init__(self, outer: _FakeStage2Client) -> None:
        self.completions = _FakeCompletions(outer)


class _FakeCompletions:
    def __init__(self, outer: _FakeStage2Client) -> None:
        self._outer = outer

    def create(self, **kwargs):
        from types import SimpleNamespace

        self._outer.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._outer.content))]
        )


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


PROMPT_ROOT = Path(__file__).resolve().parents[1] / "server" / "patent" / "prompts"


def _read_template(name: str) -> str:
    return PROMPT_ROOT.joinpath(name).read_text(encoding="utf-8")


def test_stage_prompts_are_loaded_from_txt_templates():
    assert DEFAULT_PATENT_STAGE1_PROMPT == _read_template("stage1_planning.txt")
    assert DEFAULT_PATENT_STAGE2_QUERY_PROMPT == _read_template("stage2_query_generation.txt")
    assert DEFAULT_PATENT_STAGE2_QUERY_SYSTEM_PROMPT == _read_template("stage2_query_system.txt")
    assert DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE == _read_template("stage4_answer_user.txt")
    assert DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT == _read_template("stage4_answer_system.txt")


def test_stage4_default_prompts_use_oldcode_synthesis_contract():
    assert "你是一名最终的答案润色与校验专家。" in DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE
    assert "**【按件综述式写作（必读）】**" in DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE
    assert "请输出最终答案（Markdown格式，在引用专利证据的地方直接添加 `(patent_id=公开号)` 引用）。" in DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE
    assert "专利公开号" in DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE
    assert "(patent_id=公开号)" in DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE
    assert "(doi=xxx)" not in DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE
    assert "DOI引用规则" not in DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE
    assert "你是专利分析助手" not in DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT
    assert "你是一位资深的材料科学学术专家，擅长从工程应用角度分析材料失效机理。" in DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT
    assert "禁止声称\"未找到专利证据\"" in DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT
    assert "专利公开号" in DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT
    assert "(patent_id=公开号)" in DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT
    assert "(doi=xxx)" not in DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT
    assert "DOI" not in DEFAULT_PATENT_STAGE4_ANSWER_USER_TEMPLATE
    assert "DOI" not in DEFAULT_PATENT_STAGE4_ANSWER_SYSTEM_PROMPT


def test_stage2_default_prompt_uses_patent_plain_keyword_contract():
    assert DEFAULT_PATENT_STAGE2_QUERY_SYSTEM_PROMPT.strip() == "你是一个专利检索专家，擅长根据技术问题生成精准的专利检索查询。"
    assert "你是一个专利检索专家，擅长根据技术问题生成精准的专利检索查询。" in DEFAULT_PATENT_STAGE2_QUERY_PROMPT
    assert "专利摘要库和专利全文 chunk 库" in DEFAULT_PATENT_STAGE2_QUERY_PROMPT
    assert "用空格分隔的关键字列表（40-60字）" in DEFAULT_PATENT_STAGE2_QUERY_PROMPT
    assert "请严格输出一个 JSON 对象" not in DEFAULT_PATENT_STAGE2_QUERY_PROMPT
    assert "学术论文" not in DEFAULT_PATENT_STAGE2_QUERY_PROMPT
    assert "文献检索" not in DEFAULT_PATENT_STAGE2_QUERY_PROMPT


def test_stage1_default_prompt_contains_patent_prefix_and_planning_contract():
    assert DEFAULT_PATENT_STAGE1_PROMPT.startswith("**【专利模式 — 必读】**")
    assert "向量库元数据主键为 **patent_id**" in DEFAULT_PATENT_STAGE1_PROMPT
    assert "你是一位长期从事磷酸铁锂（LFP）正极材料研发与中试/量产放大工作的工程师" in DEFAULT_PATENT_STAGE1_PROMPT
    assert "提取出3-5个最核心、最需要事实或专利证据支撑的\"关键主张\"或\"验证点\"" in DEFAULT_PATENT_STAGE1_PROMPT
    assert "必需字段**：`patent_id`" in DEFAULT_PATENT_STAGE1_PROMPT
    assert "deep_answer" in DEFAULT_PATENT_STAGE1_PROMPT
    assert "retrieval_claims" in DEFAULT_PATENT_STAGE1_PROMPT
    assert "你必须严格按照上文给出的 JSON 模板输出" in DEFAULT_PATENT_STAGE1_PROMPT
    assert "DOI" not in DEFAULT_PATENT_STAGE1_PROMPT
    assert "`doi`" not in DEFAULT_PATENT_STAGE1_PROMPT
    assert "论文" not in DEFAULT_PATENT_STAGE1_PROMPT


def test_stage2_query_generation_sends_oldcode_prompt_and_accepts_plain_keywords():
    client = _FakeStage2Client("葡萄糖 PEG 4:1 质量比 混合碳源 LiFePO4 电化学性能 最佳比例")

    queries = build_stage2_queries_for_claim(
        user_question="葡萄糖和PEG的最佳比例是多少？",
        retrieval_claim=PatentRetrievalClaim(
            claim="葡萄糖和PEG混合碳源比例影响LiFePO4电化学性能。",
            keywords=["葡萄糖", "PEG", "比例"],
        ),
        client=client,
        model="query-model",
        logger=_Logger(),
    )

    assert queries == ["葡萄糖 PEG 4:1 质量比 混合碳源 LiFePO4 电化学性能 最佳比例"]
    call = client.calls[0]
    assert "response_format" not in call
    assert call["temperature"] == 0.3
    assert call["max_tokens"] == 150
    assert call["messages"][0]["content"] == DEFAULT_PATENT_STAGE2_QUERY_SYSTEM_PROMPT.strip()
    user_prompt = call["messages"][1]["content"]
    assert "【原始用户问题】（最重要！查询必须紧密围绕这个问题生成）：" in user_prompt
    assert "葡萄糖和PEG的最佳比例是多少？" in user_prompt
    assert "葡萄糖, PEG, 比例" in user_prompt
    assert "返回值只能是一个 JSON 对象" not in user_prompt
