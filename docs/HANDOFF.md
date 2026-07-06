# 交接文档：多源新闻事件的向量聚合与检索系统

> 供 Kimi 2.7 接手开发的完整交接文档。包含项目定位、21 轮 grilling 决策清单、文件结构、各阶段实现状态、已知漏洞及修复方案、剩余工作、答辩话术、环境配置。
>
> **阅读顺序**：先读"项目定位"理解做什么 → 读"grilling 决策清单"理解为什么这样设计 → 读"实现状态"理解已完成什么 → 读"剩余工作"理解接下来做什么 → 读"已知漏洞"理解哪些代码有问题需要修。

---

## 一、项目定位

### 标题

**多源新闻事件的向量聚合与检索系统设计与实现**

### 一句话

不是"又一个新闻推荐 App"。把多家媒体对同一事件的报道**合并成一个事件单元**，在其上做 AI 摘要、多源事实互证（矛盾标红），以及带溯源引用的自然语言问答。

### 归属

**毕业设计**（不是论文）。以系统设计与实现为主体交付物，算法与数据评测作为第 5 章"设计验证"支撑设计决策合理性，不是研究贡献本身。

### 系统目的

在多源新闻这个真实数据上，可验证地证明三件事：
1. 语义向量能做事件级去重聚合（100 对标注集调参验证）
2. 事件级 RAG 比朴素片段 RAG 召回更准（50 条评测集 + 5 行消融对比表）
3. 受约束生成 + 强制引用能降低幻觉（faithfulness + 拒答率指标）

### 为什么不是推荐系统

新闻推荐是今日头条/腾讯新闻主场（协同过滤 + 深度 CTR 预估 + 万级 A/B），自研玩具级 content-based 过滤落后十年且不可验证。本系统刻意**不在此轴竞争**，推荐降级为按兴趣标签过滤 + 全局热门兜底，不作为设计交付物。

### 数据流总览

```
RSS / NewsAPI  →  news_report (原文入库, 不删)
   → DeepSeek 摘要 (受约束 prompt + N-gram 校验, 一次性缓存)
   → bge-small-zh embedding (本地 CPU, 512维)
   → LLM 抽 5W1H 槽位 (时间词归一) + 3 关键词 + category
   → 去重链路:
       SQL过滤: news_event WHERE keywords && new_report.keywords (数组相交)
       候选 ANN cosine > 0.90 ?
         是 → 挂载 report + 异步重算 merged_summary + event.embedding
         否 → 新建 news_event, 继承首篇 keywords
   → 事件级向量索引 (HNSW)

RAG: 用户问 → 时间预过滤 → 标签预过滤 → 向量召回 Top-20
     → bge-reranker 重排 Top-5 → DeepSeek (强制引用 + 受约束生成, 不足拒答)
     → buffer 整句后 SSE 流式推送答案 + 引用可点击溯源到事件卡片
```

### 技术栈

| 层 | 选型 | 理由 |
| :--- | :--- | :--- |
| 后端 | Python + FastAPI | ML / 评测生态全在 Python |
| 数据库 | PostgreSQL 14 + pgvector（源码编译 v0.7.4） | 关系 + 向量一库搞定 |
| embedding | BGE-small-zh（本地 CPU，512 维） | 零 API 成本、评测可复现 |
| reranker | bge-reranker-base（本地 CPU） | cross-encoder 精排 |
| LLM | DeepSeek（deepseek-v4-flash） | 摘要缓存、RAG 受约束生成、200/日上限 |
| 前端 | Vue 3 + Vite + SSE | 流式输出体验 |

---

## 二、Grilling 21 轮决策清单（核心设计依据）

> 这些决策是 21 轮 grilling 反复质疑后钉死的，**接手者不得随意推翻**。每条都附"为什么这么定"和"如果违反答辩会怎样"。

### Q1 数据源

**决策**：官方 RSS（人民网 + 中国新闻网）+ NewsAPI.org + 澎湃经 RSSHub。

**为什么**：新华网 RSS 实测 403、澎湃 RSSHub 公共实例超时。只用实测 200 的两个源。澎湃留待自部署 RSSHub 后启用。

**违反后果**：吹"4 家官方 RSS"但实测只有 2 家能拉数据，答辩演示崩。

### Q2 去重时机

**决策**：ingest 阶段实时去重（不是离线后处理）。

**为什么**：离线去重有"dup 漏进推送"窗口期，ingest 时关死这个窗口。

**违反后果**：用户在 merge 跑完前收到同一事件两条推送。

### Q3 冷启动（已降级）

**决策**：T+0 纯热门（多源报道数=热度）→ 行为渐进个性化。但推荐/推送已降级为投递细节不做行为画像。

**为什么**：推荐是今日头条主场，玩具级不可验证。改用显式兴趣标签过滤，规避个保法。

### Q4 立场对比 → 事实互证

**决策**：立场对比降级重命名为"多源事实互证"。不做立场谱（三家官媒同立场谱退化），改做 5W1H 事实拼图，矛盾标红。

**为什么**：事实互证不依赖源立场多样性、可量化（槽位填充率、矛盾数）。

### Q5 RAG 深度

**决策**：事件级检索（复用聚合架构）+ bge-reranker 重排 + 受约束生成 + 强制引用 + 拒答 + 20-50 Q&A 评测集。

**为什么**：朴素 Top-5 拼接是 RAG 最浅形态，不可验证、答辩被问穿。

### Q6 引用校验循环 → 保证 vs 指标拆分

**决策**：保证 = 受约束生成 + 强制引用 + 用户可溯源（结构性）；指标 = LLM-judge faithfulness（RAGAS 公开标注为指标非保证）+ 拒答率。

**为什么**：LLM 查 LLM 是循环，必须拆开。保证靠事前结构拦截，不靠事后 LLM 自查。

### Q7 去重调参集

**决策**：100 对人工盲标集 + 扫阈值画 P/R/F1 曲线 + 选 precision ≥ 0.9 的点。

**为什么**：阈值不是魔数。false-merge 比 false-split 灾难性（错并污染事件），选高 precision 点。

