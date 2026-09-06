# Text2SQL MCP Server 使用说明

> 把现有 Text-to-SQL 引擎封装为 **MCP Server**（stdio），供 **Claude Desktop / Claude Code**
> 直接调用数据库查询能力。**仅新增 `mcp_server.py` 一个入口文件，不改动任何现有核心代码**，
> 与原有命令行、Web API、Docker 部署完全并存。

---

## 1. 它能做什么

| 工具 | 参数 | 作用 | 复用的现有核心逻辑 |
|------|------|------|--------------------|
| `query_db` | `question: string` | 自然语言查询数据库（NL → SQL → 安全校验 → 只读执行 → 返回数据） | `core/agent.py::TextToSQLAgent.run()`（完整 LangGraph 链路 + SQLGuard + 两级缓存 + Self-Correction） |
| `get_schema` | `table_name?: string` | 查看表结构：表清单、列名/类型/是否可空/主键 | `core/database.py::DatabaseManager`（`get_table_names` + SQLAlchemy Inspector，轻量跨引擎读取） |
| `explain_sql` | `sql: string` | 解释 SQL 执行计划：安全拦截 + 真实 `EXPLAIN` + 规则式优化建议 | `harness/sql_guard.py::SQLGuard`（安全红线）、`core/database.py::query_readonly`（执行 EXPLAIN）、`services/sql_optimizer.py::SQLOptimizer.analyze` |

> **工作流建议**：先 `get_schema` 了解当前库有哪些表/字段 → 再 `query_db` 提问题 或
> `explain_sql` 检查某条 SQL 的执行计划。

### 安全红线（与 Web 端一致）
- `query_db` 全程只读：SQLGuard 物理拦截 + 只读事务执行，**不产生任何写操作**。
- `explain_sql` 仅接受 `SELECT` / `WITH` 开头的只读语句；对 `DROP/UPDATE/DELETE/INSERT` 等一律拒绝并返回拦截原因。
- `get_schema` 只做列级元数据读取（不复用昂贵的 `get_table_info()` 样例统计）。

---

## 2. 环境要求

- Python **3.10+**（与主项目一致）
- 已安装主项目依赖（`pip install -r requirements.txt`）——因为工具内部 import `core/agents/services/harness`
- 已安装 MCP SDK：`pip install mcp`（兼容 **1.x（FastMCP）与 2.x（MCPServer）**，`mcp_server.py` 会自动识别）

> 提示：如果系统里同时有多个 Python，务必让**启动 MCP 的那个 Python** 同时具备上面两项。

---

## 3. 配置 Claude Desktop

找到配置文件并加入 `mcpServers`：

