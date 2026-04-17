# QA Observability Beijing Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify QA runtime logs to Beijing time and add fine-grained lifecycle/LLM timing logs across gateway, admission, public-service, fastQA, and patent.

**Architecture:** Update each service's logging bootstrap to use an explicit Beijing-time formatter, then add event-style observability logs at task, stream, stage, prompt, and upstream LLM boundaries. Reuse existing telemetry where present instead of changing request semantics.

**Tech Stack:** Python, FastAPI, stdlib logging, pytest, httpx

---

### Task 1: Add Beijing Time Formatters

**Files:**
- Modify: `gateway/app/core/logging.py`
- Modify: `public-service/backend/app/core/logging.py`
- Modify: `fastQA/app/core/logging.py`
- Modify: `patent/server_fastapi/logging.py`
- Test: `gateway/tests/test_logging.py`
- Test: `public-service/backend/tests/test_logging.py`
- Test: `fastQA/tests/test_logging.py`
- Test: `patent/tests/test_logging.py`

- [ ] Step 1: Write failing formatter tests for each service
- [ ] Step 2: Run the formatter tests and verify they fail
- [ ] Step 3: Implement Beijing-time formatters
- [ ] Step 4: Run formatter tests and verify they pass

### Task 2: Expand Gateway and Admission Timing Logs

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/app/services/qa_tasks.py`
- Modify: `gateway/app/services/execution_admission.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `gateway/tests/test_task_api.py`

- [ ] Step 1: Write failing gateway log assertions for direct proxy/task/admission milestones
- [ ] Step 2: Run the targeted gateway tests and verify they fail
- [ ] Step 3: Implement milestone logs using existing telemetry and request context
- [ ] Step 4: Run the targeted gateway tests and verify they pass

### Task 3: Expand Public-Service Authority Logs

**Files:**
- Modify: `public-service/backend/app/modules/conversation/internal_api.py`
- Test: `public-service/backend/tests/test_conversation_authority_api.py`

- [ ] Step 1: Write failing assertions for richer authority task log lines
- [ ] Step 2: Run the targeted public-service tests and verify they fail
- [ ] Step 3: Implement richer create-turn/progress/terminal logs
- [ ] Step 4: Run the targeted public-service tests and verify they pass

### Task 4: Expand fastQA Stage and LLM Boundary Logs

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/stage1_planning.py`
- Modify: `fastQA/app/modules/generation_pipeline/synthesis_streaming.py`
- Modify: `fastQA/app/integrations/llm/openai_compat.py`
- Test: `fastQA/tests/test_generation_stage1_planning.py`
- Test: `fastQA/tests/test_generation_stage4_synthesis.py`

- [ ] Step 1: Write failing assertions for prompt/LLM/stage boundary logs
- [ ] Step 2: Run the targeted fastQA tests and verify they fail
- [ ] Step 3: Implement finer stage1/stage4 transport logs
- [ ] Step 4: Run the targeted fastQA tests and verify they pass

### Task 5: Expand patent Stage and LLM Boundary Logs

**Files:**
- Modify: `patent/server/patent/stages/planning.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/server/patent/orchestrators/generation.py`
- Modify: `patent/server/patent/runtime.py`
- Test: `patent/tests/test_patent_stage1_planning.py`
- Test: `patent/tests/test_patent_stage4_synthesis.py`
- Test: `patent/tests/test_patent_generation_orchestrator.py`

- [ ] Step 1: Write failing assertions for prompt/LLM/stage boundary logs
- [ ] Step 2: Run the targeted patent tests and verify they fail
- [ ] Step 3: Implement finer stage1/stage4 timing logs
- [ ] Step 4: Run the targeted patent tests and verify they pass

### Task 6: Final Verification

**Files:**
- Modify: none

- [ ] Step 1: Run the focused service test commands again as a regression pass
- [ ] Step 2: Summarize any residual gaps, especially around runtime-only log paths not covered by unit tests
