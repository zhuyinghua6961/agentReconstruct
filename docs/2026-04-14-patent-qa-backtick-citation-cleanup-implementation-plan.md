# Patent QA Backticked Citation Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改提示词、不改前端链接逻辑、也不改 public-service 归一化逻辑的前提下，修复 patent 普通 QA 中“专利号被反引号包成 inline code，最终不可点击”的偶发问题，并同时覆盖流式输出、done payload、以及最终持久化答案。

**Architecture:** 把修复限制在 `patent/server/patent/answering.py` 的“用户可见引用渲染层”。新增一个只识别“被反引号包裹的专利引用块”的窄清洗 helper，并在 `render_patent_citations_for_user()` 最前面调用。由于 `PatentCitationStreamSanitizer` 和 stage4 最终答案渲染都复用这个入口，streaming delta 与 final answer 会一起收敛，而 `sanitize_patent_id_citations()` 继续只负责白名单校验、非法引用剔除、以及 citation metadata 统计。

**Tech Stack:** Python, FastAPI patent backend, regex-based citation normalization, pytest contract tests

---

## Investigation Evidence

- 已确认坏形态源头在 patent stage4 answer builder 流式输出，不是前端 linkify 回归。
- 坏案例复现：
  - `trace_id=task_6d75a0dab6814c12b70fb510f6da6077`
  - `conversation_id=381`
- 同类坏持久化样本：
  - `public-service/data/runtime/data/conversations/9/357.json`
  - `public-service/data/runtime/data/conversations/9/373.json`
  - `public-service/data/runtime/data/conversations/9/381.json`
- 对照正常样本：
  - `public-service/data/runtime/data/conversations/9/377.json`
- 关键日志与代码证据：
  - `resource/logs/dev/patent/patent-app.log`
  - `resource/logs/dev/public-service/public-service-error.log`
  - `patent/server/patent/stages/synthesis.py`
  - `patent/server/patent/answering.py`
  - `public-service/backend/app/modules/conversation/service.py`
  - `frontend-vue/src/utils/index.js`

## Hard Constraints

1. 本补丁不改 `patent/server/patent/answering.py` 里的 stage4 prompt 文案，尤其不调整 `(patent_id=公开号)` 提示语。
2. 本补丁不改 `public-service/backend/app/modules/conversation/service.py`，因为它只能影响持久化后的文本，不能覆盖 patent stage4 流式输出。
3. 本补丁不改 `frontend-vue/src/utils/index.js`，因为前端“不在 `<code>`/`<pre>` 里 linkify”是预期行为，不是 bug。
4. 绝不能做“全局删反引号”这种宽清洗；只能移除“外层是反引号、内容是专利引用块”的 code span。
5. helper 必须依赖 `allowed_patent_ids` 做收窄判断；如果无法确认是允许白名单内的专利引用块，就保留原始 code span，不做猜测性替换。
6. 保持现有 `_STREAM_CITATION_TAIL_HOLD` 与现有诊断日志逻辑不变，先修用户可见结果，再看是否需要后续日志降噪。
7. 不要触碰当前工作区里与本补丁无关的脏文件：
   - `patent/server/patent/pdf_contract.py`
   - `patent/server/patent/pdf_service.py`
   - `patent/tests/test_patent_file_routes.py`
   - `patent/tests/test_patent_pdf_contract.py`

## Lock Decisions

1. 修复边界放在 `render_patent_citations_for_user()`，而不是 `sanitize_patent_id_citations()`。
原因：
`sanitize_patent_id_citations()` 负责 whitelist/invalid bookkeeping；把反引号去除塞进这里，会把“格式修饰清洗”和“引用合法性判定”混到一起。

2. `PatentCitationStreamSanitizer` 继续复用 `render_patent_citations_for_user()`。
原因：
这样同一套逻辑自然覆盖流式 chunk 和最终 answer，不需要额外在 stream path 再复制一份处理分支。

3. 不在 public-service 追加兜底逻辑。
原因：
public-service 现在已经能把 `(patent_id=CN...)` 归一成 `(CN...)`，但它保留外层反引号。把最终修复放在 patent 更接近源头，也能同时修掉流式输出。

4. 不在前端做“code 内专利号也 linkify”的兼容。
原因：
这样会把真正的代码片段误判成专利引用，边界更差，也掩盖后端输出脏数据。

## File Map

### Backend Implementation

- Modify: `patent/server/patent/answering.py:15-18`
- Modify: `patent/server/patent/answering.py:120-174`

### Backend Tests

