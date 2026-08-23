# 项目领域词汇 / CONTEXT

> Glossary of terms used across this system. Devoid of implementation details.
> 每次术语在 grilling 里钉死立刻更新此处。

## 核心实体

- **报道 (Report)** — 一家媒体发布的关于某一事件的一篇文章。有独立 URL、来源、发布时间、**完整原文**（正文停在一个说完了的句子上；截断残句、纯标题、纯署名不是报道，入库时拒收——完整性看结构不看长度，短快讯合法）。同一事件可被多家媒体各自报道，每篇都是一个 Report。
- **事件 (Event)** — 同一客观发生的事，多家媒体报道它们的共同主题时被聚合为一个 Event。一个 Event 挂 N 个 Report。
- **用户兴趣画像 (User Profile)** — 仅显式：用户首次进入时勾选 2–3 个兴趣标签。**不做行为画像**（设计决策，规避个保法）。

## 关系

- **1 事件 : N 报道** — 多家报道同事件合并为一个 Event，每 Report.event_id 指向其归属 Event。

## 状态枚举

- **拒答 (Refusal)** — RAG 答案判定为"context 不足"时主动说"信息不足"，不硬编。防幻觉指标之一，与 faithfulness 并列。

## 处理动作

- **挂载 (Attach)** — 新 Report 判定为某 Event 同事件时，将其 Report.event_id 指向该 Event，并触发后台异步重计算 Event.merged_summary 与 Event.embedding。
- **新建事件 (Spawn)** — 新 Report 的关键词与已有事件无重叠，或向量不相似 (≤0.90)，创建新 Event，自身成为首篇 Report。
- **去重 (Dedup)** — 按 original_url 去重（P1）+ 按语义向量跨事件去重（P3）。两种去重不同层。
- **事件中心重算 (Event Re-embed)** — 每次挂载新报道后异步 LLM 合并所有 Report 摘要→重 embed，避免首篇措辞偏置事件向量中心。

## 输出层

- **多源度 (Source Breadth)** — 覆盖同一事件的**不同媒体来源数**，事件列表/简报/RSS 的唯一排序与展示口径。_Avoid_: "热度/报道篇数"口径（source_count 数的是报道篇数，同站多稿会虚高，已废弃为产品概念）。
- **每日简报 (Daily Digest)** — `GET /api/digest?date=`，结构化清单：当日事件按**多源度降序**排序取 Top-10，复用 merged_summary，零新增 LLM 调用，逐字段可追溯。
- **事件流输出 (Event Feed)** — `GET /feed/events.rss`，RSS 2.0 订阅源：每条一个 Event，link 指向首篇报道原文，description 为合并摘要 + 来源列表。

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
- **set recall@5** — 部分得分制：|Top-5 ∩ gold| / min(|gold|, 5)。在全部评测行上计算（含拒答行——拒答行只要检索器返回了候选即记 0 分），因此它衡量"检索+生成"整体，不单是检索质量。
- **检索题 / 拒答题 (Retrieval / Refusal Question)** — Gold 集两类题：检索题有非空 gold 事件集；拒答题 gold 为空、期望系统拒答。两者指标分开报告——检索题看 set recall@5，拒答题看 refusal accuracy，不混在一个平均数里。
- **分层报告 (Stratified Reporting)** — 主表报全量平均，旁边必须附分层拆解（检索题均值 + 拒答题均值），防止两类题互相稀释对方指标。

## 数据层级

- **第一层 报道 (news_report)** — 每篇 RSS 项一行，含摘要、分类、embedding。
- **第二层 事件 (news_event)** — 多篇同事件合并，含合并摘要、关键词、分类；展示口径为多源度。

## 边界

- 系统目的 = 可验证地证明两件事：语义向量能做事件级去重聚合、受约束生成 + 强制引用降低幻觉。
- **事件级 RAG 是设计选择，非实验主张**：动机是溯源粒度（引用到事件 = 引用一组同源报道，可交叉查证）与 merged_summary 的表示质量。片段级对比属未来工作（公平对比需先解决片段切分与 gold 标注粒度对齐）。消融表证明的是"事件级管道内每段有贡献"，不承担"事件级优于片段级"的证明义务。
- 系统不做：个性化推荐（行为画像)、真立场谱分析、付费墙内容复制。
- 系统选 A 模式：抓全文入库不删，用于摘要研究。
