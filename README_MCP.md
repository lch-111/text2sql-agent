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
- （可选）环境变量 **`MCP_API_KEYS`**：**逗号分隔的多个 Key**（每用户一个专属 Token，
  可经 GitHub Issue 自助申请，见 §2.2），用于启用 **Bearer Token 鉴权**（参考瑞幸 MCP 模式）。
  未设置仅启动告警并全放行（不改变原有 stdio 用法）；设置后须以 `--http` 模式启动，
  客户端携带各自专属的 `Authorization: Bearer <Key>` 连接，详见 §2.1。
  > 兼容旧单 Key 变量 `MCP_API_KEY`：`MCP_API_KEYS` 为空时自动回退读取（启动有迁移告警）。

> 提示：如果系统里同时有多个 Python，务必让**启动 MCP 的那个 Python** 同时具备上面两项。

---

## 2.1 Key 鉴权与 HTTP 部署（可选，参考瑞幸 Bearer Token 模式）

默认 **stdio 运输没有 HTTP 头**，因此 `Authorization: Bearer` 校验只有在 **HTTP 模式
（streamable-http）** 下才真正生效。行为约定：

- 未设置任何 Key → 无论 stdio 还是 HTTP 都**全放行**（保持原有用法，不破坏现有工具）。
- 环境变量 **`MCP_API_KEYS`**：**逗号分隔的多个 Key**（每个用户一个专属 Token，形如
  `mcp-sk-...`，获取方式见 §2.2 自助申请）。设置后以 `--http` 启动 → 每个入站 HTTP 请求
  必须携带 `Authorization: Bearer <其中任意一个 Key>`，**缺失或不在列表一律返回 401**
  （覆盖握手与工具调用）；
- 设置了 Key 但仍用 stdio 启动 → stdio 无头可验，工具层会**拒绝调用**并提示改用
  `--http`，避免“看似启用实则裸奔”。
- `--self-test` 直连底层纯函数，**始终跳过鉴权**，可在设置了 Key 的环境下正常自检。
- 兼容旧变量：`MCP_API_KEYS` 为空时自动回退读取旧单 Key 变量 `MCP_API_KEY`（启动有迁移告警）。

**HTTP 方式启动服务**（host / port / path 均可配，默认 `127.0.0.1:8765` 与 `/mcp`）：

```bash
# 管理员把已发放的 Key 用逗号拼成列表注入环境变量即可
MCP_API_KEYS="mcp-sk-用户A的Key,mcp-sk-用户B的Key" python mcp_server.py --http --host 0.0.0.0 --port 8765
```

> Git Bash（MSYS）会把带前导斜杠的命令行参数（如 `--path /mcp`）转成盘符路径导致启动失败；
> cmd / PowerShell 无此问题。默认路径 `/mcp` 无需显式传 `--path`，裸段会自动补前导斜杠。

客户端用自己申请到的 Key 连接（HTTP MCP + headers），以 Claude Code 项目 `.mcp.json` 为例：

```json
{
  "mcpServers": {
    "text2sql": {
      "type": "http",
      "url": "http://<服务器地址>:8765/mcp",
      "headers": { "Authorization": "Bearer mcp-sk-你申请到的那个Key" }
    }
  }
}
```

本地快速验证某个 Key 是否已生效：

```bash
curl -i http://<服务器地址>:8765/mcp                                          # 无头 → 401
curl -i -H "Authorization: Bearer mcp-sk-错误Key" http://<服务器地址>:8765/mcp # → 401
curl -i -H "Authorization: Bearer mcp-sk-你的Key" http://<服务器地址>:8765/mcp # → 放行（进入 MCP 层）
```

---

## 2.2 用户自助申请 Key（每用户一个专属 Token）

为免去管理员手工下发 Key，仓库内置了“**GitHub Issue 申请 + Actions 自动发放**”闭环
（参考瑞幸 MCP 的每用户专属 Token 模式）。Key 通过 **Resend 邮件私密发送**，
不在公开评论区显示。本仓库已内置对应文件：

- `.github/ISSUE_TEMPLATE/apply-key.yml` —— 申请表单模板；
- `.github/workflows/generate-key.yml` —— 自动生成随机 Key 并**邮件私密发送**的工作流。