| 系统 | 配置文件路径 |
|------|--------------|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "text2sql": {
      "command": "python",
      "args": ["E:/Text-to-SQL-Agent/mcp_server.py"],
      "env": {
        "DEEPSEEK_API_KEY": "sk-你的百炼-Key",
        "GLM_API_KEY": "（可省略；或填同一把百炼 Key / 其它 GLM 兼容 Key）",
        "OPENAI_API_KEY": "sk-你的百炼-Key",
        "OPENAI_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "ROUTER_MODEL": "qwen3.8-flash",
        "GENERATOR_MODEL": "deepseek-v4-pro-0813",
        "CRITIC_MODEL": "deepseek-v4-pro-0813",
        "RERANKER_MODEL": "qwen3.8-flash",
        "DB_TYPE": "mysql",
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_PORT": "3306",
        "MYSQL_DATABASE": "beauty_analysis",
        "MYSQL_USER": "root",
        "MYSQL_PASSWORD": "你的数据库密码"
      }
    }
  }
}
```

### 说明
- **模型 Key（按模型名路由）**：四角色模型为 router/reranker=`qwen3.8-flash`、
  generator/critic=`deepseek-v4-pro-0813`，**两者均为百炼模型，可共用同一把百炼 Key**。
  `model_client.py` 按模型名前缀自动路由 Key 槽位：`deepseek*`→`DEEPSEEK_API_KEY`、
  `qwen*`→百炼（`OPENAI_API_KEY`/`DASHSCOPE_API_KEY`）、`glm*`→`GLM_API_KEY`；某槽位为空
  时自动回退到其它已配 Key。**因此最少只需一把百炼 Key**（填 `DEEPSEEK_API_KEY` 或
  `OPENAI_API_KEY` 均可调通全部角色）。README 示例同时列出 `DEEPSEEK_API_KEY` 与
  `GLM_API_KEY` 两个槽位，方便你有 DeepSeek / GLM 专用 Key 时按需补充；**若只为 qwen+deepseek
  服务，也可只留一把百炼 Key，其余删除**。
- **数据库连接二选一**：
  1. 如上通过 `env` 传 `DB_TYPE` + `MYSQL_*` / `PG_*` / `DB_PATH`（sqlite）；
  2. 或者**保持已保存连接**：项目 `data/connection.json` 若存在，`DatabaseManager`
     会**优先用它**（此时 `env` 里的 DB 变量可省）。要切库请改/删该文件后用 env 指定。
- **数据库名留空**等价于 SQLite 默认文件库（不会报错，只会查不到外部表）。
- Windows 若 `python` 不在 PATH，`command` 请填虚拟环境解释器的**绝对路径**，
  例如 `"C:\\Users\\你\\.venv\\Scripts\\python.exe"`。

---

## 4. 在 Claude 里怎么用

配置并重启 Claude Desktop 后，对话里会看到一个 `text2sql` 的 MCP 图标/工具列表。

示例提问：
- 「用 text2sql 查一下 product_info 有哪些字段」→ 调 `get_schema`
- 「统计当前库 sales_order 按产品分组的销量 Top10」→ 调 `query_db`
- 「分析这条 SQL 的执行计划：SELECT * FROM sales_order WHERE price > 100」→ 调 `explain_sql`

也可以让 Claude 自主编排：先看结构，再提问，数据不足再追问。

---

## 5. 独立自检（不依赖 Claude Desktop）

在本机（项目根目录）直接运行：

```bash
# 逻辑自检：依次打印 get_schema / explain_sql(含拦截分支) / query_db 的返回 JSON
python mcp_server.py --self-test
```

- 第 1~3 步**不需要模型 Key**（表结构/执行计划/拦截都是纯本地+数据库）；
- 最后的 `query_db` 需要模型 Key 且数据库可连。

更完整的 MCP 协议握手验证（手动）：

```bash
pip install mcp
# 法一：MCP Inspector 网页调试
mcp dev E:/Text-to-SQL-Agent/mcp_server.py

# 法二：命令行直接跑
python E:/Text-to-SQL-Agent/mcp_server.py
# 然后按 MCP stdio 协议发送 JSON-RPC：
#   initialize → tools/list 应返回 query_db / get_schema / explain_sql
```

### 在 Docker 容器里验证（可选）

```bash
docker cp mcp_server.py text2sql-app:/app/mcp_server.py
docker exec text2sql-app python -c "import mcp_server; print(mcp_server._HAS_MCP)"
# 若容器未装 mcp SDK（打印 False）→ 装一个用于验证：
docker exec text2sql-app pip install mcp
docker exec text2sql-app python mcp_server.py --self-test
```

---

## 6. 排查指南

| 现象 | 原因与处理 |
|------|-----------|
| Claude Desktop 里看不到 text2sql 工具 | ① 启动用的 Python 没装 `mcp`：`pip install mcp`；② 该 Python 没装项目依赖；③ 配置文件 JSON 语法错（看 Claude 的 MCP 报错） |
| 工具列表有，但调用报 `Unknown tool` | 客户端可能缓存了旧名；重启 Claude Desktop。正确工具名即 `query_db`/`get_schema`/`explain_sql` |
| `get_schema` 返回空 / 不是你要的库 | `data/connection.json` 保存连接优先于 `env`——要么删它，要么确认它就是目标库 |
| `query_db` 报 `400 Arrearage` | 模型 Key 对应的厂商账号**欠费**（外部账单问题，非代码）。见 CLAUDE.md 修复记录；先去厂商控制台充值，或换另一个 Key 供应商的变量 |
| `explain_sql` 的 plan 为空但 suggestions 有值 | 数据库对 EXPLAIN 支持有限/语句复杂导致真实计划失败；此时仍返回规则式优化建议，属正常降级 |
| MCP 进程异常无输出 | 所有日志走 **stderr**，stdout 只承载协议。启动时把 `2>` 重定向到文件看：`python mcp_server.py 2> mcp.log` |

---

## 7. 与现有系统的关系

- **不影响**命令行运行、Web API（FastAPI/SSE）、Docker 部署——MCP 只是项目根目录多出一个入口文件。
- **不修改**核心代码；若把 `mcp_server.py` 删除，主系统行为与之前完全一致。
- 模型调用仍统一走 `model_client.py`；数据库仍用 `core/database.py` 单例；
  安全仍由 `SQLGuard` 兜底。所有约定与主项目完全一致。
