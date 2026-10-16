# 电商问数 Agent

基于 LangGraph 的电商自然语言问数项目，通过字段、指标和字段取值检索，为模型提供上下文，完成 SQL 生成、校验、执行与结果展示。

本仓库基于开源项目扩展，当前重点改造方向是**多轮问数与会话记忆**：让系统理解省略式追问，并避免将旧话题条件带入新问题。

## 个人改造：多轮问数与会话记忆

当前完成记忆基础组件、服务层历史读写代码及独立的问题改写调用，正在推进完整工作流接入。

| 改造内容 | 实现方式 | 当前状态 |
| --- | --- | --- |
| 会话历史管理 | 按 session_id 保存多条成功查询记录 | 已实现内存存储 |
| 历史与本轮状态分离 | SuccessfulTurn 保存成功记录，State 承载本轮执行数据 | 已实现数据结构 |
| 成功后更新历史 | SQL 执行返回成功标记，服务层读取最终状态后追加记录 | 已接入代码，待完整接口验证 |
| 意图识别与问题改写 | 提示词、现有模型、Pydantic 输出解析器组成调用链 | 可独立调用，尚未接入问数图 |
| 新话题识别 | 判断当前问题是否应继承历史条件 | 已完成一条真实模型样例验证 |

改写目标示例：

- 历史问题：统计2025年华北地区的销售总额，按品牌分组。
- 本轮追问：那华东呢？
- 目标问题：统计2025年华东地区的销售总额，按品牌分组。

当前约定四类意图：追问、新话题、需要澄清、超出范围。
历史保存原话、完整问题和 SQL，暂不保存查询结果。

实现过程与验证详情见 [改造记录](docs/改造记录-多轮问数与会话记忆.md)。

## 基础问数链路

以下链路来自上游项目，本仓库在此基础上扩展会话能力。

```text
自然语言问题
    ↓
关键词抽取
    ↓
字段语义召回 + 指标语义召回 + 字段取值全文召回
    ↓
合并上下文、补齐依赖、过滤候选信息
    ↓
SQL 生成 → 校验 → 必要时修正 → 执行
    ↓
通过 SSE 返回进度与查询结果
```

计划将问题理解与改写接在关键词抽取之前。
当前图入口仍为关键词抽取，完整多轮流程尚未接通。

## 验证情况

已通过局部脚本验证：

- 记忆容器：多轮追加、不同会话编号分别保存、返回列表副本。
- 历史格式化：JSON 内容、空历史处理、排除历史 SQL。
- 输出解析：合法结构解析、非法意图及缺失意图拒绝。
- 真实模型调用：地区追问改写、新话题识别各一条样例。

模型验证使用人工准备的历史作为输入，输出来自真实模型调用。
这些验证未执行完整 API 多轮查询或测试 SQL，不代表整体准确率或性能评测。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 工作流与模型调用 | LangGraph、LangChain |
| 后端与流式接口 | FastAPI、SSE |
| 结构化数据访问 | MySQL、SQLAlchemy |
| 检索 | Qdrant、Elasticsearch |
| 向量模型服务 | TEI、bge-large-zh-v1.5 |
| 前端 | React、TypeScript、Vite、Tailwind CSS |
| 依赖管理 | uv、pnpm |

## 本地运行

需要 Python 3.14+、uv、Docker Compose、Node.js 和 pnpm。
以下命令以 PowerShell 为例。

### 1. 获取源码并安装依赖

```powershell
git clone --branch 17-api-integration-logging https://github.com/minimaod/-agent.git ecommerce-query-agent
cd ecommerce-query-agent
uv sync
Copy-Item .env.example .env
```

在 `.env` 中填写 `LLM_API_KEY`。
当前模型配置位于 `conf/app_config.yaml`，使用
`deepseek-flash` 和 `https://api.deepseek.com`。

### 2. 准备模型并启动基础服务

首次运行需下载 Embedding 模型，模型权重不包含在仓库中。

```powershell
uv run hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5
docker compose -f docker/docker-compose.yaml up -d
```

等待 MySQL、Elasticsearch、Qdrant 和 Embedding 服务就绪。
MySQL 初始化脚本仅在空数据目录首次启动时执行。

首次使用时构建元数据知识库：

```powershell
uv run python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

### 3. 启动后端

```powershell
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

接口文档：`http://127.0.0.1:8000/docs`。

查询接口为 `POST /api/query`，请求示例：

```json
{
  "query": "统计华北地区的销售总额",
  "session_id": "demo-session-001"
}
```

接口已接收会话编号，但追问改写尚未接入该接口。
服务端生成编号的回传和前端编号复用也尚未完成。

### 4. 启动前端

在另一个终端进入项目的 frontend 目录：

```powershell
cd frontend
pnpm install
pnpm dev
```

前端默认通过 Vite 代理访问本地 8000 端口的后端。

## 核心代码入口

| 路径 | 职责 |
| --- | --- |
| app/agent/graph.py | 问数图编排 |
| app/agent/state.py | 本轮执行状态 |
| app/services/query_service.py | 问数服务与历史读写衔接 |
| app/services/conversation_memory.py | 会话历史存储 |
| app/entities/conversation.py | 成功查询记录 |
| app/agent/nodes/rewrite_query.py | 历史格式化与改写调用 |
| prompts/rewrite_query.prompt | 意图判断与改写规则 |
| app/api/routers/query_router.py | 查询接口 |
| frontend/ | 问数交互界面 |

## 后续计划与边界

第一阶段继续完成：

- 改写节点接入与意图分支处理。
- 会话编号回传、前端复用及完整多轮验证。
- 条件继承、替换、追加和分组调整的场景验证。
- 查询失败与话题切换情况下的历史处理。

第二阶段支持历史结果引用，例如“刚才排名第一的商品”。

当前历史仅保存在单个进程内，重启后丢失；尚未实现持久化、
容量控制和多进程共享。会话编号不承担身份认证或访问控制职责。

## 开源来源与许可

基础工程来自 [didilili/shopkeeper-agent](https://github.com/didilili/shopkeeper-agent)。
本仓库在其问数链路上扩展会话记忆与问题改写。

保留原项目 MIT 许可证，详见 [LICENSE](LICENSE)。
