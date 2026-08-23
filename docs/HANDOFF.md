# 交接文档：多源新闻事件的向量聚合与检索系统

> 供 Kimi 2.7 接手开发的完整交接文档。包含项目定位、21 轮 grilling 决策清单、文件结构、各阶段实现状态、已知漏洞及修复方案、剩余工作、答辩话术、环境配置。
>
> **阅读顺序**：先读"项目定位"理解做什么 → 读"grilling 决策清单"理解为什么这样设计 → 读"实现状态"理解已完成什么 → 读"剩余工作"理解接下来做什么 → 读"已知漏洞"理解哪些代码有问题需要修。

---

## 〇、2026-08 grilling v7 重大变更记录（本会话已执行）

> 上一轮交接（v6）后，用户经再次 grilling 钉死了 7 个新决策并已全部实现。**以下内容已过时**，接手时以本记录为准：

| 决策 | 内容 | 实现状态 |
| :--- | :--- | :--- |
| Q31 删 5W1H + 冲突分级 | 事实互证模块整体移除（fact_extract 瘦身为纯 category 抽取、p4 重写、fact_merge.py 删除、RAG context 去槽位、前端去冲突 UI、live 库清残留字段） | ✅ 已完成 |
| Q32 冻结评测库 | `news_aggregator_eval` = pg_dump 快照，评测脚本经 `DB_URL` env 覆盖指向它 | ✅ 已完成 |
| Q33 重跑评测 | 48 条重跑：recall@5 0.452（持平）/ refusal 0.958（持平）/ citation 1.0（持平）/ faithfulness **0.978→0.941**（删槽位后如实下降） | ✅ 已完成 |
| Q34 每日简报 | `GET /api/digest?date=&days=`，按多源度（COUNT(DISTINCT source_site)）排序 Top-10，复用 merged_summary 零新增 LLM | ✅ 已完成（前端 /digest 页） |
| Q35 RSS 2.0 事件流 | `GET /feed/events.rss?days=&category=`，一条 = 一个事件，link = 首篇原文 | ✅ 已完成（前端订阅按钮） |
| Q36 澎湃加源 | 自部署 RSSHub Docker :1200。`/thepaper/featured` ✅ / `/thepaper/channel/25950` ❌（RSSHub 上游 bug 503，改用 `/thepaper/sidebar/hotNews`）。ingest 后 +70 报道，多源事件 10→15，澎湃×中新网×热榜三方聚合已出现 | ✅ 已完成 |
| Q37 README 重定位 | README 改为答辩文档（删 2.3 事实互证/删 P0-P7 状态跟踪/加输出层/加澎湃/更新答辩口径与评测数字） | ✅ 已完成 |

**当前数据规模**：200 报道（人民网 100 / 中新网 60 / 澎湃 20 / 澎湃热榜 20），176 事件（15 多源）。
**当前服务**：后端 :8000（uvicorn，须 `env -u ALL_PROXY -u all_proxy` 启动）、前端 :5173（vite dev）、RSSHub :1200（docker，--restart=always）、PostgreSQL :5432。
**下一任务**：论文 4 图 + 答辩 PPT + 快照重打（答辩证库已冻结，live 库若继续 ingest 不影响评测）。

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

**决策**：T+0 纯热门（多源度=几家媒体报道）→ 行为渐进个性化。但推荐/推送已降级为投递细节不做行为画像。

**为什么**：推荐是今日头条主场，玩具级不可验证。改用显式兴趣标签过滤，规避个保法。

### Q4 立场对比 → ~~事实互证~~（Q31 已废弃）

**决策（v6）**：立场对比降级重命名为"多源事实互证"，做 5W1H 事实拼图矛盾标红。
**Q31 变更（v7）**：事实互证模块整体移除（答辩价值低、成本高）。系统回到"事件级聚合 + 摘要 + RAG + 输出层"。论文中此节改写为"事件级合并摘要"，不出现 5W1H 术语。

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

### Q12 扩源 → 澎湃经自部署 RSSHub ✅ 已落地

**决策（v6）**：(ii) 加澎湃经 RSSHub 撑覆盖。澎湃 RSSHub 公共实例超时，待自部署。