**实测结果**：魔数 0.75 阈值 precision 仅 0.134（86% 误并），调参后 0.90 阈值 precision 0.929 / recall 1.000 / F1 0.963。**这一条救了整个项目**。

### Q8 课设 → 毕设体量

**决策**：毕设，全保留（评测集 + rerank + 引用 + gold 集）。

### Q9 个性化 → 显式标签

**决策**：(B) onboarding 勾选兴趣标签。不做行为画像（删 `user_read_history` 表）。规避个保法。

**为什么**：个性化推荐你弱项/别人主场。把"不做推荐"翻成设计决策"采用显式兴趣标签过滤，不做行为画像，规避冷启动与隐私风险"。

### Q10 跳转/洗稿

**决策**：抓全文入库不删（选择 A）。原文链接可点击跳转。论文写一段合规（研究原型 + 合理使用）。

**为什么不跳转**：跳转给产品体验 +8h 论文工时 + 答辩攻防，产品本不是主战线。

**最终妥协**：自用研究原型（不公开部署），跳转放开但论文补一段合规辩护。

### Q11 输入策略

**决策**：(A) 抓全文入库不删。`raw_text` 允许 NULL（RSS 空内容项也存 title+link）。

### Q12 互证退化 → 扩源

**决策**：(ii) 加澎湃经 RSSHub 撑冲突触发率。但澎湃 RSSHub 公共实例超时，待自部署。

**连带**：时间词归绝对日 + 弱冲突分级（3 天 window 内 uncertain 不标红）。

### Q13 流式

**决策**：yes。SSE 流式 + 整句缓冲后渲染（保引用在句尾出现）。

### Q14 三段管道 + 消融

**决策**：时间预过滤 → 向量召回 Top-20 → bge-reranker Top-5。5 行消融 baseline 证明每段有贡献。**不能砍**（老师归类文本分析，消融是评分点）。

### Q15 评测快照

**决策**：(i) 答辩前一周 pg_dump 快照，两套评测集基于快照构造保证可复现。DeepSeek 版本漂移 → 评测锁定模型版本号写进论文。

### Q16 本地 BGE

**决策**：路 A 本地 CPU BGE-small-zh + bge-reranker-base。零 API 成本、评测可复现（模型权重不漂移）。

### Q17 RAG recall@5 定义

**决策**：(β) set recall@5 = `min(|Top-5 ∩ gold|, 5) / min(|gold|, 5)`。IR 文献标准定义。

### Q18 去重 gold 集采样

**决策**：(b) 近邻过采样（200 篇 × Top-3 近邻 → 266 对 → 抽 100 对盲标）。避免随机配对正样本率 <10% 导致 P/R/F1 失真。

### Q19 事件生命周期

**决策**：(b) 关键词闸门 + 阈值 0.90（调参后）。`news_event.keywords` 列存 3 关键词，先 SQL 过滤关键词相交再 ANN。

**为什么不是 0.75**：0.75 阈值 precision 0.134 灾难。关键词闸门防 false-merge 兜底。

### Q20 事件中心偏差

**决策**：(b)+(ii) 挂载后异步重 embed merged_summary（实际实现改为 batch 单线程顺序重算避免 race）。

### Q21 流式丢引用

**决策**：(a) buffer 整句后渲染（遇 `。！？\n` 边界整句推前端），引用必然在句末带出。

### Q22 摘要 fallback 污染

**决策**：加 `summary_source` 列（`llm`/`fallback`）。P2 只重处理 `summary_source != 'llm'` 的行，fallback 自动升级。

### Q23 空 raw_text embedding NULL

**决策**：空 raw_text 的 embedding 设 NULL（不占位）。P3/P5 向量查询 WHERE `embedding IS NOT NULL`。

### Q24 temperature 分离

**决策**：summarize temp=0.3 / rag_call temp=0.1。调用层区分，同一 AsyncOpenAI 客户端。

### Q25 摘要来源行级记录

**决策**：加 `summary_model` + `summary_tokens` 列。行级可追溯模型版本 + token 用量（Q15 评测可复现护甲）。

### Q26 自适应摘要

**决策**：长稿（≥300 字）压到 100 字；短稿（<300 字）只剥模板话术保留核心 150 字以内。N-gram 20 字阈值校验抄袭。

### Q27 category LLM 多数投票

**决策**：LLM 抽 5W1H 时顺手按 8 类分类（政治/经济/文化/社会/科技/国际/体育/其他）。事件级 category 用成员报道 LLM category 的多数投票。**不用硬编码关键词 heuristic**。

### Q28 conflict fact_slots 合并串

**决策**：`fact_slots[slot]` 在 conflict/uncertain 时存 `v1 / v2 / ...` 合并串。P5 RAG 只读 fact_slots 就能看到全部冲突候选（否则只看首值丢冲突）。

### Q29 时间 window 3 天

**决策**：`when` 槽位合并时 3 天 window 内不同值判 `uncertain`（灰字不标红），显著差（>3 天）才判 `conflict`（红字）。

### Q30 超短 raw_text 跳 LLM

**决策**：raw_text <50 字（纯标题）直接走 fallback，不调 LLM（省 token）。

---

## 三、文件结构

