# P0 踩坑记录

> 从 0 到 7 张表建好 + FastAPI 起服务全过程中遇到的坑及解法。
> 写下来给答辩"工程难点"那节 + 复盘参考。

## 坑 1 / pgvector 不在 Ubuntu jammy apt 源

**现象**：`sudo apt-get install pgvector` 报 `E: Unable to locate package pgvector`。
**原因**：pgvector 在 Ubuntu 22.04 jammy 的官方源里**没有独立 deb 包**，必须源码编译。
**解法**：
```bash
cd /tmp && git clone --branch v0.7.4 https://github.com/pgvector/pgvector.git
cd pgvector && make && sudo make install
sudo -u postgres psql -d news_aggregator -c "CREATE EXTENSION IF NOT EXISTS vector;"
```
**连带坑**：第一次 `git clone` 因 WSL CA 证书问题报 `server certificate verification failed`。
**解决**：先 `sudo apt-get install -y ca-certificates`，再 `GIT_SSL_NO_VERIFY=true git clone ...` 绕过。

---

## 坑 2 / Python venv 缺 ensurepip

**现象**：`python3 -m venv .venv` 创建了目录但 `.venv/bin/python -m ensurepip` 报 `No module named ensurepip`。
**原因**：Debian/Ubuntu 把 ensurepip 单独打包成 `python3.10-venv`，不随 python3 主包装。
**解法**：`sudo apt-get install -y python3.10-venv`，然后重建 venv。

---

## 坑 3 / pip 缺 + setuptools 太老

**现象 1**：`python3 -m pip` 报 `No module named pip`。
**原因**：`python3-pip` 是独立 apt 包，不随 python3 装。
**解法**：`sudo apt-get install -y python3-pip`。

**现象 2**：装 feedparser 时，其依赖 `sgmllib3k` 需要源码构建，但 PEP 517 隔离构建环境里找不到 setuptools（venv 里 setuptools 还是 59.6.0 太老）。
**解法**：`.venv/bin/pip install --upgrade setuptools wheel` 升到 83+，再装依赖。

---

## 坑 4 / torch 默认 2GB CUDA wheel

**现象**：`.venv/bin/pip install -r requirements.txt` 时 torch 默认拉 CUDA bundle 2GB+，国内网络必断。
**原因**：`torch==2.5.1` 在 PyPI 主源默认指 CUDA 版本。
**解法**：改用 CPU-only wheel（约 200MB），从 PyTorch 官方 CPU 索引单独装：
```bash
.venv/bin/pip install torch==2.5.1+cpu --index-url https://download.pytorch.org/whl/cpu
```
然后在 `requirements.txt` 里**不**写 pinned torch 行（否则 fresh clone 解析失败），只留注释说明单独装。

---

## 坑 5 / 网络中断 pip 不续传

**现象**：pip 下载大包中途断网报 `ProtocolError: IncompleteRead`，重跑又从头下。
**原因**：pip 默认不支持断点续传。
**解法**：
- 加 `--retries 20 --timeout 300` 提高容错
- 换清华镜像 `--index-url https://pypi.tuna.tsinghua.edu.cn/simple`
- 大包（torch）单独装，不混进 requirements 一次性下

---

## 坑 6 / SQLAlchemy dialect 前缀 psycopg3 不认

**现象**：`psycopg.connect("postgresql+psycopg://news:changeme@...")` 报 `missing "=" after "postgresql+psycopg://..."`。
**原因**：`+psycopg` / `+asyncpg` 是 **SQLAlchemy dialect 前缀**，psycopg3 原生不认，要纯 `postgresql://`。
**解法**：在 `init_schema.py` 里传给 psycopg3 前剥掉前缀：
```python
plain = dsn.replace("+psycopg","").replace("+asyncpg","")
conn = psycopg.connect(plain, autocommit=True)
```
而 `create_engine` 那侧反而**要**带 `+psycopg` 让 SQLAlchemy 知道用 psycopg3 而非 psycopg2 dialect。

---

## 坑 7 / news 用户没权限 CREATE EXTENSION