**Q36 落地（v7）**：Docker 自部署 RSSHub `:1200`（`docker run -d --name rsshub --restart=always -p 1200:1200 diygod/rsshub`）。
- `/thepaper/featured` ✅（20 条）+ `/thepaper/sidebar/hotNews` ✅（20 条）
- `/thepaper/channel/25950` ❌ RSSHub 上游 bug（cheerio.load expects a string），已在 feeds.yaml 注释说明
- 入库 +70 报道；澎湃 × 中新网 × 热榜三方聚合事件已产生（多源事件 10→15）

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

**决策**：LLM 按 8 类分类（政治/经济/文化/社会/科技/国际/体育/其他）。事件级 category 用成员报道 LLM category 的多数投票。**不用硬编码关键词 heuristic**。（5W1H 抽取部分随 Q31 移除，category 保留。）

### Q28 / Q29 事实槽位合并串与时间 window

**Q31 已废弃**：`fact_slots` 合并串、`when` 槽位 3 天 window 均随事实互证模块整体移除。RAG context 不再注入事实槽位块（faithfulness 0.978→0.941 的部分原因）。

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
    │   ├── p4.py                 # POST /api/p4/run（category 抽取+投票+多源重 embed）
    │   ├── rag.py                # POST /api/rag/ask（SSE 流式 RAG）
    │   ├── reports.py            # GET /api/reports（浏览报道/事件）
    │   ├── digest.py             # GET /api/digest（Q34 每日简报，多源优先 Top-10）
    │   └── feed.py               # GET /feed/events.rss（Q35 RSS 2.0 事件流）
    │
    └── services/                 # 业务逻辑层
        ├── embed.py              # BGE-small-zh 本地 embedding（singleton）
        ├── summarize.py          # DeepSeek 自适应摘要 + N-gram 校验 + rag_call
        ├── keywords.py           # LLM 抽 3 关键词
        ├── dedup.py              # P3 事件去重（关键词闸门 + ANN 0.90）
        ├── p2.py                 # P2 编排器（摘要+embedding 批处理）
        ├── p4.py                 # P4 编排器（category 抽取+投票+多源重 embed）
        ├── fact_extract.py       # LLM 抽 category（Q31 瘦身，无 5W1H）
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

### 6 张表（两层结构：1 事件 : N 报道）

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
| fact_slots | JSONB | **Q31 废弃**：保留列但恒为空（live 库已清），代码不再读写 |
| conflict_flags | JSONB GIN index | **Q31 废弃**：保留列但恒为空，代码不再读写 |
| source_count | Integer index | 报道篇数计数（dedup attach 时 +1）；对外展示/排序口径 = 多源度 COUNT(DISTINCT source_site)，API 字段 source_breadth |
| event_publish_time | DateTime index | Q1: MIN over reports.publish_time |
| category | String(32) index | Q27: 多数投票 |
| ts | DateTime server_default now() index | 入库时间 |

**索引**：HNSW on embedding（vector_cosine_ops, m=16, ef_construction=64）、GIN on keywords、btree on event_publish_time/source_count/category/ts。

> `news_report_fact` 表（事实槽位表）Q31 起不再使用，live 库已 TRUNCATE，仅评测快照库保留历史数据。

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

### P4 事件分类 + 重 embed ✓ 完成（Q31 瘦身）

- **Q31 变更**：5W1H 事实抽取 + 4 档冲突分级整体移除。P4 现做：category 抽取（LLM 8 类）+ 多数投票 + 多源事件合并摘要重 embed。
- category LLM 多数投票（政治 / 社会 / 文化 / 经济 / 体育 / 国际 / 其他 / 科技）
- 多源事件 LLM 合并摘要 + BGE 重 embed
- `news_report_fact` 表不再写入（live 库已 TRUNCATE）

### P5 RAG 检索问答 ✓ 完成（SSE 中文乱码已修）