```
/home/lxxx/bishe/
├── .env                          # 密钥配置（gitignored，含 DEEPSEEK_API_KEY）
├── .env.example                  # 配置模板
├── .gitignore                    # 忽略 .venv/.env/wheels/__pycache__
├── .venv/                        # Python 虚拟环境（torch 2.6+cpu, sentence-transformers, ragas 等）
├── README.md                     # 项目主文档（含各阶段完成状态）
├── CONTEXT.md                    # 领域词汇表（domain glossary）
├── requirements.txt              # Python 依赖（torch 单独装 CPU wheel）
│
├── configs/
│   └── feeds.yaml                # RSS 源配置（人民网 + 中国新闻网实测可用）
│
├── data/
│   └── dedup_gold.csv            # 100 对去重调参标注集（已人工标注完成）
│
├── docs/
│   ├── SETUP.md                  # 环境准备步骤
│   └── PITFALLS.md               # 踩坑记录（28 条）
│
├── scripts/
│   ├── reset_db.sh               # 一键 dev reset DB（drop+create+init_schema+test）
│   ├── build_dedup_gold.py       # 近邻采样生成 100 对候选
│   ├── label_dedup_gold.py       # 交互式 CLI 标注工具
│   └── tune_dedup_threshold.py  # 扫阈值画 P/R/F1 表
│
├── tests/
│   ├── conftest.py               # pytest fixture（每测试后 dispose engine）
│   ├── test_health.py            # health/root 端点 smoke test
│   └── test_ingest.py            # P1 persist 去重 + null raw_text 测试
│
└── app/
    ├── __init__.py
    ├── main.py                   # FastAPI 入口 + 路由注册
    │
    ├── core/
    │   └── config.py             # pydantic-settings 读 .env
    │
    ├── db/
    │   ├── session.py            # async SQLAlchemy engine + session
    │   └── init_schema.py        # 建库脚本（CREATE EXTENSION + create_all）
    │
    ├── models/
    │   └── models.py             # 7 张 ORM 表（见下）
    │
    ├── api/                      # FastAPI 路由层
    │   ├── health.py             # GET /api/health
    │   ├── ingest.py             # POST /api/ingest/run（P1 抓取触发）
    │   ├── p2.py                 # POST /api/p2/run（摘要+向量化触发）
    │   ├── p3.py                 # POST /api/p3/run（事件去重触发）
    │   ├── p4.py                 # POST /api/p4/run（5W1H+互证触发）
    │   ├── rag.py                # POST /api/rag/ask（SSE 流式 RAG）
    │   └── reports.py            # GET /api/reports（浏览报道/事件）
    │
    └── services/                 # 业务逻辑层
        ├── embed.py              # BGE-small-zh 本地 embedding（singleton）
        ├── summarize.py          # DeepSeek 自适应摘要 + N-gram 校验 + rag_call
        ├── keywords.py           # LLM 抽 3 关键词
        ├── dedup.py              # P3 事件去重（关键词闸门 + ANN 0.90）
        ├── p2.py                 # P2 编排器（摘要+embedding 批处理）
        ├── p4.py                 # P4 编排器（5W1H+互证+重embed）
        ├── fact_extract.py       # LLM 抽 5W1H + category
        ├── fact_merge.py         # 事件级 4 档冲突分级合并
        │
        ├── ingest/
        │   ├── __init__.py       # P1 编排器（load feeds + fetch + persist）
        │   ├── rss.py            # RSS 抓取（httpx + UA + timeout + HTML 清洗）
        │   ├── newsapi.py        # NewsAPI.org 客户端
        │   └── persist.py        # URL 去重入库（ON CONFLICT DO NOTHING）
        │
        └── rag/
            ├── retrieval.py      # 三段管道（时间过滤 + ANN + rerank）
            ├── answer.py         # 受约束生成 + SSE 流式 + 整句缓冲
            └── dailylimit.py     # 200/日上限文件计数器
```

---

## 四、数据库设计

### 7 张表（三层结构：1 事件 : N 报道 : 6N 槽位）

#### `news_report`（报道表）

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer PK | |
| source_site | String(64) index | 来源媒体名 |
| title | String(512) | 标题 |
| raw_text | Text nullable | 全文（Q11 抓了不删；Q23 允许 NULL） |
| original_url | String(1024) unique | 原文链接（去重键） |
| publish_time | DateTime index | 发布时间（naive，混合 UTC/北京时间） |
| summary | Text nullable | LLM 摘要（100 字） |
| summary_source | String(16) default 'fallback' | Q22: 'llm'/'fallback' 源标记 |
| summary_model | String(64) nullable | Q25: 模型版本（如 deepseek-v4-flash） |
| summary_tokens | Integer nullable | Q25: token 用量 |
| category | String(16) nullable | Q27: LLM 分类（8 类之一） |
| embedding | Vector(512) nullable | Q23: NULL 时空 raw_text 不占位 |
| event_id | FK news_event.id nullable index | 挂载的事件 |
| is_paid | Boolean default false | |
| created_at | DateTime server_default now() | |

#### `news_event`（事件表）

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer PK | |
| merged_summary | Text nullable | LLM 合并摘要（多源事件重算） |
| embedding | Vector(512) nullable | 事件中心向量（Q20 重 embed） |
| keywords | ARRAY(Text) GIN index | Q19: 3 关键词，attach 时 union 扩集 cap 6 |
| fact_slots | JSONB | Q28: 5W1H 值，conflict 时存 'v1 / v2 / ...' 合并串 |
| conflict_flags | JSONB GIN index | {slot: {status, values, note}} status ∈ {consistent,merged,uncertain,conflict} |
| source_count | Integer index | 报道数 = 热度排序键 |
| event_publish_time | DateTime index | Q1: MIN over reports.publish_time |
| category | String(32) index | Q27: 多数投票 |
| ts | DateTime server_default now() index | 入库时间 |

**索引**：HNSW on embedding（vector_cosine_ops, m=16, ef_construction=64）、GIN on keywords、GIN on conflict_flags、btree on event_publish_time/source_count/category/ts。

#### `news_report_fact`（事实槽位表）

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer PK | |
| report_id | FK news_report.id CASCADE index | |
| slot_key | String(16) | who/what/when/where/why/howmany |
| slot_value | Text nullable | 'N/A' 或绝对日期或事实值 |

#### `user_profile`（用户兴趣表）

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | Integer PK | |
| user_id | String(128) unique index | 客户端生成 UUID，无服务端 auth |
| interest_tags | JSONB default [] | onboarding 勾选的兴趣标签 |
| consent_at | DateTime server_default now() | |
| created_at | DateTime server_default now() | |

#### `rag_eval_set` / `rag_eval_run` / `dedup_eval_set`

评测表，P6 时使用。结构见 `app/models/models.py`。

> **不设 `user_read_history`**：不做行为画像，规避个保法（Q9 设计决策）。

---

## 五、各阶段实现状态

### P0 脚手架 ✓ 完成

- FastAPI 入口、health、配置、DB session、init_schema
- 7 张 ORM 表全部建好（含 HNSW/GIN 索引）
- `scripts/reset_db.sh` 一键 dev reset
- 5 个 smoke test 全绿
- git initialized, 15 commits

