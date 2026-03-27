# Keys — API Key Manager

一个基于 FastAPI 的 API 密钥管理平台，支持从多个公开渠道自动采集、校验并统一管理 API Key（Google、OpenAI 等）。

## 功能

- **密钥管理**：统一存储、查看、删除各类 API Key
- **自动采集**：通过爬虫脚本从 HuggingFace、Kaggle、FOFA 等平台抓取泄露的 API Key
- **自动校验**：采集后自动调用对应 API 验证 Key 的有效性
- **登录保护**：基于 Session 的单用户认证，所有接口需登录后访问
- **Web 界面**：基于 Jinja2 模板的轻量前端

## 技术栈

- **后端**：FastAPI + SQLModel (SQLite)
- **包管理**：[uv](https://github.com/astral-sh/uv)
- **运行环境**：Python 3.13+

## 快速开始

**1. 安装依赖**

```bash
uv sync
```

**2. 配置环境变量**

复制示例文件并填写配置：

```bash
cp .env.example .env
```

`.env` 配置项说明：

| 变量 | 说明 | 示例 |
|------|------|------|
| `APP_NAME` | 应用名称 | `Key Manager` |
| `DEBUG` | 调试模式 | `false` |
| `DATABASE_URL` | 数据库路径 | `sqlite:///./keys.db` |
| `SECRET_KEY` | Session 加密密钥 | 随机长字符串 |
| `LOGIN_PASSWORD` | 登录密码 | 自定义 |
| `FOFA_API_KEYS` | FOFA API 密钥列表 | `["key1","key2"]` |

**3. 启动服务**

```bash
uv run python -m app.main
```

服务默认运行在 `http://127.0.0.1:8888`。

## 采集脚本

脚本位于 `scripts/` 目录，可独立运行，采集后自动校验并入库：

| 脚本 | 说明 |
|------|------|
| `hf_google_scraper.py` | 从 HuggingFace 全文搜索中提取 Google API Key |
| `hf_openai_scraper.py` | 从 HuggingFace 全文搜索中提取 OpenAI API Key |
| `kaggle_scraper.py` | 从 Kaggle 公开 Notebook 中提取 API Key |
| `fofa_scraper.py` | 通过 FOFA 搜索引擎采集 API Key |
| `verify_keys.py` | 对库中存量密钥进行批量重新校验 |

运行示例：

```bash
uv run python scripts/hf_google_scraper.py
```

## 项目结构

```
Keys/
├── app/
│   ├── main.py          # FastAPI 入口
│   ├── config.py        # 配置（pydantic-settings）
│   ├── db.py            # 数据库初始化
│   ├── http_client.py   # 共享 aiohttp Session
│   ├── models/          # SQLModel 数据模型
│   ├── routers/         # 路由（auth、key）
│   ├── services/        # 业务逻辑
│   └── utils/           # 工具函数
├── scripts/             # 采集 & 校验脚本
├── static/              # 前端静态资源
├── templates/           # Jinja2 HTML 模板
├── tests/               # 测试
├── pyproject.toml
└── uv.lock
```

## 开发

```bash
# 安装 pre-commit hooks
uv run pre-commit install

# 运行测试
uv run pytest
```
