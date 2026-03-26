# 2026-03-26 P3 Runtime Boundary Alignment Notes

## 范围

本轮只收口 3 个 P3 子项：
- `P3-3` fastQA / highThinkingQA 问答阶段缓存 TTL 对齐
- `P3-5` Redis key 命名简化
- `P3-6` 移除 gateway QA ask 路径聊天持久化兼容代理

不包含：
- `P3-1` public-service summary/memory 增强
- `P3-2` gateway 兼容字段/旧路由清理
- `P3-4` highThinkingQA checker 慢点定位

---

## 一、P3-3 TTL 对齐结果

### fastQA
当前保留：
- `stage1`: `3600s`
- `stage2`: `1800s`
- `stage2.5`: `1800s`
- `stage3`: `1800s`
- `pdf_text`: `86400s`

说明：
- `pdf_text` 保持 `86400s`，因为它更接近文件提取缓存，不是本轮要和 `highThinkingQA` 强行对齐的问答阶段推理缓存。
- 本轮没有缩短 `pdf_text` TTL，避免显著增加重复 PDF 文本提取开销。

### highThinkingQA
当前收口为：
- `direct_answer`: `3600s`
- `decompose`: `3600s`
- `retrieve`: `1800s`

说明：
- 唯一明显漂移的是 `decompose`，已从 `21600s` 收口到 `3600s`。
- 这样 `direct_answer/decompose` 与 fastQA 的上游规划类缓存更接近，`retrieve` 与 fastQA `stage2` 保持同档。

---

## 二、P3-5 Redis key 命名收口结果

### fastQA
缓存 key 从：
- `fastqa:cache:qa:stage1:...`
- `fastqa:cache:qa:stage2:...`
- `fastqa:cache:qa:stage25:...`
- `fastqa:cache:qa:stage3:...`
- `fastqa:cache:qa:pdftext:...`

收口为：
- `fastqa:cache:stage1:...`
- `fastqa:cache:stage2:...`
- `fastqa:cache:stage25:...`
- `fastqa:cache:stage3:...`
- `fastqa:cache:pdftext:...`

lock key 同步去掉中间冗余 `qa` 层。

### highThinkingQA
缓存 key 从：
- `highthinkingqa:cache:highthinkingqa:direct_answer:...`
- `highthinkingqa:cache:highthinkingqa:decompose:...`
- `highthinkingqa:cache:highthinkingqa:retrieve:...`

收口为：
- `highthinkingqa:cache:direct_answer:...`
- `highthinkingqa:cache:decompose:...`
- `highthinkingqa:cache:retrieve:...`

lock key 同步去掉重复的服务名前缀段。

### 结论
本轮命名原则已经收口为：
- `<service_prefix>:cache:<capability>:...`
- `<service_prefix>:lock:<capability>:...`

没有继续保留“prefix 后再重复 service / qa 命名空间”的冗余层级。

---

## 三、P3-6 gateway QA 持久化代理移除结果

### 变更前
`gateway` 在 `/api/.../ask` 与 `/api/.../ask_stream` 路径上：
- 会先代写 user message
- sync ask 成功后会代写 assistant summary
- stream ask 完成后也会代写 assistant summary

这与当前目标边界冲突，因为：
- `fastQA` 已直接走 `public-service authority`
- `highThinkingQA` 已直接走 `public-service authority`
- `gateway` 应只保留分发职责，而不是再做 QA ask 路径持久化

### 变更后
`gateway/app/routers/qa.py` 已删除：
- `_proxy_ask()` 里的 user persistence
- `_proxy_ask()` 里的 sync assistant persistence
- `_proxy_ask_stream()` 里的 user persistence
- `_proxy_ask_stream()` 里的 stream summary persistence

保留不变：
- route decision
- upstream forwarding
- SSE body passthrough
- upstream error conversion

### 边界结论
现在 QA ask 主链的 authority 写入只应发生在：
- `fastQA -> public-service`
- `highThinkingQA -> public-service`

`gateway` 在这条链路上不再代写聊天记录。

---

## 四、验证证据

### gateway
命令：
```bash
conda run -n agent pytest tests/test_qa_proxy.py tests/test_route_decision.py -q
```

结果：
- `51 passed in 0.85s`

### fastQA + highThinkingQA cache
命令：
```bash
conda run -n agent pytest fastQA/tests/test_qa_cache_stage1.py fastQA/tests/test_qa_cache_stage2.py fastQA/tests/test_qa_cache_stage25.py fastQA/tests/test_qa_cache_stage3.py fastQA/tests/test_qa_cache_ttl_contract.py highThinkingQA/tests/test_stage_cache_runtime.py highThinkingQA/tests/test_stage_cache_ttl_contract.py highThinkingQA/tests/test_stage_cache_behavior.py -q
```

结果：
- `24 passed in 0.63s`

---

## 五、当前状态判断

### 已完成
- `P3-3`
- `P3-5`
- `P3-6`

### 仍未完成
- `P3-1` public-service summary/memory 增强
- `P3-2` gateway 兼容字段、旧路由、历史兼容层清理
- `P3-4` highThinkingQA checker 慢点定位

---

## 六、下一步建议

下一步更适合直接进入：
1. `P3-4` highThinkingQA checker 慢点定位
2. 或者回到 `P4` 产品能力探索