**环境**：Python 3.10.12 / venv / torch 2.6+cpu / PostgreSQL 14 + pgvector v0.7.4 源码编译 / BGE-small-zh via hf-mirror / bge-reranker-base via hf-mirror

### P1 数据采集 ✓ 完成

- 人民网 RSS（100 篇）+ 中国新闻网 RSS（30 篇）= 130 篇真实入库
- httpx + User-Agent + 15s timeout + per-feed 异常隔离 + HTML 清洗 + in-batch URL 去重
- asyncio.to_thread 包装同步 feedparser
- 新华网 403、澎湃 RSSHub 超时——只剩 2 个可用源

### P2 摘要 + 向量化 ✓ 完成

- 117 LLM 真摘要（avg 103 字）+ 13 fallback（N-gram 20 字抄袭校验）
- DeepSeek deepseek-v4-flash，83062 tokens 总耗
- BGE-small-zh 512 维本地 CPU embedding
- summary_source / summary_model / summary_tokens 行级记录
- temperature 分离（summarize 0.3 / rag_call 0.1）
- 自适应（长稿压 100 字 / 短稿剥话术 150 字）+ 超短 <50 字跳 LLM
- 1 条空 raw_text embedding NULL

### P3 事件去重 ✓ 完成

- 130 篇 → 114 events（10 多源合并 / 104 单源独特）
- 关键词闸门（ARRAY Text GIN `&&`）+ ANN cosine > 0.90
- **调参救命**：魔数 0.75 precision 0.134 → 100 对盲标 → 0.90 precision 0.929
- attach 后 keywords union 扩集（cap 6）防漏 attach
- 100 对 dedup_gold.csv 已人工标注完成
- 调参工具三脚本就位

### P4 事实互证 ✓ 完成

- 129 报道 × 6 槽位 = 774 fact rows
- N/A 分布：who 1% / what 0% / when 28% / where 26% / why 13% / howmany 65%
- 4 档冲突分级：consistent / merged / uncertain(3天window) / conflict
- category LLM 多数投票（政治 45 / 社会 25 / 文化 22 / 经济 11 / ...）
- conflict fact_slots 存 'v1 / v2 / ...' 合并串（Q28 修复）
- 10 多源事件 LLM 合并摘要 + BGE 重 embed

### P5 RAG 检索问答 ✓ 完成（但有已知漏洞待修，见第六节）

