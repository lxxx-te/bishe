# 毕业设计行动计划（grilling v7 更新版）

> 基于 2026-07-13 `/grill-with-docs` 会话生成，**2026-07-30 更新 P5-P6 完成状态**。
> 时间线：**剩余 3-4 周**。
> 交付物定位：**毕业设计**（系统实现 + 演示 + 设计说明书），不是毕业论文。

---

## 一、本次 grilling 钉死的决策

| 议题 | 决策 | 理由 |
|------|------|------|
| P5 修复优先级 | **最高阻塞项，先修再进 P6** | refusal/citation 信号是假的，P6 指标不可信 |
| refusal 判定 | **硬匹配"信息不足"** | prompt 已强制四字，简单可复现 |
| citation 校验 | **零宽容** | 所有 `[事件#N]` 必须落在召回 Top-5 内 |
| "开始生成…"预通知 | **删掉** | 避免拒答时产生预期错位 |
| 异常分支 | **`error: true` + `refusal: true`** | 前端知错误，评测不当作有效答案 |
| RAG gold 集 | **混合生成 + 人工标**（10 具体 + 10 宽泛 + 10 拒答） | 覆盖三类查询，不自标 ground truth |
| 消融实验 | **feature flags，单脚本跑 5 次** | 路径一致、无起停污染 |
| faithfulness 指标 | **RAGAS + DeepSeek** | 比简单 LLM judge 更权威 |
| P7 前端 | **两页 MVP，事件流页做精致** | 答辩演示最抢眼，onboarding 用简单表单 |
| 书面交付物 | **设计说明书，30-40 页** | 毕业设计不需要 60+ 页论文 |
| 文献策略 | **6 篇国际经典尽量换中文综述/学位论文，全部知网可搜** | 导师要求知网可查，不编 |
| 论文写作顺序 | **等 P6 数据出来后再写** | 7-8 周时间够，先保证系统可运行 |
| 多出来的时间 | **优先补测试覆盖** | 毕业设计系统测试章需要模块级证明 |

### v7.5 补充决策（grilling 后续补漏）

| 议题 | 决策 | 理由 |
|------|------|------|
| 数据库快照 | **答辩前一周手动 `pg_dump` 一次** | 单人项目最轻量，所有评测基于该固定快照 |
| 数据源不足 | **保持 2 家可用源，写进系统局限** | 扩源运维风险高，主动写局限比硬撑稳 |
| 答辩 demo | **4 步：ingest → 事件流 → RAG 正常回答 → RAG 拒答** | 拒答直观展示防幻觉保证 |
| P4 多日期 bug | **现在修：提取所有 YYYY-MM-DD 取最新日期** ✅ 已完成 | 局部修复，避免 false-positive conflict |
| API 额度 | **真 key 分天跑，每天 ≤200 条** | 保留真实模型输出，避免 mock 失真 |

---

## 二、当前进度

- **P0–P4**：已完成并闭环。
- **P5**：✅ 已完成。RAG 三段管道 + 受约束生成 + 强制引用 + 拒答 + SSE 流式跑通；5 个结构性漏洞已修复，新增 5 个单元测试全绿。
- **P6**：✅ 已完成。50 条 RAG 问答 gold 集已标注 48 条、消融 5 配置已跑通、指标已出（recall@5 0.452 / refusal accuracy 0.958 / citation valid 1.0 / faithfulness 0.978）。
- **P7**：无前端文件，需新建 Vue 项目。
- **设计说明书**：未开始，但 HANDOFF.md 已积累大量素材。

---

## 三、3-4 周路线图

| 周 | 主题 | 关键产出 | 预估工时 |
|----|------|----------|----------|
| **第 1 周** | ~~P5 修复 + 小范围回归~~ ✅ 已完成 | `answer.py` 修复、`dailylimit.py` 加锁、新增 `tests/test_rag.py` | 12h |
| **第 1 周** | ~~P6 评测基建 + 标注 + 跑表~~ ✅ 已完成 | `build_rag_gold.py`、`label_rag_gold.py`、`run_rag_eval.py`、`run_ablation.py`、50 条 gold、5 行消融表 | 16h |
| **第 2 周** | **P7 Vue 前端** | onboarding 表单 + 事件流卡片 + RAG 对话框 + CORS | 15h |
| **第 3 周** | 测试覆盖 + 性能测试 + 文档更新 | P2/P3/P4 单元测试 + `benchmark.py` + README/PLAN/PITFALLS 刷新 | 10h |
| **第 3-4 周** | 设计说明书 + 论文图表 + 答辩 PPT | 4 张图 + 第 1-5 章 + 答辩话术 + 系统截图 | 18h |
| **第 4 周** | 缓冲 + 演练 + 数据库快照 | `pg_dump` 快照、预答辩演练、修复 residual bug | 8h |