**现象**：app 用户跑 `CREATE EXTENSION IF NOT EXISTS vector` 报权限不足。
**原因**：CREATE EXTENSION 需要 SUPERUSER 或 CREATEDB 权限，app 业务用户不应有。
**解法**：扩展由 postgres 超管一次装好（`sudo -u postgres psql -d news_aggregator -c "CREATE EXTENSION vector"`），app 用户只使用不创建。`init_schema.py` 里加容错——CREATE EXTENSION 失败时 fallback 查 `pg_extension` 验证 vector 已存在则继续，不存在才中止。

---

## 坑 8 / 表设计反查发现两大漏洞

**现象**：初版 ORM 跑通后反查发现：
1. `news_event` 没有 `publish_time`——P5 RAG 时间预过滤无列可用
2. `hotness` Float 死列——设计说 source_count 就是热度，又另建 hotness 是矛盾设计

**解法**：
- 补 `news_event.event_publish_time` 列（写入时算 MIN over reports.publish_time），加索引
- 删 `hotness` 列，source_count 既作计数又作热度排序键

---

## 坑 9 / raw_text NOT NULL 会崩 P1

**现象**：ORM 初版 `raw_text: Mapped[str]` 是 NOT NULL，但 RSS 实际很多项 `content:encoded` 为空、description 也可能空，P1 一抓 INSERT 失败整条 pipeline 崩。
**解法**：改为 `raw_text: Mapped[str | None]` 允许 NULL，P4 抽槽位时 skip 无原文的报道（仍保留 title + link 用于事件聚合）。

---

## 坑 10 / 把"8 张表"写进 README 自我放大

**现象**：从开题话术里顺口说"8 张表"，一路抄到 v6 README，实际 ORM 设计只有 7 张。
**解法**：反查 `pg_tables` 实测 7 张 → 改 README 为"7 张表"，附实测列表。
**教训**：开题话术数字别直接搬到文档，要反查源码。

---

## 坑 11 / 没初始化 git + 空 wheels 文件混进暂存

**现象**：git init 后 `git add -A` 把 `wheels/torch-*.whl`（一次 404 wget 留下的 0 字节文件）和 `.venv/` 也加进暂存。
**解法**：
- `.gitignore` 加 `wheels/`、`.venv/`、`__pycache__/`、`.env`
- 删空 wheels 目录
- `git rm --cached -r wheels`

---

## 坑 12 / 0 行测试

**现象**：P0 一开始没有任何测试，第一次跑 init_schema 改 ORM 后没法快速验证回归。
**解法**：写 `tests/test_health.py` 2 个 smoke test（health + root endpoint），pytest 跑 0.5 秒，每次 reset_db 脚本最后自动跑确认。

---

## 坑 13 / transformers 4.57 要求 torch ≥ 2.6（CVE-2025-32434）

**现象**：装 sentence-transformers 后跑 BGE 加载报 `Due to a serious vulnerability issue in torch.load, even with weights_only=True, we now require users to upgrade torch to at least v2.6`。
**原因**：transformers 4.57 强制要求 torch >= 2.6 修 CVE，2.5.1+cpu 不被接受。
**解法**：
```bash
# wget 断点续传大 wheel（pip 不支持续传，wget -c 能）
wget -c --timeout=120 --tries=20 "https://download.pytorch.org/whl/cpu/torch-2.6.0%2Bcpu-cp310-cp310-linux_x86_64.whl"
.venv/bin/pip install torch-2.6.0+cpu-cp310-cp310-linux_x86_64.whl --no-deps
```
**注意**：wheel 文件名必须按规范 `包名-版本-标签.whl`，瞎命名会让 pip 拒识别。

---

## 坑 14 / HuggingFace 直连 SSL 证书失败

**现象**：sentence-transformers 加载 BAAI/bge-small-zh 时报 `SSL: CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`。
**原因**：huggingface.co 在国内 SSL 证书验证常失败（vpn 不通到 HF / 自签证书）。
**解法**：用 hf-mirror.com 镜像，在代码里设环境变量：
```python
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
```
镜像站权重正常可达，下载速度 2-3 MB/s，~30 秒下完 100MB 模型。

---

## 坑 15 / 旧 ORM 死锁 summary：embed 失败时 summary 也丢