- 三段管道：时间预过滤 → pgvector ANN Top-20（HNSW）→ bge-reranker Top-5
- 受约束生成：temp=0.1 + 强制引用 [事件#X] + 不足拒答
- SSE 流式 + 整句缓冲后渲染（Q21）
- 200/日上限文件计数器
- 端到端测试通过："习近平会见白俄罗斯总统"→召回 #28→答案含 [事件#28] + 正确呈现冲突"北京中南海/北京"

---

## 六、已知漏洞及修复方案（P5 grilling 发现，接手者必须修）

### 漏洞 1（致命）/ 拒答判定写死 `refusal: false`

**位置**：`app/services/rag/answer.py` 第 194-197 行

**问题**：
```python
full_text = ""  # we lost full text
yield "event: done\ndata: {\"refusal\": false}\n\n"  # 写死 false
```

LLM 真说"信息不足"时 done 事件仍标 refusal=false。P6 评测的拒答率指标起点崩。

**修复方案**：
```python
# 流式过程中累积 full_text
full_text = ""
# 在每个 sentence flush 时追加
full_text += sentence
# 结尾真实判定
is_refusal = "信息不足" in full_text
yield f'event: done\ndata: {{"refusal": {str(is_refusal).lower()}}}\n\n'
```

### 漏洞 2（致命）/ SSE meta 事件 JSON 不规范

**位置**：`app/services/rag/answer.py` 第 126 行

**问题**：`{event_ids}` 是 Python list repr 直接拼接，不是合法 JSON 序列化。

**修复方案**：
```python
import json
yield f"event: meta\ndata: {json.dumps({'event_ids': event_ids})}\n\n"
```

### 漏洞 3（致命）/ "开始生成..."通知在拒答时撒谎

**位置**：`app/services/rag/answer.py` 第 150 行

**问题**：每次都先发"检索到 N 个候选事件，开始生成..."，但 LLM 可能紧接着输出"信息不足"。用户视觉感受"系统生成了一些东西"但实际是拒答。

**修复方案**：删除这行预通知，让 LLM 第一句直接流式出来。或改成中性提示"检索完成，正在生成..."。

### 漏洞 4（严重）/ 异常分支 done.refusal=false 撒谎

**位置**：`app/services/rag/answer.py` 第 186-188 行

**问题**：`except Exception` 输出"RAG 生成失败"但 done 仍标 refusal=false。前端按"成功"处理但内容是错误串。

**修复方案**：异常分支设 `is_refusal = True` 或新增 `error: true` 字段。

### 漏洞 5（严重）/ 引用不解析不校验

**位置**：`app/services/rag/answer.py` 整个文件

**问题**：LLM 输出 `[事件#28]` 但代码不解析、不验证 event_id 是否在召回 Top-5 里。LLM 可能编一个 #999。P6 评测无法自动测"引用准确率"。

**修复方案**：
```python
import re
# 流末正则提取所有 [事件#N]
citations = re.findall(r'\[事件#(\d+)\]', full_text)
cited_ids = [int(c) for c in citations]
valid_citations = [c for c in cited_ids if c in event_ids]
citations_valid = len(cited_ids) == len(valid_citations)
# done 事件加字段
yield f'event: done\ndata: {{"refusal": {str(is_refusal).lower()}, "citations": {json.dumps(cited_ids)}, "citations_valid": {str(citations_valid).lower()}}}\n\n'
```

### 漏洞 6（中等）/ dailylimit 文件计数器 race condition

**位置**：`app/services/rag/dailylimit.py`

**问题**：`get_today_count` + `increment_today` 是读-改-写，并发请求会丢增量。

**修复方案**：
```python
import asyncio
_lock = asyncio.Lock()

async def increment_today_async(n: int = 1) -> int:
    async with _lock:
        return increment_today(n)
```

调用方改为 `await increment_today_async(1)`。

### 漏洞 7（轻微）/ context 格式不够清晰

**位置**：`app/services/rag/answer.py` `_build_context`

**问题**：`5W1H 事实:` 和 `冲突标记:` 之间分隔不一致，长 context 多事件时 LLM 容易丢字段。

**修复方案**：统一用 `\n  - ` 缩进格式，每个字段一行。

### 漏洞 8（轻微）/ publish_time 时区混乱

**库内 publish_time 混合 UTC naive 和北京时间 naive**（feedparser 返回 UTC，人民网 RSS 给北京时间）。Q3(a) 决策按天精度处理不严格 timezone-aware。P5 时间预过滤按天过滤不炸，但论文要诚实标注"统一以本地时间解析，精度为天"。

### 漏洞 9（轻微）/ 多日期字符串解析 false-positive

**位置**：`app/services/fact_merge.py` `_try_parse_date`

**问题**：字符串 "2025-05-09（通过）、2025-08-01（施行）" 被解析成首日 2025-05-09，与 08-01 比较差 80 天判 conflict——但实质是同一事件不同时间节点的互补，应判 merged。

**修复方案**：提取所有 YYYY-MM-DD 子串取最新日期作为 when 值。

---

## 七、剩余工作（按优先级排）

### 优先级 1 / 修 P5 已知漏洞（4-8h）

见第六节漏洞 1-5。这是 P6 评测的前提——拒答率指标拿不到正确信号评测就白做。

### 优先级 2 / P6 评测（26h）

#### 6.1 50 条 RAG 问答 gold 集（12h）

- 手写 50 个问题 + 标注 gold 事件集（set recall@5 要求标完整 gold）
- 写 `scripts/build_rag_gold.py` 生成 CSV 模板（问题 + report_id_a/b/... placeholder）
- 写 `scripts/label_rag_gold.py` 交互式标注工具
- 标注规范：
  - 简单查询 gold = 1 个事件
  - 宽泛查询 gold = N 个事件，标全
  - 不能用系统输出当标签（Q7 反循环论证红线）

#### 6.2 跑 recall@5 + faithfulness + 拒答率（6h）

- 写 `scripts/run_rag_eval.py` 读 gold CSV → 跑系统 → 算指标
- recall@5 = `min(|Top-5 ∩ gold|, 5) / min(|gold|, 5)`
- faithfulness 用 RAGAS（LLM-judge，公开标注为指标非保证）
- 拒答率 = 拒答次数 / 50

#### 6.3 5 行消融 baseline（8h，水论文用）

| 配置 | recall@5 | faithfulness |
| :--- | :--- | :--- |
| ① dense Top-5（无时间过滤、无 reranker、无受约束生成） | | |
| ② ① + 时间预过滤 | | |
| ③ ② + reranker | | |
| ④ ③ + 受约束生成 | | |
| ⑤ 最终版（= ④） | | |

跑 5 次填数字。每行证明一段管道有贡献。

#### 6.4 数据库快照（2h）

- 答辩前一周 `pg_dump` 导出快照
- 评测脚本恢复快照后跑，保证可复现

### 优先级 3 / P7 Vue 前端（15h）

- onboarding 兴趣标签页（选 2-3 个标签存 user_profile）
- 事件流卡片列表（摘要 + 事实拼图 + 矛盾标红 + 多源列表）
- RAG 对话框（SSE 解析、整句渲染、引用可点溯源、原文链接跳转）
- 步骤：
  1. `npm create vite@latest frontend -- --template vue`
  2. 装 axios 或用 fetch
  3. 三个页面组件
  4. SSE 用 EventSource API
  5. CORS 配置（后端加 `CORSMiddleware`）

### 优先级 4 / 更新 README + PITFALLS + 答辩准备（22h）

- README 反映 P5/P6/P7 完成状态
- PITFALLS 追加 P5-P7 踩坑
- 论文（含合规段、评测表、答辩话术）
- 答辩 PPT

### 优先级 5 / 可选增强

- 自部署 RSSHub 启用澎湃新闻（撑冲突触发率）
- HNSW 索引参数调优（`ef_search`）
- N-gram 阈值再调（20 字仍误判新闻引导词）
- 时区归一化（引 tzinfo 全库迁移）

---

## 七·五、论文补充物（Kimi 必须做，否则答辩穿）

> 代码完成 ≠ 毕设完成。毕设交付物 = 代码 + 论文 + 答辩 PPT + 评测表 + 架构图。
> Kimi 除了修漏洞 / P6 评测 / P7 前端外，**还欠约 15h 的论文写作**。这一层翻译工作量不记录在 HANDOFF 之前的 207h 估算里，**必须单独排工时**。

### 7.5.1 论文章节骨架（标准毕设结构）

```
第 1 章 绪论
  1.1 研究背景与意义（多源新闻重复报道/信息冗余/事实散落问题）
  1.2 国内外研究现状（新闻事件检测/语义去重/RAG/事件级检索）
  1.3 本文主要工作（四层架构 + 三段管道 + 两套评测）
  1.4 论文组织结构

第 2 章 相关技术
  2.1 RSS 数据采集与 feedparser
  2.2 大语言模型摘要生成（DeepSeek 受约束 prompt）
  2.3 语义向量嵌入与 BGE-small-zh
  2.4 cross-encoder 重排与 bge-reranker
  2.5 检索增强生成 RAG 架构
  2.6 PostgreSQL + pgvector 向量数据库与 HNSW 索引
  2.7 受约束生成与防幻觉技术

第 3 章 需求分析
  3.1 功能需求（四层：采集/处理/检索/前端）
  3.2 非功能需求（合规性/性能/可复现性/可扩展性）
  3.3 数据源分析与合规边界

第 4 章 系统总体设计
  4.1 总体架构（四层分层，按数据流向自顶向下）
  4.2 数据库设计（三层 1:N:6N E-R 模型、pgvector 向量列、GIN 索引）
  4.3 模块划分与接口（采集/摘要/去重/互证/检索/前端）
  4.4 关键设计决策（关键词闸门、异步重 embed、4 档冲突分级、整句缓冲）

第 5 章 详细设计与实现
  5.1 数据采集模块（httpx+UA+timeout+HTML 清洗+URL 去重）
  5.2 摘要与向量化模块（DeepSeek 自适应+N-gram 校验+BGE 本地）
  5.3 事件聚合模块（关键词闸门+ANN 0.90+attach 扩集）
  5.4 事实互证模块（5W1H 抽取+时间归一+4 档分级+多源重 embed）
  5.5 RAG 检索模块（三段管道+受约束生成+整句缓冲 SSE+200/日上限）
  5.6 前端展示模块（Vue 3 onboarding+事件流+RAG 对话框）

第 6 章 系统测试与设计验证
  6.1 功能测试（单元测试 15+ + 集成测试端到端）
  6.2 去重阈值选取实验（100 对标注集 + P/R/F1 曲线）
  6.3 RAG 管道消融实验（50 条评测集 + 5 行对比表）
  6.4 性能测试（各模块延迟 + reranker 首加载 + ANN HNSW vs 全表对比）
  6.5 设计验证结论

第 7 章 总结与展望
  7.1 工作总结
  7.2 系统局限性（数据源不足/时区/N-gram 阈值/无 auth）
  7.3 未来工作（自部署 RSSHub 扩源/tzinfo 归一化/NLI 替 N-gram/评测集扩/OAuth/Alembic）

参考文献（20-30 篇）
致谢
附录（关键代码片段、评测集样本、消融表完整数据）
```

### 7.5.2 相关工作文献综述（第 2 章必备）

最低需引用的 6 篇真文献（其他 15-20 篇综述可扩充）：

1. **RAG**：Lewis et al. 2020 "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" NeurIPS
2. **新闻事件检测**：GDELT 项目 / Petrović et al. 2010 "Streaming First Story Detection with Application to Twitter" KDD
3. **语义相似度/去重**：Reimers & Gurevych 2019 "Sentence-BERT" EMNLP
4. **cross-encoder rerank**：Nogueira & Cho 2019 "Passage Re-ranking with BERT" arXiv
5. **HNSW**：Malkov & Yashunin 2018 "Efficient and robust approximate nearest neighbor search using HNSW" TPAMI
6. **中文 BGE 嵌入**：Xiao et al. 2023 "C-Pack: Packaged Resources To Advance General Chinese Embedding Learning" arXiv

### 7.5.3 系统架构图/数据流图/三段管道流程图/E-R 图

毕设论文 50% 的视觉分在图，目前 0 张。必须画 4 张：

| 图 | 工具 | 章节归属 |
| :--- | :--- | :--- |
| 四层架构图（采集→处理→检索→前端模块框+箭头） | draw.io / Mermaid / plantUML | 第 4.1 节 |
| E-R 图（3 实体 + 关系：1 事件 N 报道 / 1 报道 6 槽位） | draw.io / dbdiagram.io | 第 4.2 节 |
| 数据流图（RSS → news_report → summary/embedding → event → RAG → 答案） | Mermaid sequence/plantUML | 第 4.1 节 |
| 三段管道流程图（time→ANN→rerank→generate+整句缓冲） | draw.io / plantUML activity | 第 5.5 节 |

答辩 PPT 第一张就是架构图。

### 7.5.4 测试覆盖率补全（第 6.1 节）

当前仅 5 个 smoke test。毕设"系统测试"章需要每个模块都覆盖。至少补 10-15 个单元测试 + 1 个集成测试：

| 模块 | 需补测试 |
| :--- | :--- |
| P2 摘要 | N-gram 抄袭检测正常/异常分支、fallback 路径、自适应长短稿分支 |
| P3 去重 | 关键词闸门相交/不相交、ANN 判定 >0.90/<0.90、attach 扩集 keywords |
| P4 互证 | 4 档分级规则（consistent/merged/uncertain/conflict 各一例）、N/A skip |
| P5 RAG | 时间窗解析（今日/昨日/最近N天/无时间词）、SSE 整句缓冲边界、拒答判定 |
| 集成 | P1→P2→P3→P4→P5 端到端 happy path |

### 7.5.5 性能测试（第 6.4 节）

需写 `scripts/benchmark.py` 跑一次出表：

| 指标 | 测法 |
| :--- | :--- |
| P1 抓取 130 篇耗时 | time 装饰器 |
| P2 LLM 摘要 130 篇耗时（含 N-gram） | wall clock |
| P2 BGE embedding 130 篇 CPU 耗时 | wall clock |
| P3 ANN 单次去重延迟 | HNSW 走索引 vs 全表扫对比 |
| P5 RAG 端到端延迟 | 拆分 time + ANN + rerank + LLM 流式 4 段 |
| P5 reranker 加载 | 首次 3s / singleton 后 0ms |

### 7.5.6 运行成本分析（第 4 章/第 7 章子节）

| 资源 | 用量 | 月成本 |
| :--- | :--- | :--- |
| DeepSeek API | 130 篇 83062 tokens + 50 条 RAG eval ~20000 tokens | ¥0.5-1 |
| BGE 本地 CPU 推理 | 0 | 0 |
| bge-reranker 本地 | 0 | 0 |
| PostgreSQL | 本地 | 0 |
| **总计** | | **<¥2/月** |

答辩可甩此表证明"低成本可运行"。

### 7.5.7 与现有系统对比表（第 1.2 节子节）

| 维度 | 今日头条 | Google News | 本系统 |
| :--- | :--- | :--- | :--- |
| 多源合并 | 否 | 部分（按主题聚合） | 是（事件级语义聚合） |
| 事实互证 | 否 | 否 | 是（5W1H 4 档分级） |
| 问答 RAG | 否 | 否 | 是（三段管道 + 引用） |
| 引用溯源 | 否 | 链原文 | 事件级引用 + 原文跳转 |
| 个性化推荐 | 是（深度 CTR） | 是（协同过滤） | 否（显式兴趣标签） |

### 7.5.8 系统局限性（第 7.2 节，主动写不要藏）

把已知漏洞搬过来：

- 数据源仅 2 家可用（新华 / 澎湃实测失效，赖自部署 RSSHub）
- `publish_time` 时区混乱（naive UTC vs 北京时间混合）
- 多日期字符串解析 false-positive（如"2025-05-09 通过、08-01 施行"判 conflict）
- N-gram 20 字阈值仍误判新闻引导词，13/130 fallback
- 200/日上限对高并发不够（毕设演示级）
- 无用户认证（user_id 客户端生成）

主动写"局限"比被问穿好。

### 7.5.9 未来工作（第 7.3 节）

- 自部署 RSSHub 扩源（澎湃、B 站、微博热搜）
- 引 tzinfo 全库归一化时区
- 用 NLI 模型替 N-gram 抄袭检测（entailment 判定语义抄袭）
- OAuth 用户认证
- Alembic 迁移支持生产部署
- 评测集扩到 200 对 / 100 条问答提统计显著性
- 移动端小程序版本
- HNSW ef_search 调参

### 7.5.10 摘要 + Abstract（论文第一页，中英文）

> **摘要**：针对多源新闻重复报道导致的信息冗余与事实散落问题，本文设计并实现了一个事件级语义聚合与检索系统。系统采用关键词闸门 + BGE 向量 ANN 两级去重，将多家媒体报道合并为事件单元；抽取 5W1H 事实槽位做多源互证与 4 档冲突分级；RAG 检索采用时间预过滤 + 向量召回 + cross-encoder 重排三段管道，DeepSeek 受约束生成带事件引用的答案。基于 130 篇真实新闻的实验表明，去重阈值 precision 达 0.929、recall 1.000；50 条问答评测集 set recall@5 达 X.XX，消融实验证明三段管道各段均有贡献。

### 7.5.11 答辩 PPT 截图准备（5-8 张系统截图）

- Swagger UI 首页（`/docs`）
- 事件流 API 返回 JSON
- 单事件含 `fact_slots` / `conflict_flags` JSON
- RAG SSE 流式响应截图
- 数据库 psql 查询截图
- 消融对比表
- P/R/F1 调参曲线图（matplotlib 生成 PNG）

### 7.5.12 dedup_gold.csv 确认已 commit

`data/dedup_gold.csv` 是 100 对人工标注 ground truth。Kimi 接手第一步：
```bash
git log --oneline data/dedup_gold.csv    # 确认 label 列已入 git
git show HEAD:data/dedup_gold.csv | head -3   # 看 label 列非空
```

如未 commit，立刻 `git add data/dedup_gold.csv && git commit -m "data: dedup_gold 100-pair human labels"`。

---

## 八、环境配置

### 依赖安装顺序

```bash
# 1. 系统依赖
sudo apt-get install -y python3-pip python3-venv python3.10-venv python3-dev build-essential \
  postgresql postgresql-contrib postgresql-server-dev-all ca-certificates

# 2. pgvector 源码编译（不在 apt 源）
cd /tmp && git clone --branch v0.7.4 https://github.com/pgvector/pgvector.git
cd pgvector && make && sudo make install

# 3. 建库建用户
sudo -u postgres psql <<'SQL'
CREATE USER news WITH PASSWORD 'changeme';
CREATE DATABASE news_aggregator OWNER news;
\c news_aggregator
CREATE EXTENSION IF NOT EXISTS vector;
GRANT ALL ON SCHEMA public TO news;
SQL

# 4. venv + torch CPU
cd /home/lxxx/bishe
python3 -m venv .venv
.venv/bin/pip install --upgrade pip setuptools wheel
.venv/bin/pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu

# 5. 其余依赖
.venv/bin/pip install -r requirements.txt

# 6. 配置密钥
cp .env.example .env
# 编辑 .env 填 DEEPSEEK_API_KEY

# 7. 建表
.venv/bin/python -m app.db.init_schema

# 8. 起服务
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### .env 必填字段

```
DEEPSEEK_API_KEY=<你的 key>     # 必填，P2+ 需要
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_RAG_DAILY_LIMIT=200
DB_URL=postgresql+asyncpg://news:changeme@localhost:5432/news_aggregator
EMBEDDING_MODEL=BAAI/bge-small-zh
RERANKER_MODEL=BAAI/bge-reranker-base
DEDUP_SIM_THRESHOLD=0.90
```

### 一键全流程重跑

```bash
# Reset DB
./scripts/reset_db.sh

# P1 抓取
.venv/bin/python -c "import asyncio; from app.services.ingest import run_ingest; asyncio.run(run_ingest())"

# P2 摘要+向量化
.venv/bin/python -c "import asyncio; from app.services.p2 import run_p2; asyncio.run(run_p2())"

# P3 事件去重
.venv/bin/python -c "import asyncio; from app.services.dedup import run_p3; asyncio.run(run_p3())"

# P4 事实互证
.venv/bin/python -c "import asyncio; from app.services.p4 import run_p4; asyncio.run(run_p4())"

# P5 RAG 测试
.venv/bin/python -c "
import asyncio
async def main():
    from app.db.session import AsyncSessionLocal
    from app.services.rag.answer import rag_answer_stream
    async with AsyncSessionLocal() as session:
        async for evt in rag_answer_stream(session, '你的问题'):
            print(evt.rstrip())
asyncio.run(main())
"
```

---

## 九、答辩话术（4 句必背）

1. **被问"你这是设计还是研究？"**
   > "毕业设计，系统设计与实现是主交付物。两套标注集 + 消融对比表是第 5 章的设计验证手段，验证去重阈值和三段管道选型的合理性，不是研究贡献本身。"

2. **被问"创新点在哪？"**
   > "在设计层面——三层 1:N:6N 数据模型支持事件级聚合与事实互证、关键词闸门 + ANN 两级去重解决事件生命周期问题、异步重 embed 解决事件中心偏差、三段检索管道分级精筛。这些都是设计决策。"

3. **被问"和今日头条有何不同"**
   > "头条做 per-user 个性化分发深度 CTR 预估，我做事件级聚合 + 事实互证 + 可溯源问答，方向相反，刻意不在此轴竞争。"

4. **被问"洗稿风险"**
   > "摘要为大模型抽象式事实复述，经 N-gram 校验防复现原文片段。系统不公开部署仅作研究原型遵循合理使用。"

---

## 十、给老师的一段话（设计定位版）

> 老师您好，我做的是多源新闻事件的向量聚合与检索系统的设计与实现，属于毕业设计。
>
> 系统分四层：数据采集层抓取人民网/中国新闻网 RSS 入库；数据处理层用 DeepSeek 生成事实摘要并用本地 BGE 转向量，按关键词闸门 + 语义相似度判别同事件并聚合为事件单元；检索问答层用三段管道（时间预过滤 → 向量召回 Top-20 → cross-encoder 重排 Top-5）+ 受约束大模型生成带引用的流式答案；前端展示层用 Vue 3 实现事件流卡片与 RAG 对话框。
>
> 数据库设计为三层结构——报道表、事件表、事实槽位表，1 事件挂 N 报道、每报道 6 个 5W1H 槽位，事件级做事实互证与冲突分级（一致/互补/疑似/标红 4 档）。
>
> 为验证设计决策有效性，构造两套人工标注集：100 对近邻采样调去重阈值（选 precision ≥ 0.9），50 条问答评测集跑 set recall@5 和 faithfulness，横向消融三段管道证明每段都有贡献。
>
> 技术栈：Python+FastAPI、PostgreSQL+pgvector、BGE 与 reranker 本地 CPU 推理、DeepSeek、Vue 3。总工时约 207 小时。

---

## 十一、对 Kimi 2.7 的具体指令

### 第一步：修 P5 已知漏洞

按第六节漏洞 1-5 修 `app/services/rag/answer.py`：
1. 流式过程累积 `full_text`
2. 结尾真实判定 `信息不足` 二字 → `refusal` 真实
3. meta 事件用 `json.dumps`
4. 删除"开始生成..."预通知
5. 流末正则提取 `[事件#N]` 解析 citations + 校验是否在 Top-5
6. 异常分支标 `error: true`
7. dailylimit 加 `asyncio.Lock`

### 第二步：跑 P6 评测

1. 写 `scripts/build_rag_gold.py` 生成 50 条问题模板
2. 人工标 gold 事件集
3. 写 `scripts/run_rag_eval.py` 跑 recall@5 + faithfulness + 拒答率
4. 写 5 行消融 baseline 跑 5 次
5. 填消融对比表

### 第三步：P7 Vue 前端

1. `npm create vite@latest frontend -- --template vue`
2. 三个页面：onboarding / 事件流 / RAG 对话
3. SSE 用 EventSource
4. 后端加 CORS

### 不要做的事

- **不要推翻 grilling 决策**（除非你有非常充分的理由 + 在新 ADR 里记录）
- **不要用 AI 标 ground truth**（Q7 反循环论证红线）
- **不要加个性化推荐**（Q9 已钉死不做）
- **不要加立场对比**（Q4 已降级为事实互证）
- **不要改 DEDUP_THRESHOLD 0.90**（Q7 调参集已验证，100 对标注在 `data/dedup_gold.csv`）
- **不要删 `summary_source`/`summary_model`/`summary_tokens` 列**（Q22/Q25 行级记录是评测可复现护甲）
- **不要 commit `.env`**（含 API key，已 gitignored）

---

## 十二、git 历史（15 commits）

```
791ba82 P5: RAG three-stage retrieval + constrained generation + SSE streaming
c65c0cc docs: README + PITFALLS reflect P4 v2
d146ee0 P4 v2 grilling fixes: LLM category majority vote + conflict fact_strings
e89141f P4: 5W1H fact extraction + 4-grade merge + multi-source re-embed
92e1f2c docs: README + PITFALLS reflect P1/P2/P3 progress
4a31d26 P3 v2: tuned threshold 0.75 -> 0.90 (precision 0.134 -> 0.929)
8b978b4 P3 grilling fixes: attach keywords union + 100-pair dedup gold
4c4b03b P3: keyword-gate + ANN event dedup (130 reports -> 86 events)
e7d6d94 P2 v3: per-row model+token provenance, plagiarism 15->20, ultra-short skip
ac3df2a docs: PITFALLS add fallback pollution + null embedding + n-gram too strict
05ef37e P2 grilling fixes: summary_source provenance + embedding NULL + temp split
0a4cd43 P2: deepseek adaptive summary + BGE-small-zh local embedding + N-gram
5d49118 P1: real RSS ingest end-to-end (130 reports persisted)
b8dc97e docs: update README P0 status + PITFALLS reference
cc55aeb P0: scaffold + ORM v5 (7 tables, pgvector, ingest modules)
```

---

## 十三、关键工具脚本

| 脚本 | 用途 | 用法 |
| :--- | :--- | :--- |
| `scripts/reset_db.sh` | 一键 dev reset DB | `./scripts/reset_db.sh` |
| `scripts/build_dedup_gold.py` | 生成 100 对去重调参候选 | `.venv/bin/python -m scripts.build_dedup_gold` |
| `scripts/label_dedup_gold.py` | 交互式标注 100 对 | `.venv/bin/python -m scripts.label_dedup_gold` |
| `scripts/tune_dedup_threshold.py` | 扫阈值画 P/R/F1 表 | `.venv/bin/python -m scripts.tune_dedup_threshold` |

**需新建**：
- `scripts/build_rag_gold.py` — 生成 50 条 RAG 问答 gold 模板
- `scripts/label_rag_gold.py` — 交互式标注 gold 事件集
- `scripts/run_rag_eval.py` — 跑 recall@5 + faithfulness + 拒答率 + 消融

---

## 十四、API 端点清单

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| GET | `/api/health` | 健康检查 |
| GET | `/` | 根信息 |
| POST | `/api/ingest/run` | P1 抓取 RSS 入库 |
| POST | `/api/p2/run` | P2 摘要 + 向量化 |
| POST | `/api/p3/run` | P3 事件去重 |
| POST | `/api/p4/run` | P4 5W1H + 互证 + 重 embed |
| POST | `/api/rag/ask` | P5 RAG SSE 流式问答 |
| GET | `/api/reports` | 列出报道（支持 source/limit/offset） |
| GET | `/api/reports/{id}` | 单篇报道全文 |
| GET | `/api/reports/stats/summary` | 数据总览 |

Swagger UI: http://localhost:8000/docs

---

**交接完毕。Kimi 2.7 接手后第一步：修 P5 漏洞 1-5 → 跑 P6 评测 → P7 前端 → 答辩。**