- Modify: `patent/tests/test_patent_stage4_synthesis.py:555-583`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py:1285-1332`

### Read-Only References

- Read only: `patent/server/patent/stages/synthesis.py:479-556`
- Read only: `patent/server/patent/stages/synthesis.py:587-626`
- Read only: `public-service/backend/app/modules/conversation/service.py:307-330`
- Read only: `frontend-vue/src/utils/index.js:507-535`
- Read only: `frontend-vue/src/utils/index.js:1014-1027`

## Helper Contract

准备新增的 helper 只处理下面三类 code span：

- `` `(patent_id=CN115367725B)` ``
- `` `(CN115367725B)` ``
- `` `(CN115367725B, CN117199293A, CN118164457B)` ``

helper 不处理下面这些内容：

- 任意普通 inline code，例如 `` `x = y + z` ``
- 非白名单专利号构成的 code span
- 不是括号包裹的 code span
- 括号内包含普通词语、公式、单位、URL、DOI 或其他无法确定为 patent citation list 的内容

建议 helper 轮廓：

```python
_BACKTICK_CODE_SPAN_RE = re.compile(r"`(?P<body>[^`\n]{1,200})`")
_PATENT_CITATION_LIST_ITEM_RE = re.compile(r"^(?:patent_id\s*=\s*)?([A-Za-z0-9._/\\-]+)$", re.IGNORECASE)


def _unwrap_backticked_patent_citation_blocks(
    text: str,
    *,
    allowed_patent_ids: list[str] | None,
) -> str:
    allowed = set(_normalize_patent_id_list(allowed_patent_ids))
    if not allowed:
        return str(text or "")

    def _replace(match: re.Match[str]) -> str:
        body = str(match.group("body") or "").strip()
        if not (body.startswith("(") and body.endswith(")")):
            return match.group(0)
        inner = body[1:-1].strip()
        if not inner:
            return match.group(0)

        raw_parts = [part.strip() for part in inner.split(",")]
        if not raw_parts:
            return match.group(0)

        for raw_part in raw_parts:
            token_match = _PATENT_CITATION_LIST_ITEM_RE.fullmatch(raw_part)
            if not token_match:
                return match.group(0)
            patent_id = _normalize_patent_id(token_match.group(1))
            if not patent_id or patent_id not in allowed:
                return match.group(0)
        return body

    return _BACKTICK_CODE_SPAN_RE.sub(_replace, str(text or ""))
```

实现时保持两个原则：

- helper 只去掉“最外层 backticks”，不负责 `patent_id=` 清洗。
- `patent_id=` 的去除仍交给 `render_patent_citations_for_user()` 现有逻辑处理。

### Task 1: Lock The Failure With Red Tests

**Files:**
- Modify: `patent/tests/test_patent_stage4_synthesis.py:555-583`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py:1285-1332`
- Read: `patent/server/patent/stages/synthesis.py:479-556`
- Read: `patent/server/patent/stages/synthesis.py:622-626`

- [ ] **Step 1: 扩展 stage4 流式测试，先锁住反引号坏形态**

在 `patent/tests/test_patent_stage4_synthesis.py` 新增或拆分一个专门的 backtick case，覆盖：

- 流式 builder 输出 `结论来自专利 \`(patent_id=CN115132975B)\`。` 这种单专利引用
- 流式 builder 输出 `补充证据见 \`(patent_id=P1, P2)\`。` 这种多专利列表引用
- 至少一个 case 要跨 chunk 拆开反引号引用块，确保 `PatentCitationStreamSanitizer` 也能修到

断言：

- `streamed_text` 中不再包含反引号
- `streamed_text` 中不再包含 `patent_id=`
- `streamed_text` 保留 `(CN...)` 或 `(P1, P2)` 这样的普通文本引用
- `result["final_answer"]` 同样不包含反引号
- 普通 inline code 例如 `` `x = y + z` `` 在 `streamed_text` 和 `result["final_answer"]` 中都保持不变

- [ ] **Step 2: 扩展 ask contract 测试，锁住最终 SSE 对外行为**

在 `patent/tests/fastapi_contract/test_ask_contract.py` 新增一个与现有
`test_stream_ask_strips_raw_patent_id_from_streaming_and_done_payload`
并列的 case，runtime 返回 backticked citation block。

断言：

- `content` event 聚合文本没有反引号
- `done.final_answer` 没有反引号
- `content` 与 `done.final_answer` 都保留白名单内专利号
- 非专利引用的普通 inline code 仍保持 code span 原样

