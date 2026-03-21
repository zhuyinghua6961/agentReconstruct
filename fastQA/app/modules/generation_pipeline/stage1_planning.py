from __future__ import annotations

import json
from typing import Any, Dict


def run_stage1_pre_answer_and_planning(
    *,
    user_question: str,
    stage1_prompt: str,
    vector_db_context: str,
    client: Any,
    model: str,
    logger: Any,
) -> Dict[str, Any]:
    logger.info("阶段一：LLM预回答与检索规划")
    logger.info("用户问题: %s", user_question)

    try:
        full_system_prompt = stage1_prompt + (("\n\n" + vector_db_context) if vector_db_context else "")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": full_system_prompt
                    + "\n\n你必须严格按照 JSON 模板输出，返回值只能是一个 JSON 对象，不能包含任何解释性文字。",
                },
                {"role": "user", "content": f"用户问题：{user_question}"},
            ],
            temperature=0.5,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )

        result_text = str(response.choices[0].message.content or "").strip()
        cleaned_text = result_text
        if "```json" in cleaned_text:
            cleaned_text = cleaned_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in cleaned_text:
            cleaned_text = cleaned_text.split("```", 1)[1].split("```", 1)[0].strip()

        try:
            stage1_result = json.loads(cleaned_text)
        except json.JSONDecodeError:
            try:
                stage1_result = json.loads(result_text)
                cleaned_text = result_text
            except json.JSONDecodeError as exc:
                logger.error("阶段一 JSON 解析失败，降级为仅预回答: %s", exc)
                return {
                    "success": True,
                    "deep_answer": result_text,
                    "retrieval_claims": [],
                    "raw_response": result_text,
                    "fallback": "json_parse_failed",
                }

        deep_answer = str(stage1_result.get("deep_answer") or "").strip()
        raw_claims = stage1_result.get("retrieval_claims") or []

        retrieval_claims = []
        for item in raw_claims:
            if isinstance(item, dict):
                claim_text = str(item.get("claim") or "").strip()
                retrieval_claims.append(
                    {
                        "claim": claim_text,
                        "keywords": list(item.get("keywords") or []),
                        "preferred_sections": list(item.get("preferred_sections") or item.get("preferred") or []),
                        "filters": item.get("filters") if isinstance(item.get("filters"), dict) else {},
                    }
                )
            else:
                retrieval_claims.append(
                    {
                        "claim": str(item or "").strip(),
                        "keywords": [],
                        "preferred_sections": [],
                        "filters": {},
                    }
                )

        retrieval_claims = [item for item in retrieval_claims if str(item.get("claim") or "").strip()]
        logger.info(
            "阶段一结果归一化完成: deep_answer_chars=%s retrieval_claims=%s",
            len(deep_answer),
            len(retrieval_claims),
        )
        return {
            "success": True,
            "deep_answer": deep_answer,
            "retrieval_claims": retrieval_claims,
            "raw_response": cleaned_text,
        }
    except Exception as exc:
        logger.error("阶段一执行失败: %s", exc)
        return {"success": False, "error": str(exc)}