**申请流程（用户侧）**
1. 在本仓库 **New issue → 选择“申请 MCP API Key”模板**，填好称呼/邮箱后提交；提交即自动打上
   `key-request` 标签。
2. 稍等片刻，GitHub Actions 会把一条**专属随机 Key**（形如 `mcp-sk-xxx-yyy-zzz`，每次申请
   都不同）**私密发送到你填写的邮箱**；Issue 评论区只回复“✅ Key 已发送至你的邮箱”，
   不出现 Key 本体。
3. 收到邮件拿到 Key 后，按 §2.1 用 HTTP MCP + `Authorization: Bearer <你的Key>` 连接即可。
   若暂时 401，说明管理员还没把这个 Key 登记进服务器环境的 `MCP_API_KEYS`，请稍候。

**管理员侧（让新 Key 生效）**
1. 首次使用前，先在仓库 **Settings → Secrets and variables → Actions** 添加 Secret
   `RESEND_API_KEY`（Resend 免费注册：<https://resend.com/api-keys>），否则发信步骤会失败。
   建议同时在 Resend 验证发件域名——未验证时测试发件人（`onboarding@resend.dev`）
   **只能发到账号本人的邮箱**，真实外部申请人会收不到；验证后如需自定义发件人，可给
   `resend/resend-action@v3` 补 `from` 输入。
2. 再创建 `key-request` 标签（Settings → Labels，或 `gh label create key-request`），
   否则表单无法提交。
3. 收到带 `key-request` 标签的 Issue（Actions 已邮件发出 Key）后，把该 Key **追加**到服务器
   环境变量 `MCP_API_KEYS`（逗号分隔）并重启 `mcp_server.py`，见 §2.1 启动示例。
4. 吊销 / 轮换：从 `MCP_API_KEYS` 移除 / 替换对应 Key 后重启即可。

**WorkBuddy / Claude Desktop / Claude Code 配置示例**（都指向同一个 HTTP MCP 地址，
各自携带自己的 Key）：
- **WorkBuddy**：新建“远程 / HTTP MCP”时填——地址
  `http://<服务器地址>:8765/mcp`，请求头 `Authorization: Bearer mcp-sk-你的Key`。
- **Claude Code / Claude Desktop**（`mcpServers`）：

```json
{
  "mcpServers": {
    "text2sql": {
      "type": "http",
      "url": "http://<服务器地址>:8765/mcp",
      "headers": { "Authorization": "Bearer mcp-sk-你的Key" }
    }
  }
}
```

> 提示：Key 通过邮件私密发送（评论区不显示），仍请勿把 Key 转发或提交进代码库。
> 老单 Key 变量 `MCP_API_KEY` 仍兼容；新申请的多 Key 统一走 `MCP_API_KEYS`。

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
| 配置了 `MCP_API_KEYS` 但工具被拒（未授权） | stdio 无 HTTP 头，无法完成 Bearer 校验。改用 §2.1 的 `--http` 启动并以 HTTP 客户端（url + headers）连接；或清空 Key 回到全放行 |
| HTTP 模式请求一律 401 | 请求头缺失，或 `Authorization: Bearer <Key>` 不在 `MCP_API_KEYS` 列表内。对照 §2.1/§2.2 的 headers 写法；未配置任何 Key 时不会出现 401 |
| 用 GitHub Issue 申请的 Key 仍 401 | Actions 只负责“发放”，该 Key 尚未被管理员追加进服务器 `MCP_API_KEYS`。联系管理员登记后重启；或核对 Key 是否抄错 / 泄露后被吊销 |

---

## 7. 与现有系统的关系

- **不影响**命令行运行、Web API（FastAPI/SSE）、Docker 部署——MCP 只是项目根目录多出一个入口文件。
- **不修改**核心代码；若把 `mcp_server.py` 删除，主系统行为与之前完全一致。
- 模型调用仍统一走 `model_client.py`；数据库仍用 `core/database.py` 单例；
  安全仍由 `SQLGuard` 兜底。所有约定与主项目完全一致。