**现象**：第一版 P2 在 try/except 里 `continue`，导致 LLM 摘要生成后 embedding 失败时，summary 写库也跳过——下次重跑要重新花钱调 LLM。
**解法**：拆两阶段事务——写 summary 先 commit（保住已花 token 的 LLM 调用），再 try embedding。embed 失败时 summary 不回退，下次只需补 embedding。

---

## 坑 16 / fallback 摘要污染 P2 重跑机制

**现象**：无 DEEPSEEK_API_KEY 时跑 P2，会走 fallback 把 summary 填成"原文截 150 字"。填 key 后想跑真 LLM 摘要，但 P2 的 WHERE 是 `summary IS NULL`，已 fallback 的永远不会被 LLM 重生成。
**原因**：fallback 摘要和 LLM 真摘要用同一列、同一标记区分，无法识别"已处理但是占位"。
**解法**：加 `summary_source` 枚举列（`llm`/`fallback`），P2 WHERE 改成 `summary_source != 'llm'`，fallback 自动重跑。
**教训**：fallback 和真值混在一列是数据治理的隐患。Grilling 临时改的占位机制被代码写进默认流程，会埋下污染。

---

## 坑 17 / 空 raw_text 的占位向量污染 ANN

**现象**：第一版 P2 给 raw_text NULL 的报道用占位文字"（空内容）"编码 bge 向量。所有空文报道共享同一向量，P3 ANN dedup 时互相 cosine=1.0 → 误判同事件 → 把多篇空文报道强行并进一个伪事件。
**原因**：占位向量不该参与 ANN 相似度计算。
**解法**：空 raw_text 的 embedding 设 NULL（不占位），P3 ANN 查询 WHERE `embedding IS NOT NULL`。空文报道仍参与"多源列表"展示，不参与指纹和向量计算。

---

## 坑 18 / 15-gram 抄袭阈值过严导致 fallback 比例过高

**现象**：130 条真跑数据里 24 条（18%）被 N-gram 校验判定抄袭走 fallback。样本检查发现：DeepSeek 写短稿摘要时会复用"新华社北京6月5日电""国务院日前印发"这类**新闻常用引导短句**——15 字虽然够长但被这些套话触发。
**根因**：固定字符 window 比对 vs 语义相似度差异，原新闻短语>"新华社北京6月5日电 国务院"刚好 15 字压线触发。
**待解**（虽代码可跑）：
- (a) 把 N-gram 阈值放到 20 字（增加宽容度，减少套话误判）
- (b) 先 strip 引导词再算 N-gram（_strip_boilerplate 已有但没用在 plagiarism 检查前）
- (c) 用 token-level 比较代替 char-level（避开空格带来的人类友好但语义无关的边界问题）
- 当前选择：保留 15 字 + retry，larger 数据评测时再调

---

## 坑 19 / 关键词列用 JSONB 不支持 && 数组重叠操作

**现象**：P3 关键词闸门的 SQL `WHERE keywords && new_keywords` 报 `operator does not exist: jsonb && unknown`。
**原因**：`&&` 在 PostgreSQL 里是**数组重叠操作符**，对 jsonb 类型 `&&` 是另一个语义（jsonb 内容重叠），不是数组元素的 any-match。
**解法**：把 `news_event.keywords` 列从 `JSONB` 改成 `ARRAY(Text)`，加 GIN 索引。这样 `&&` 直接走数组重叠语义，GIN 索引支持高效查询。

---

## 坑 20 / psycopg3 返回 pgvector 是字符串不是 list

**现象**：`cur.execute("SELECT id, embedding FROM news_report...")` 拿到的 `embedding` 字段是 `'[0.1, 0.2, ...]'` 字符串，直接 `list(emb)` 拆成单个字符串字符——`sum(x*y)` 算 cosine 报 TypeError。
**原因**：psycopg3 默认对 pgvector 类型不解析，返回原始字符串。
**解法**：写 `_parse_vec` 处理两种形态：
```python
def _parse_vec(emb):
    if isinstance(emb, (list, tuple)): return [float(x) for x in emb]
    s = str(emb).strip().lstrip('[').rstrip(']')
    return [float(p) for p in s.split(',') if p.strip()]
```

---

## 坑 21 / pgvector 列返回 numpy array，`not a` 触发 ambiguous ValueError