> 合计约 61h，留有余量。前两件事（P7 前端 + 论文骨架）必须优先，否则答辩无系统可演示。

---

## 四、第 1 周任务清单：P5 修复

### 4.1 `app/services/rag/answer.py` 修改点

1. **累积 `full_text`**
   - 在流式循环里维护 `full_text += delta`。
   - 整句 flush 时仍然用 buf，但额外累积完整文本用于后处理。

2. **真实 refusal 判定**
   - 生成结束后 `is_refusal = "信息不足" in full_text`。
   - `done` 事件：`{"refusal": true/false}`。

3. **citation 解析与校验**
   - `re.findall(r'\[事件#(\d+)\]', full_text)`。
   - 校验每个 id 在 `event_ids` 内。
   - `done` 增加 `citations` + `citations_valid`。

4. **meta 事件合法 JSON**
   - `json.dumps({"event_ids": event_ids})`。

5. **删除"开始生成…"预通知**
   - 删掉第 150 行那行 `yield`。

6. **异常分支**
   - `except Exception` 里：`yield f'event: done\ndata: {json.dumps({"refusal": true, "error": true})}\n\n'`。

7. **context 格式微调（可选，本周有空就做）**
   - 统一用 `\n  - ` 缩进，长 context 更清晰。

### 4.2 `app/services/rag/dailylimit.py`

- 加 `asyncio.Lock`，封装 `increment_today_async`。
- 调用方改为 `await increment_today_async(1)`。

### 4.3 测试

- 新增 `tests/test_rag.py`：
  - 测试空召回时 refusal=true；
  - 测试正常回答含合法 citation；
  - 测试 LLM 输出"信息不足"时 refusal=true；
  - 测试异常分支 `error: true`。
- 跑 `pytest` 全量回归。

---

## 五、P6 评测要点（第 2-3 周）

1. **50 条 gold 集**
   - 20 具体查询（gold=1 个事件）
   - 20 宽泛查询（gold=N 个事件，标全）
   - 10 应拒答查询（gold=空集）
   - 禁用系统输出当标签。

2. **指标**
   - `set recall@5 = min(|Top-5 ∩ gold|, 5) / min(|gold|, 5)`
   - `faithfulness`（RAGAS LLM-judge，公开标注为指标）
   - `拒答率 = 拒答次数 / 50`

3. **消融实验**
   - 配置 A：dense Top-5（无时间过滤、无 reranker、无受约束生成）
   - 配置 B：A + 时间预过滤
   - 配置 C：B + reranker
   - 配置 D：C + 受约束生成
   - 配置 E：最终版（= D）

---

## 六、P7 前端要点（第 4 周）

- `npm create vite@latest frontend -- --template vue`
- 后端加 `CORSMiddleware`。
- 页面：
  1. **onboarding**（弹窗/简单表单）：选 2-3 兴趣标签，写 `user_profile`。
  2. **事件流**：精致卡片，含 `merged_summary`、`fact_slots`、`conflict_flags` 标红、多源列表、原文链接。
  3. **RAG 对话**：SSE 解析、整句渲染、引用可点、溯源到事件卡片。
- 重点把事件流页做漂亮，这是答辩演示核心。

---

## 七、设计说明书骨架（第 6 周参考）

1. 项目背景与意义
2. 需求分析
3. 总体设计（四层架构 + 数据流图 + E-R 图）
4. 详细设计与实现（P1-P5 各模块）
5. 系统测试（单元测试 + 去重阈值实验 + RAG 消融实验 + 性能测试）
6. 总结与展望

文献综述压缩到 5-8 页，全部知网可搜。

---

## 八、已知风险

1. **DeepSeek API 不稳定 / 额度** — 200/日上限演示足够，但跑 P6 50 条 eval 可能需要分几天。
2. **知网文献数量** — 部分国际经典可能没有中文对应综述，需用近义词/相关主题检索。
3. **前端美感** — 若 CSS 不熟练，事件流页可能不够"精致"，可考虑用现成 UI 库（Element Plus / Ant Design Vue）。
4. **时区问题** — `publish_time` naive 混合 UTC/北京时间，论文里诚实标注"精度为天"。

---

## 九、下一步动作（2026-07-30 更新）

1. ✅ P5 修复已完成；P6 评测已完成。
2. **进入 P7 Vue 前端**：`npm create vite@latest frontend -- --template vue`
3. 后端加 `CORSMiddleware`。
4. 三个页面：onboarding / 事件流 / RAG 对话。
5. 同时刷新 PITFALLS.md 追加 P5-P6 踩坑。
6. 答辩前一周 `pg_dump` 快照。
