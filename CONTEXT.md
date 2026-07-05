# 项目领域词汇 / CONTEXT

> Glossary of terms used across this system. Devoid of implementation details.
> 每次术语在 grilling 里钉死立刻更新此处。

## 核心实体

- **报道 (Report)** — 一家媒体发布的关于某一事件的一篇文章。有独立 URL、来源、发布时间、原文。同一事件可被多家媒体各自报道，每篇都是一个 Report。
- **事件 (Event)** — 同一客观发生的事，多家媒体报道它们的共同主题时被聚合为一个 Event。一个 Event 挂 N 个 Report。
- **事实槽位 (Fact Slot)** — 一篇报道描述事件的 6 个事实维度之一：who / what / when / where / why / howmany。每篇 Report 有 6 行 Fact Slot。
- **事件级事实 (Event-level Fact)** — 同一 Event 下所有 Report 的某槽位做合并后得到的事件事实。一 Event 有 6 个事件级事实（5W1H 各一）。
- **用户兴趣画像 (User Profile)** — 仅显式：用户首次进入时勾选 2–3 个兴趣标签。**不做行为画像**（设计决策，规避个保法）。

## 关系

- **1 事件 : N 报道** — 多家报道同事件合并为一个 Event，每 Report.event_id 指向其归属 Event。
- **1 报道 : 6 事实槽位** — 每 Report 固定抽 5W1H 共 6 行。
- **1 事件 : 6 事件级事实** — 同槽位跨多篇 Report 合并后的事件级事实，存 NewsEvent.fact_slots JSONB。

## 状态枚举

- **冲突分级 (Conflict Grade)** — 事件级事实合并时按 4 档判别：

  | 状态 (status) | 含义 | 前端呈现 |
  | :--- | :--- | :--- |
  | `consistent` | 多家报道槽位值完全一致 | 绿色单值 |
  | `merged` | 值可合并（同粒度差或子集关系） | 绿色合并值 |
  | `uncertain` | 时间差 ≤24h 等疑似同一值 | 灰色多值候选不标红 |
  | `conflict` | 显著不同 | **红色**保留全部候选值 |

- **拒答 (Refusal)** — RAG 答案判定为"context 不足"时主动说"信息不足"，不硬编。防幻觉指标之一，与 faithfulness 并列。

## 处理动作

- **挂载 (Attach)** — 新 Report 判定为某 Event 同事件时，将其 Report.event_id 指向该 Event，并触发后台异步重计算 Event.merged_summary 与 Event.embedding。
- **新建事件 (Spawn)** — 新 Report 的关键词与已有事件无重叠，或向量不相似 (≤0.75)，创建新 Event，自身成为首篇 Report。
- **去重 (Dedup)** — 按 original_url 去重（P1）+ 按语义向量跨事件去重（P3）。两种去重不同层。
- **事件中心重算 (Event Re-embed)** — 每次挂载新报道后异步 LLM 合并所有 Report 摘要→重 embed，避免首篇措辞偏置事件向量中心。

## 检索管道子段

- **时间预过滤** — SQL 按 publish_time 过滤候选 Event，堵 embedding 不认时间的洞。
- **向量召回 (Top-20)** — pgvector ANN cosine，召回导向粗筛。
- **重排 (Rerank Top-5)** — bge-reranker cross-encoder 精排，提 precision。
- **受约束生成 (Constrained Generation)** — Prompt 约束 LLM "只能用 context，否则说信息不足"，强制引用每句事件 ID。

## 评测术语

- **Gold 事件集 (Gold Event Set)** — 问题人工标的完整正确答案事件集合。set recall@5 的分母。
- **Ground Truth** — 必须人工盲标，不能用系统输出当标签（否则循环论证）。
- **快照 (Database Snapshot)** — 答辩前一周的 pg_dump 快照，两套评测集都基于它构造，保证可复现。
- **消融 (Ablation)** — 把系统某段砍掉重跑评测看指标掉多少，证明该段真有贡献。本系统 5 行消融 baseline。

## 数据层级

- **第一层 报道 (news_report)** — 每篇 RSS 项一行。
- **第二层 事件 (news_event)** — 多篇同事件合并。
- **第三层 事实槽位 (news_report_fact)** — 每篇 6 行 5W1H。

## 边界

- 系统目的 = 可验证地证明三件事：语义向量能做事件级去重聚合、事件级 RAG 召回优于片段 RAG、受约束生成 + 强制引用降低幻觉。
- 系统不做：个性化推荐（行为画像)、真立场谱分析、付费墙内容复制。
- 系统选 A 模式：抓全文入库不删，用于摘要和事实抽取研究。