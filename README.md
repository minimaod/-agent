# 电商问数 Agent

基于 LangGraph 的电商自然语言问数项目，通过字段、指标和字段取值检索，为模型提供上下文，完成 SQL 生成、校验、执行与结果展示。

本仓库基于开源项目扩展，个人改造重点是多轮问数与会话记忆：将“那华东呢？”这类省略式追问补成完整问题，再交给已有 NL2SQL 链路。

## 个人改造与当前进度

第一阶段已接入前后端工作流，用户反馈指定多轮联调场景通过。失败处理通过服务层模拟验证，尚未进行真实模型或数据库故障下的完整接口验证。

| 改造内容 | 实现方式 |
| --- | --- |
| 会话历史 | 按 session_id 保存成功查询的原话、完整问题和 SQL |
| 问题理解与改写 | 复用现有模型，通过提示词与 Pydantic 解析器返回结构化意图 |
| 意图路由 | 追问、新话题进入查询链路；澄清、超范围进入回复节点 |
| 前后端会话关联 | 服务端通过 SSE 返回编号，前端保存并随后续请求提交 |
| 成功记录保存 | 正常结束且最终状态具有 SQL 执行成功标记时追加历史 |
| 话题边界 | 收到新话题意图即移动历史起点；没有收到改写结果时保守隔开旧历史 |

示例：

| 本轮输入 | 预期理解 |
| --- | --- |
| 统计2025年华北地区的销售总额，按品牌分组 | 建立查询条件 |
| 那华东呢？ | 替换地区，保留年份、指标和分组 |
| 改成按月份分组 | 调整分组，保留2025年、华东和销售指标 |
| 只看第一季度 | 在当前条件上追加第一季度限制 |

以上是问数场景说明，不包含预设查询金额。

实现与验证详情见 [改造记录](docs/改造记录-多轮问数与会话记忆.md)。

## 查询流程

个人改造接在原有关键词抽取之前；后续检索和 SQL 主链路来自上游项目。

```text
本轮原话 + 当前会话可参考的成功历史
    ↓
问题理解与改写
    ├─ follow_up / new_topic → 完整问题
    │      ↓
    │   关键词抽取
    │      ↓
    │   字段语义召回 + 指标语义召回 + 字段取值全文召回
    │      ↓
    │   合并上下文、补齐依赖、过滤候选信息
    │      ↓
    │   SQL 生成 → 校验 → 必要时修正 → 执行
    │      ↓
    │   SSE 返回进度和结果；满足保存条件后追加成功历史
    │
    └─ clarify / out_of_scope → SSE 返回提示，结束本轮
```

## 验证情况

验证脚本由助手提供，用户在 VS Code 运行；页面联调也由用户执行。

| 验证类型 | 范围 | 模型与 SQL |
| --- | --- | --- |
| 基础组件 | 记忆追加、会话隔离、列表副本、历史格式化、输出解析 | 无真实模型调用，无 SQL 执行 |
| 图的局部验证 | 澄清路由、追问改写结果向后续节点传递 | 使用人工改写结果，不执行 SQL |
| 服务层模拟验证 | 会话编号生成与复用、成功保存、失败不追加、话题边界、回复转发 | 模拟图输出和异常，不执行模型或 SQL |
| 独立模型样例 | 地区追问改写、新话题识别 | 真实模型调用，人工历史中的 SQL 未执行 |
| 页面与接口联调 | 连续追问、条件替换和追加、分组调整、新话题、新会话澄清 | 用户反馈真实联调通过；查询轮次涉及模型和 SQL |

当前证据以用户反馈为主，具体 SQL、结果数字和完整响应未逐项归档。临时验证脚本未作为测试文件提交。

这些场景验证不代表整体准确率、性能评测、并发可靠性或所有异常路径均已通过。

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

本阶段未新增依赖，继续复用项目现有模型客户端。

## 本地运行

需要 Python 3.14+、uv、Docker Compose、Node.js 和 pnpm。以下以 PowerShell 为例。

### 1. 获取源码并安装依赖

```powershell
git clone --branch main https://github.com/minimaod/-agent.git ecommerce-query-agent
cd ecommerce-query-agent
uv sync
Copy-Item .env.example .env
```

在 `.env` 中填写 `LLM_API_KEY`，不要提交真实密钥。

现有模型配置位于 `conf/app_config.yaml`，使用 `deepseek-flash` 和 `https://api.deepseek.com`。

### 2. 准备模型并启动基础服务

首次运行需下载 Embedding 模型，模型权重不包含在仓库中。

```powershell
uv run hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5
docker compose -f docker/docker-compose.yaml up -d
```

等待 MySQL、Elasticsearch、Qdrant 和 Embedding 服务就绪。MySQL 初始化脚本仅在空数据目录首次启动时执行。

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

省略 `session_id` 时，服务端生成编号并通过 `session` SSE 事件返回；后续追问应携带相同编号。

### 4. 启动前端

在另一个终端进入项目的 frontend 目录：

```powershell
cd frontend
pnpm install
pnpm dev
```

前端默认通过 Vite 代理访问本地 8000 端口的后端。前端收到会话编号后在当前页面内复用；点击“新会话”或“清空”会清除前端编号，下一次请求创建新会话。

## 核心代码入口

| 路径 | 职责 |
| --- | --- |
| app/agent/graph.py | 节点注册与意图路由 |
| app/agent/state.py | 本轮执行状态 |
| app/agent/nodes/rewrite_query.py | 历史格式化、模型改写调用和图节点 |
| app/agent/nodes/respond_query.py | 澄清与超范围提示 |
| app/services/query_service.py | 会话编号、流式消费、成功保存与话题切换 |
| app/services/conversation_memory.py | 成功历史与当前话题起点 |
| app/entities/conversation.py | 成功查询记录 |
| app/entities/query_rewrite.py | 结构化意图与改写输出 |
| prompts/rewrite_query.prompt | 意图判断和改写规则 |
| frontend/src/App.tsx | 会话编号保存、消息展示和会话切换 |

## 当前边界

- 历史仅保存在单个进程内，重启后丢失；没有持久化、容量控制和多进程共享。
- 未实现同一会话并发请求的串行控制；前端单页限制不能代替后端并发治理。
- 前端编号没有跨刷新持久化；新会话不会删除后端旧记录。
- 会话编号用于关联历史，不承担身份认证或访问控制。
- SQL 执行成功不等于问题理解或查询语义正确。
- 改写失败后隔开旧历史是保守策略，可能需要用户重新补全条件。
- 尚未保存查询结果，不支持“刚才排名第一的商品”等历史结果引用。

第二阶段的历史结果引用暂不开展。

## 开源来源与许可

基础工程来自 [didilili/shopkeeper-agent](https://github.com/didilili/shopkeeper-agent)，原有检索、NL2SQL 主链路及前后端基础工程来自上游。

个人扩展聚焦会话记忆、问题改写、意图路由和话题边界。本阶段在助手讲解与代码协助下完成，运行验证由本人执行。

保留原项目 MIT 许可证，详见 [LICENSE](LICENSE)。