**现象**：`if not a or not b or len(a) != len(b)` 在 `_cosine` 里报 `truth value of array with more than one element is ambiguous`。
**原因**：pgvector 通过 asyncpg 路径返回 numpy array，`bool(numpy_array)` 触发 `any/all` 要求。
**解法**：先用 `if a is None or b is None` 显式判 None，再用 `try: a = list(a)` 强转回 list 再走纯 Python 比较。

---

## 坑 22 / 魔数阈值 0.75 灾难（precision 0.134）— 这是 Q1 grilling 救命的一条

**现象**：未经调参直接设 DEDUP_THRESHOLD=0.75 跑 P3，得到 86 events / 46 attach 表面上"看起来合理"。
**反查真相**：造 100 对人工盲标集扫阈值 0.50-0.95，发现 0.75 阈值下 precision 仅 0.134——**86% 判定为"同事件"的对其实是不同事件**。整批"46 attach"里只有约 6 个真同事件，其余 40 个是 false-merge。
**根因**：bge-small-zh 在中文新闻摘要语义空间里，"相同主题不同事件"的摘要向量也可能 cosine 0.75–0.85（如"两会"和"习近平颁条例"向量相似因为都涉及国家机关用语），0.75 阈值在这种"同领域不同事件"上失效。
**解法**：100 对人工盲标 + 扫阈值画 P/R/F1，按 Q7 非对称成本（false-merge 灾难性）选 precision ≥ 0.9 的最高 recall 点 → 阈值 = **0.90**（precision 0.929 / recall 1.000 / F1 0.963）。
**重跑结果**：86 events → 114 events（少合并但每个合并都是真的）；最大聚合 16 源 → 4 源（不再把 16 篇同主题不同事件绑一起）。
**教训**：阈值不能是拍脑袋"测一下感觉合适"。任何 cutoff 都必须有 ground truth 调参集支撑。grilling 在 Q7 钉死这条原则、Q18 钉死近邻采样、这里终于坐实——光靠"觉得合理"会埋 false-merge 雷到 P4 互证才爆。

---

## 坑 23 / attach 后不扩集 event.keywords — SQL 闸门形同虚设

**现象**：第一版 `_attach_to_event` 只 bump source_count 不并 keywords。事件首篇 spawn keywords=[A,B,C]，第 2 篇 attach 进来 keywords=[B,D,E] 但 event.keywords 仍 [A,B,C]。第 3 篇 keywords=[D,F,G] 查闸门 `event.keywords && [D,F,G]` 不重叠 → spawn 新事件 → 漏 attach。
**根因**：domain 规则上"事件关键词集"应随挂载报道**累积扩集**而非固定首篇，但 CONTEXT.md 没明示这点（遗漏）。
**解法**：`_attach_to_event` 改成并集：
```python
event.keywords = list(set((event.keywords or []) + new_keywords))[:6]
```
cap 在 6 防止 GIN 索引失效。重跑后 `#74`事件 keywords 从首篇 3 扩到 4 篇 attach 后 6 个，正确接纳不同角度报道的入闸。
**教训**：domain glossary 必须显式描述聚合实体的"演化语义"——事件不只是"首篇+附属"，它的 keywords/embedding 都会随 attach 进化。Q2 第一次反查就被戳穿。

---

## 坑 24 / `_attach_to_event` 调用方没传 keywords 参数 — 修一半

**现象**：第一次修 Q2 改了 `_attach_to_event` 签名加 `new_keywords: list[str]` 参数，但调用方仍传旧的两个参数 → TypeError。
**解法**：调用点改成 `await _attach_to_event(session, report, best_event, keywords)`。
**教训**：改函数签名要同步改调用点；type hint 在 Python 不强制，是软约束，靠 grep。

---

## 坑 25 / Q2 决策被代码违反 — category LLM 抽了被硬编码 heuristic 覆盖

