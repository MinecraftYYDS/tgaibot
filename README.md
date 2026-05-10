# Telegram Topics AI Bot

基于 Telegram Forum/Topics 的多会话 AI 机器人。

默认文档语言为中文；英文版在文末。

## 中文文档

### 1. 项目目标

本项目实现一个运行在 Telegram 群组 Topics 模式下的 AI Bot：

- 每个 Topic 是独立会话
- 支持 Auto 路由与手动切换模型
- 支持自定义 base_url、自定义 model_name、多模型目录配置
- 支持流式回复和强制停止
- 支持会话状态持久化、模型切换审计、checkpoint
- 支持后台作业：自动总结、自动改名、删除同步补偿

### 2. 当前能力

- 运行时：aiogram + FastAPI 同进程
- 数据层：SQLite + WAL + 每 Topic 串行事务锁
- 核心命令：/start, /new, /stop, /models
- 模型系统：
	- Auto 模式
	- 动态模型按钮（来自模型目录）
	- 模型切换持久化到数据库
- 生成系统：
	- 流式编辑回复
	- Stop 按钮和 /stop 中断
	- 中断后 checkpoint 落库
- 作业系统：
	- summarize（总结）
	- rename_topic（改名）
	- delete_sync（删除同步）

### 3. 目录结构（核心）

```text
tgaibot/
	config/
		models.json
	src/
		bot/
		jobs/
		llm/
		persistence/
		routing/
		session/
		main.py
	.env.example
	requirements.txt
```

### 4. 快速开始（Windows PowerShell）

1. 创建并激活虚拟环境

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. 安装依赖

```powershell
pip install -r requirements.txt
```

3. 配置环境变量

```powershell
Copy-Item .env.example .env
```

至少需要配置：

- TELEGRAM_BOT_TOKEN
- TELEGRAM_ALLOWED_CHAT_IDS（可选，逗号分隔）
- TELEGRAM_ALLOWED_USER_IDS（可选，逗号分隔；这些用户总是允许使用）

建议配置（OpenAI 兼容）：

- OPENAI_API_KEY 或 OPENAI_API_KEYS（逗号分隔多 key）
- MODEL_CONFIG_PATH

4. 启动

```powershell
python -m src.main
```

### 5. 模型配置（多模型 / 自定义 base_url / 自定义模型）

默认模型目录：config/models.json

每个模型字段说明：

- id：模型唯一 ID（会在按钮和路由中使用）
- label：按钮展示名
- provider：当前支持 openai_compatible
- model_name：实际请求给服务商的模型名
- base_url：该模型独立 API 地址
- api_key_env：从哪个环境变量取 key
- tags：可选分类标签

示例：

```json
{
	"id": "my_proxy_fast",
	"label": "My Proxy Fast",
	"provider": "openai_compatible",
	"model_name": "qwen-plus",
	"base_url": "https://your-proxy.example.com/v1",
	"api_key_env": "OPENAI_API_KEY",
	"tags": ["simple"]
}
```

### 6. Auto 路由配置

可通过 .env 调整 Auto 目标模型：

- AUTO_SIMPLE_MODEL_ID
- AUTO_REASONING_MODEL_ID
- AUTO_LONG_MODEL_ID
- AUTO_TOOL_MODEL_ID

路由逻辑（当前版本）：

- 编程/推理关键词 -> reasoning 模型
- 长文本 -> long 模型
- 工具意图关键词 -> tool 模型
- 其余 -> simple 模型

### 7. 命令与按钮

命令：

- /start：启动提示
- /new：Topic 会话初始化并弹出模型按钮
- /stop：停止当前 Topic 生成
- /models：列出当前加载模型

按钮：

- Model 按钮：切换会话模型（持久化）
- Stop：停止生成
- Summarize：入队总结作业
- Clear：清空 Topic 上下文（软删除）

### 8. 数据一致性说明

- topic_id 由 chat_id + message_thread_id 表示
- 每 Topic 串行锁，避免并发写冲突
- 消息保存 telegram_message_id 与内部消息映射
- 删除消息时进行上下文删除同步，并可通过作业补偿

### 9. 作业系统

后台 worker 与 bot 同进程运行，周期拉取 pending 作业：

- summarize：基于上下文生成摘要并写入 topics.summary
- rename_topic：生成短标题并尝试调用 Telegram edit_forum_topic
- delete_sync：同步删除上下文消息

失败任务会重试，超过阈值标记 error。

### 10. Tool 调用（当前）

已提供轻量协议（用于验证工具环路）：

- [tool:echo] 任意文本
- [tool:time_now]
- [tool:web_search] 关键词
- [tool:search] 关键词 | 5

后续可升级为完整 function-calling schema/tool-loop。

说明：

- web_search/search 使用项目根目录下的本地 ddgs 包
- 结果会返回标题、链接和摘要
- max_results 范围为 1-10，默认 5

### 11. 常见问题

1) 没有流式输出

- 检查 OPENAI_API_KEY 是否配置
- 检查 base_url 是否兼容 /chat/completions stream 格式

2) 模型按钮为空

- 检查 MODEL_CONFIG_PATH 指向的 JSON 是否存在且格式正确

3) Topic 改名失败

- 机器人可能无编辑话题权限，数据库标题仍会更新

### 12. 开发建议

- 先用 /models 验证模型目录加载
- 先在单一 Topic 压测 stop 与流式，再扩展并发 Topic
- 生产建议迁移 PostgreSQL + Redis 锁

---

## English Documentation

### 1. Overview

Telegram Topics AI Bot with per-topic isolated sessions.

Implemented features:

- Multi-model catalog with per-model base_url and model_name
- Auto model routing + manual model switch
- Streaming response editing + stop control
- SQLite persistence with per-topic lock
- Background job worker for summarize/rename/delete-sync

### 2. Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m src.main
```

Required:

- TELEGRAM_BOT_TOKEN

Recommended:

- OPENAI_API_KEY (or OPENAI_API_KEYS)
- MODEL_CONFIG_PATH

### 3. Model Catalog

Default path: config/models.json

Each model defines:

- id
- label
- provider (currently openai_compatible)
- model_name (real provider model name)
- base_url (custom endpoint)
- api_key_env
- tags

### 4. Commands

- /start
- /new
- /stop
- /models

### 5. Job Worker

- summarize: generate topic summary
- rename_topic: generate and apply topic title
- delete_sync: synchronize context deletion

### 6. Notes

- Bot and API run in the same process
- Worker runs with bot polling
- Search tool is backed by local ddgs package in project root
- For production scale, consider PostgreSQL + Redis lock layer