- 三段管道：时间预过滤 → pgvector ANN Top-20（HNSW）→ bge-reranker Top-5
- 受约束生成：temp=0.1 + 强制引用 [事件#X] + 不足拒答
- SSE 流式 + 整句缓冲后渲染（Q21）
- 修复 `unicode_escape` 导致的乱码问题；SSE 现在按规范输出多行 `data:`
- 200/日上限文件计数器
- 端到端测试通过："最近有什么科技新闻" → 召回 #42 等事件 → 答案纯中文 + 含 `[事件#42]` 引用

### P6 评测 ✓ 完成（Q32/Q33 冻结库重跑）

- `scripts/build_rag_gold.py` 生成 50 条 RAG 问答 gold 模板
- `scripts/label_rag_gold.py` 交互式标注工具，已人工完成 48/50 条（2 条 skipped）
- **Q32**：评测库 `news_aggregator_eval`（pg_dump 快照冻结），评测脚本经 `DB_URL` env 覆盖指向
- **Q33**：Q31 改动后 48 条全量重跑：recall@5 **0.452** / refusal **0.958** / citation **1.0** / faithfulness **0.941**（v1 0.978，删槽位后如实下降）
- `scripts/run_rag_eval.py` 跑 recall@5 + faithfulness + 拒答率
- `scripts/run_ablation.py` 跑 5 行消融 baseline
- `data/rag_eval_summary.json` / `data/rag_ablation_results.json` 已产出（v1 备份：`data/rag_eval_*_v1_5w1h.json`）

### P7 Vue 前端 ✓ 完成

- `frontend/vue-app/` Vue 3 + Vite 脚手架已搭好
- 后端 CORS 已配置（允许 `http://localhost:5173`）
- 三个页面：Onboarding 兴趣标签 / EventStream 事件流 / RagChat RAG 问答
- EventCard 卡片展示：类别/时间/多源度、摘要、关键词、详情抽屉
- EventStream 支持搜索、多源度/时间排序、类别过滤
- RagChat 支持推荐问题、清空对话、SSE 流式渲染、引用 `[事件#N]` 按钮弹出事件详情弹窗
- `npm run build` 通过，dev server 可在 `http://localhost:5173` 访问

---

## 六、已修复漏洞与仍存局限

### 已修复（P5 后续已修）

| # | 问题 | 位置 | 修复状态 |
| :--- | :--- | :--- | :--- |
| 1 | 拒答判定写死 `refusal: false` | `app/services/rag/answer.py` | ✓ 已累积 `full_text`，按 `"信息不足" in full_text` 真实判定 |
| 2 | SSE meta 事件 JSON 不规范 | `app/services/rag/answer.py` | ✓ 已用 `json.dumps({'event_ids': event_ids})` |
| 3 | "开始生成..."预通知撒谎 | `app/services/rag/answer.py` | ✓ 已删除预通知，LLM 直接流式输出 |
| 4 | 异常分支 `done.refusal=false` 撒谎 | `app/services/rag/answer.py` | ✓ 异常分支返回 `refusal: true, error: true` |
| 5 | 引用不解析不校验 | `app/services/rag/answer.py` | ✓ 已用正则提取 `[事件#N]` 并校验是否在召回 Top-5 |
| 6 | dailylimit 文件计数器 race | `app/services/rag/dailylimit.py` | ✓ 已加 `asyncio.Lock`，调用方用 `increment_today_async` |
| 7 | context 格式不清晰 | `app/services/rag/answer.py` | ✓ `_build_context` 已统一缩进格式 |
| 9 | 多日期字符串 false-positive | `app/services/fact_merge.py` | ✓ `_try_parse_date` 已提取所有日期取最新值 |
| — | SSE 中文乱码 | `app/services/rag/answer.py` | ✓ 已移除 `encode('unicode_escape')`，新增 `_sse_token` 按 SSE 规范输出多行 `data:` |

### 仍存在的已知局限（写进论文 7.2 节）

1. **数据源覆盖有限**：新华网 403 仍不可用；澎湃经自部署 RSSHub 已接入（featured + 热榜），channel 路由为 RSSHub 上游 bug 暂不可用。
2. **`publish_time` 时区不统一**：feedparser 返回 UTC naive，人民网 RSS 给北京时间 naive，目前按天精度处理。
3. **N-gram 20 字阈值仍误判新闻引导词**：13/130 篇走 fallback。
4. **200/日上限仅适合演示**：毕设级，非生产并发设计。
5. **无用户认证**：`user_id` 由客户端生成 UUID，无服务端 auth。
6. **事件流页面后端缺少高级过滤**：目前只有类别过滤 + 搜索 + 排序，缺少时间范围、多源度区间等过滤。
7. **前端 UI 仍较朴素**：功能完整但视觉打磨不足（答辩截图够用，但不够精致）。

---

## 七、剩余工作（按优先级排，接手者重点）

### 优先级 1 / 论文图表与答辩 PPT（30-40h，当前在途）

> 代码已跑通，现在进入“把代码翻译成论文章节和 PPT”阶段。这是毕设能否过的关键。

#### 1.1 必须画的 4 张图（用于论文第 4 章 + PPT 首页）

| 图 | 工具建议 | 章节 | 状态 |
| :--- | :--- | :--- | :--- |
| 四层架构图（采集→处理→检索→前端） | draw.io / Mermaid | 4.1 | 待画 |
| E-R 图（事件/报道/槽位三层 1:N:6N） | draw.io / dbdiagram.io | 4.2 | 待画 |
| 数据流图（RSS → 报道 → 摘要/向量 → 事件 → RAG 答案） | Mermaid sequence | 4.1 | 待画 |
| 三段管道流程图（time→ANN→rerank→generate） | draw.io | 5.5 | 待画 |

#### 1.2 必须补的 3 张表（用于论文第 6 章）

| 表 | 数据位置 | 状态 |
| :--- | :--- | :--- |
| 去重阈值调参表 / P-R-F1 曲线 | `data/dedup_gold.csv` + `scripts/tune_dedup_threshold.py` | 待整理成论文表格 |
| RAG 消融实验 5 行表 | `data/rag_ablation_results.json` | 已产出，待格式化 |
| RAG 评测指标表（recall@5 / faithfulness / 拒答率） | `data/rag_eval_summary.json` | 已产出，待格式化 |

#### 1.3 答辩 PPT 准备

- 10-15 页：背景 → 架构 → 关键技术 → 评测 → 演示截图 → 总结
- 系统截图 5-8 张：Swagger `/docs`、事件流、事件详情抽屉、RAG 对话、数据库查询、消融表

### 优先级 2 / 测试覆盖率补齐（8-12h）

目前只有 5 个 smoke test。第 6.1 节需要更多单元/集成测试：

| 模块 | 需补测试 |
| :--- | :--- |
| P2 摘要 | N-gram 正常/异常分支、fallback 路径、自适应长短稿分支 |
| P3 去重 | 关键词闸门相交/不相交、ANN >0.90/<0.90、attach 扩集 keywords |
| P4 互证 | 4 档分级各一例、N/A skip、多日期解析 |
| P5 RAG | 时间窗解析、SSE 整句缓冲边界、拒答判定、引用校验 |
| 集成 | P1→P2→P3→P4→P5 端到端 happy path |

### 优先级 3 / 性能基准脚本（4-6h）

写/跑 `scripts/benchmark.py`，出一张性能表（第 6.4 节）：

| 指标 | 当前状态 |
| :--- | :--- |
| P1 抓取 130 篇耗时 | 待测 |
| P2 LLM 摘要 130 篇耗时 | 待测 |
| P2 BGE embedding CPU 耗时 | 待测 |
| P3 ANN 单次去重延迟 | 待测 |
| P5 RAG 端到端延迟（拆分 4 段） | 待测 |

### 优先级 4 / 论文正文写作（20-30h）

按 7.5.1 节骨架补完各章节。优先写：
1. 摘要 + Abstract
2. 第 4 章 系统总体设计（架构图 + E-R 图 + 数据流图）
3. 第 5 章 详细设计（去重、RAG 管道、前端）
4. 第 6 章 测试与验证（指标表 + 消融表 + 性能表）
5. 第 7 章 局限与未来工作

### 优先级 5 / 可选 polish（时间够再做）

- 前端标题从默认 `vue-app` 改成系统名
- 事件流加时间范围过滤
- 自部署 RSSHub 启用澎湃/新华网
- 时区统一加 `tzinfo`
- HNSW `ef_search` 调参
- 移动端小程序版本（如果导师要求演示）

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

- 数据源覆盖有限（新华网 403；澎湃已接 featured+热榜，channel 路由 RSSHub bug）
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
   > "在设计层面——两层 1:N 数据模型支持事件级聚合、关键词闸门 + ANN 两级去重解决事件生命周期问题、异步重 embed 解决事件中心偏差、三段检索管道分级精筛、事件级输出（每日简报 / RSS 事件流）复用聚合成果。这些都是设计决策。"

3. **被问"和今日头条有何不同"**
   > "头条做 per-user 个性化分发深度 CTR 预估，我做事件级聚合 + 可溯源问答 + 事件级输出，方向相反，刻意不在此轴竞争。"

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

## 十一、对下一位 AI 的具体指令

### 当前状态（接手时）

- P0-P4 已完成（数据抓取 130 篇 → 摘要/向量化 → 事件去重 114 个 → 5W1H 互证）。
- P5 RAG 已完成且主要漏洞已修（拒答判定、SSE 格式、引用校验、乱码）。
- P6 评测已完成：48/50 条 RAG gold 已标，`data/rag_eval_summary.json` 和 `data/rag_ablation_results.json` 已产出。
- P7 Vue 前端已完成：onboarding / 事件流 / RAG 对话三页可跑，`npm run build` 通过。
- **当前在途任务**：论文图表、答辩 PPT、测试覆盖率、性能基准。

### 第一步：确认环境还能跑通（0.5h）

```bash
cd /home/lxxx/bishe
.venv/bin/python -m pytest tests/ -q
curl -s http://localhost:8000/api/health
```

如果后端没起：
```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

前端 dev server（需要时再起）：
```bash
cd frontend/vue-app
npm run dev
```

### 第二步：论文图表与表格（优先做）

1. 生成/补全 4 张图（Mermaid 或 draw.io），放进 `docs/diagrams/`：
   - 四层架构图
   - E-R 图
   - 数据流图
   - 三段管道流程图
2. 整理 3 张表：
   - 去重阈值 P/R/F1 表（用 `scripts/tune_dedup_threshold.py` 出数据）
   - RAG 消融表（从 `data/rag_ablation_results.json`）
   - RAG 评测指标表（从 `data/rag_eval_summary.json`）

### 第三步：答辩 PPT

按“背景 → 架构 → 关键技术 → 评测 → 演示截图 → 总结”做 10-15 页。截图来源：
- Swagger UI `http://localhost:8000/docs`
- 前端事件流 `http://localhost:5173/events`
- 前端 RAG 对话 `http://localhost:5173/chat`

### 第四步：测试与性能基准（有时间再做）

- 补单元测试（见第七节优先级 2）。
- 写/跑 `scripts/benchmark.py` 出性能表。

### 不要做的事

- **不要推翻 grilling 决策**（除非你有非常充分的理由 + 在新 ADR 里记录）
- **不要用 AI 标 ground truth**（Q7 反循环论证红线；已标的 48/50 条是人类标的）
- **不要加个性化推荐**（Q9 已钉死不做）
- **不要加立场对比**（Q4 已降级，Q31 已废弃事实互证）
- **不要改 DEDUP_THRESHOLD 0.90**（Q7 调参集已验证）
- **不要删 `summary_source`/`summary_model`/`summary_tokens` 列**（Q22/Q25 行级记录是评测可复现护甲）
- **不要 commit `.env`**（含 API key，已 gitignored）
- **不要改 Vue 前端核心逻辑**（当前 SSE 解析和引用弹窗已正常工作）

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
| GET | `/api/events` | 事件流列表（支持 category/limit/offset/search/order/sort） |
| GET | `/api/events/{event_id}` | 单事件详情（含 reports） |
| GET | `/api/digest` | Q34 每日简报（date/days 参数，多源优先 Top-10） |
| GET | `/feed/events.rss` | Q35 RSS 2.0 事件流（days/category 参数） |
| POST | `/api/rag/ask` | P5 RAG SSE 流式问答 |
| GET | `/api/reports` | 列出报道（支持 source/limit/offset） |
| GET | `/api/reports/{id}` | 单篇报道全文 |
| GET | `/api/reports/stats/summary` | 数据总览 |
| POST | `/api/user/profile` | 保存/更新用户兴趣标签 |

Swagger UI: http://localhost:8000/docs

---

**交接完毕。Kimi 2.7 接手后第一步：修 P5 漏洞 1-5 → 跑 P6 评测 → P7 前端 → 答辩。**