**现象**：grilling 钉死 Q2(a) "category 从 event.keywords 派生 + 在抽 5W1H 那次 LLM 调用顺手归类"。实测 `fact_extract.py` 花了 token 调出 category，但 `p4.py` 的 `_merge_and_persist_per_event` 直接扔掉 LLM 结果，改用硬编码 `if any(k in kw for k in ("习近平","国务院")): cat_guess = "政治"` 这种脆弱 heuristic。
**根因**：开发顺序是 fact_extract 先写完含 category，写到 p4 时忘记把 LLM 结果通道打通，临时手写个 heuristic 兜底。
**后果双重浪费**：① 花 token 让 LLM 算了不用；② 用更差的 heuristic 覆盖。答辩被问"category 怎么分类"会答"关键词匹配"——和 Q2(a) 钉死的"LLM 抽 5W1H 时顺手归类"对不上。
**解法**：`news_report` 加 `category` 列；P4 在持久化 fact rows 时顺手 `UPDATE news_report SET category=...`；事件级用 `Counter(cats_seen).most_common()` 多数投票。
**教训**：grilling 拍板的决策对应到代码后必须 grep 验证"决策名-代码-答辩口径"三点对齐。开发顺序里的临时兜底兜成了默认流程，是典型的决策漂移。

---

## 坑 26 / fact_slots 在 conflict 时只存首值 — P5 RAG 看不到冲突

**现象**：`fact_merge.py` 把 `fact_slots[slot]` 在 conflict 时填成 `out_vals[0]`，`conflict_flags[slot].values` 才存所有候选。但 P5 RAG 主要读 `event.fact_slots` 而非 `conflict_flags`——RAG 答案看到冲突的第一条值，违背 Q12 "系统不判谁对，保留全部让用户判"。
**根因**：fact_slots 写法保留了"single 选择值"语义，但 P5 RAG 不读 conflict_flags 这个附属字段，是设计接口与下游未对齐。
**解法**：fact_slots[slot] 在 conflict/uncertain 时改存 `"v1 / v2 / v3"` 合并串，让 facts_string 一行传给 RAG 就含全部候选。`#51.howmany = "8章44条 / 第809号令 / 8章44条"` ✓
**教训**：domain 字段写入时必须显式画像"下游读什么"。grilling 钉死的"不判谁对且保全部候选"必须落到负责回传给 RAG 的那一列，不能挂在附属 dict 里。

---

## 经验提炼（论文"工程难点"一节素材）

1. **首次部署 pgvector 步骤被低估**——apt 不可直装、SSL 证书坑、编译需 postgresql-server-dev-all 头文件、CREATE EXTENSION 权限隔离，完整跑通 4 个独立子坑。
2. **Python venv 在 Debian/Ubuntu 的"模块化打包"陷阱**——venv、pip、ensurepip 是独立 apt 包，不全装齐就互相卡。
3. **Python LLM 栈的 torch + sentence-transformers 体积**——CUDA bundle 2GB+ 不适合 CPU 推理 demo，CPU-only wheel 需特殊 index 装，requirements.txt pin 写法要小心。
4. **SQLAlchemy dialect 前缀与 psycopg3 原生 dsn 不兼容**——同一个连接串在 dialect 层和 driver 层格式要求不同，开发期容易踩。
5. **ORM 设计与下游模块对齐的反查**——表建好后必须倒推 P5 检索会用到什么列、P4 抽取需要什么字段约束，否则 schema 漏洞导致下游崩。dev 期 reset 策略优于 alembic 迁移（schema 变动 <10 次时）。
6. **CVE 驱动的版本强约束**——transformers 4.57 因 CVE-2025-32434 强迫 torch 升 2.6+，时间差依赖升级需要 wget 续传大 wheel 走断点下载。
7. **HuggingFace 国内可达性**——直连常 SSL 失败，hf-mirror.com 是 sentence-transformers/transformers 生态最可靠的镜像端点，代码层 setdefault 不依赖 .env 配置。
8. **P2 双阶段事务防 token 损失**——LLM 调用花钱但 embed 失败时 return-summary-as-null 等于双损，事务拆分让 LLM 已花的钱不白丢。
9. **fallback 与真值混列的污染**——临时占位机制不能和真值共用同一列，否则无法区分"已处理但占位"和"已处理且真"两种状态，重跑决策错乱。Q1 加 summary_source 源列修复。
10. **字符级 N-gram 抄袭判定与新闻体常用短语冲突**——15字阈值被新闻引导词压线触发，导致正常 LLM 摘要被误判 fallback。后续应 strip 引导词或采用 token-level/20字阈值。