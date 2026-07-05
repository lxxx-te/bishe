# 环境准备 (P0)

> 一次性系统准备。完成后 `uvicorn app.main:app --reload` 即可起服务。

## 1. 安装系统依赖 (sudo)

注意 pgvector 不在 Ubuntu jammy 默认源，**不要 apt 装 pgvector**，第 3 步源码编译。

```bash
sudo apt-get update && sudo apt-get install -y \
  python3-pip python3-venv python3.10-venv python3-dev build-essential \
  postgresql postgresql-contrib postgresql-server-dev-all
```

## 2. 启动 PostgreSQL + 建库建用户

```bash
sudo service postgresql start
sudo -u postgres psql <<'SQL'
CREATE USER news WITH PASSWORD 'changeme';
CREATE DATABASE news_aggregator OWNER news;
SQL
```

## 3. 源码编译 + 安装 pgvector

```bash
cd /tmp && rm -rf pgvector
git clone --branch v0.7.4 https://github.com/pgvector/pgvector.git
cd pgvector && make && sudo make install
sudo -u postgres psql -d news_aggregator -c "CREATE EXTENSION IF NOT EXISTS vector; GRANT ALL ON SCHEMA public TO news;"
```

## 4. 建 venv + 装依赖（torch CPU-only 单独装）

torch CPU-only wheel 不在 PyPI 主源，必须先从 PyTorch 官方 CPU 索引单独装：

```bash
cd /home/lxxx/bishe
rm -rf .venv
python3 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/pip install --upgrade pip setuptools wheel

# torch CPU-only（约 200MB，不是 2GB CUDA bundle）
.venv/bin/pip install torch==2.5.1+cpu --index-url https://download.pytorch.org/whl/cpu

# 其余依赖（PyPI 主源，国内可加清华镜像）
.venv/bin/pip install -r requirements.txt
```

国内网络不稳可加镜像 + 重试：
```bash
.venv/bin/pip install -r requirements.txt \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --trusted-host pypi.tuna.tsinghua.edu.cn \
  --retries 20 --timeout 300
```

## 5. 配置密钥

```bash
cp .env.example .env
# 编辑 .env: 填入 DEEPSEEK_API_KEY（必填，P2+ 需要）和 NEWSAPI_API_KEY（可选）
```

## 6. 建表

```bash
.venv/bin/python -m app.db.init_schema
```

## 7. 起服务

```bash
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000/api/health 应返回 `{"status":"ok", ...}`。
访问 http://localhost:8000/docs 看 OpenAPI 文档。

## dev 期 schema 修改：reset 策略（不引 alembic）

毕设开发期 schema 变动次数少（<10 次），用 reset 策略而非 alembic 迁移——丢库重建 5 秒，论文写"开发期采用 reset，生产部署引 alembic"。

```bash
./scripts/reset_db.sh
```

脚本会：DROP DB → CREATE DB → 装 pgvector extension → init_schema → 跑 smoke tests。