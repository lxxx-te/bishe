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

## 经验提炼（论文"工程难点"一节素材）

1. **首次部署 pgvector 步骤被低估**——apt 不可直装、SSL 证书坑、编译需 postgresql-server-dev-all 头文件、CREATE EXTENSION 权限隔离，完整跑通 4 个独立子坑。
2. **Python venv 在 Debian/Ubuntu 的"模块化打包"陷阱**——venv、pip、ensurepip 是独立 apt 包，不全装齐就互相卡。
3. **Python LLM 栈的 torch + sentence-transformers 体积**——CUDA bundle 2GB+ 不适合 CPU 推理 demo，CPU-only wheel 需特殊 index 装，requirements.txt pin 写法要小心。
4. **SQLAlchemy dialect 前缀与 psycopg3 原生 dsn 不兼容**——同一个连接串在 dialect 层和 driver 层格式要求不同，开发期容易踩。
5. **ORM 设计与下游模块对齐的反查**——表建好后必须倒推 P5 检索会用到什么列、P4 抽取需要什么字段约束，否则 schema 漏洞导致下游崩。dev 期 reset 策略优于 alembic 迁移（schema 变动 <10 次时）。
6. **CVE 驱动的版本强约束**——transformers 4.57 因 CVE-2025-32434 强迫 torch 升 2.6+，时间差依赖升级需要 wget 续传大 wheel 走断点下载。
7. **HuggingFace 国内可达性**——直连常 SSL 失败，hf-mirror.com 是 sentence-transformers/transformers 生态最可靠的镜像端点，代码层 setdefault 不依赖 .env 配置。
8. **P2 双阶段事务防 token 损失**——LLM 调用花钱但 embed 失败时 return-summary-as-null 等于双损，事务拆分让 LLM 已花的钱不白丢。