- [ ] **Step 3: 跑红灯测试**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_stage4_synthesis.py patent/tests/fastapi_contract/test_ask_contract.py -q
```

Expected:
- FAIL
- 失败点集中在当前 `render_patent_citations_for_user()` 仍然保留外层反引号

### Task 2: Implement Narrow Cleanup In The User-Visible Render Layer

**Files:**
- Modify: `patent/server/patent/answering.py:15-18`
- Modify: `patent/server/patent/answering.py:120-174`
- Test: `patent/tests/test_patent_stage4_synthesis.py:555-583`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py:1285-1332`

- [ ] **Step 1: 在 `answering.py` 增加窄匹配 regex 与 helper**

实现要求：

- helper 名字建议为 `_unwrap_backticked_patent_citation_blocks`
- 只识别单行 code span，不处理跨行 code block
- 只接受括号包裹的 citation body
- citation list 中每个 token 都必须能被 `_normalize_patent_id()` 归一，并且命中 `allowed_patent_ids`
- 一旦有任一 token 不合法，整个 code span 原样保留

- [ ] **Step 2: 把 helper 接到 `render_patent_citations_for_user()` 最前面**

顺序要求：

1. 先做 backtick citation unwrap
2. 再执行现有 `_PATENT_ID_CITATION_RE.sub(...)`
3. 再执行现有 `patent_id=` 删除、空括号清理、空白压缩

不要改动：

- `sanitize_patent_id_citations()`
- `PatentAnswerBuilder._build_prompt_with_metadata()`
- `PatentAnswerBuilder._build_request_payload()`

- [ ] **Step 3: 保持 stream path 复用同一逻辑，不新增第二套流式清洗分支**

确认 `PatentCitationStreamSanitizer.consume()` 与 `.finalize()` 继续只调用
`render_patent_citations_for_user(..., trim=False)`；
如果测试已经覆盖，就不要再引入额外 stream-only helper。

- [ ] **Step 4: 重跑目标测试**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_stage4_synthesis.py patent/tests/fastapi_contract/test_ask_contract.py -q
```

Expected:
- PASS
- 现有 `patent_id=` 清洗行为仍保持通过
- 新增 backtick case 转绿

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/answering.py patent/tests/test_patent_stage4_synthesis.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "fix(patent): unwrap backticked patent citations before render"
```

### Task 3: Focused Regression And Rollout Verification

**Files:**
- Read: `resource/logs/dev/patent/patent-app.log`
- Read: `resource/logs/dev/public-service/public-service-error.log`
- Read: `public-service/data/runtime/data/conversations/9/381.json`

- [ ] **Step 1: 跑一轮更聚焦的 backend regression**

Run:
```bash
env PYTEST_ADDOPTS='--cache-dir=patent/.pytest_cache' TMPDIR=patent/.tmp conda run -n agent pytest patent/tests/test_patent_stage4_synthesis.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_kb_service.py -q
```

Expected:
- PASS
- 普通 `kb_qa` 流式行为不变

- [ ] **Step 2: 用现有诊断日志确认“raw 仍可脏，user-visible 已变干净”**

检查点：

- `raw_answer` 日志里仍可能看到 `backtick_patent_span_count > 0`
- `rendered_answer_for_user` 不应再出现 `backtick_rendered_patent_citation_count > 0`
- `patent stage4 stream suspicious trailing visible delta` 不应再由 backtick citation 触发

可用命令：

```bash
rg -n "raw_answer|rendered_answer_for_user|suspicious trailing visible delta" resource/logs/dev/patent/patent-app.log
```

- [ ] **Step 3: 人工 smoke 一次 patent 普通 QA 流式输出**

如果本地 patent 服务已启动，针对普通 `kb_qa` 发一轮流式请求，确认：

- SSE `content` 中的专利号不是 `<code>` 语义
- 最终持久化会话文本里不再保留 `` `(CN...)` ``

如无法在当前环境完成人工 smoke，至少保留 Task 1/2/3 的自动化测试结果与日志检查结果。

## Acceptance Criteria

- patent 普通 QA 的流式 `content` 与 `done.final_answer` 都不会再输出 `` `(CN...)` `` 这种 backticked patent citation
- 白名单内合法引用仍保留，非法引用仍按现有逻辑清掉
- `sanitize_patent_id_citations()` 的 `cited_patent_ids` / `invalid_cited_patent_ids` 统计逻辑不回归
- 前端无需改代码就能重新把 `(CN...)` 渲染成可点击专利号
- public-service 无需新增兜底清洗

## Non-Goals

- 不修 prompt 诱导问题
- 不把所有 inline code 中的专利号都做链接化
- 不调整 public-service conversation normalization 范围
- 不在本期顺手清理调试日志
