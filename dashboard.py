"""
==============================================================================
智能数据大屏 & 交互界面 — Streamlit 实现
==============================================================================
设计思路：
  将整个系统通过 Streamlit 组织为三个核心面板：

  1. 🐱 智能对话面板
     - 用户输入自然语言问题
     - 显示生成的 SQL + 执行结果 + 缓存命中状态
     - 支持上下文追问

  2. 📊 智能数据大屏
     - 关键 KPI 卡片（今日销售额 / 订单数 / 用户数 / 商品数）
     - Plotly 趋势图（每日销售趋势 / 类别分布）
     - AgGrid 明细数据展示
     - AI 大屏控制器（侧边栏：语音/文本控制过滤条件）

  3. ⚙️ 系统监控面板
     - 缓存命中率仪表盘
     - Token 消耗估算
     - 查询日志

  大屏 AI 控制器设计：
  当用户输入"聚焦华南区"或"切换到月度视图"等指令时，
  Agent 会解析意图并动态修改大屏的过滤条件和图表类型。
==============================================================================
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ============================================================================
# 页面配置（必须放在最前面）
# ============================================================================

st.set_page_config(
    page_title="企业级智能数据分析 Agent",
    page_icon="🐱",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================================
# 日志配置
# ============================================================================

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("dashboard")


# ============================================================================
# 初始化 Session State
# ============================================================================

def init_session_state():
    """初始化 Streamlit Session State"""
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "你好！我是你的数据分析助手。请输入自然语言问题，我会帮你生成 SQL 并查询数据。"}
        ]
    if "last_result" not in st.session_state:
        st.session_state.last_result = None
    if "dashboard_state" not in st.session_state:
        st.session_state.dashboard_state = {
            "region_filter": "全部",
            "view_mode": "daily",  # daily | monthly | category
            "chart_type": "bar",   # bar | line | pie
            "date_range": "全部",
        }
    if "query_log" not in st.session_state:
        st.session_state.query_log = []
    if "agent_ready" not in st.session_state:
        st.session_state.agent_ready = False
    if "uploaded_content" not in st.session_state:
        st.session_state.uploaded_content = None
    if "uploaded_filename" not in st.session_state:
        st.session_state.uploaded_filename = None


# ============================================================================
# 懒加载模块
# ============================================================================

@st.cache_resource
def init_resources():
    """
    初始化所有核心资源（只执行一次）。

    使用 st.cache_resource 确保在整个应用生命周期中只初始化一次。
    """
    from config import CONFIG
    from database import DatabaseManager

    # 初始化数据库
    db = DatabaseManager()
    db.initialize()

    # 初始化向量库
    from vector_store import SchemaVectorStore
    try:
        vector_store = SchemaVectorStore()
        vector_store.build_index()
    except Exception as e:
        logger.warning(f"向量库初始化失败: {e}")

    # 初始化 Agent
    from agent import TextToSQLAgent
    agent = TextToSQLAgent()

    # 初始化缓存
    from cache import get_cache
    cache = get_cache()

    # 初始化 Route Chain 和文件处理器
    try:
        from file_processor import RouteChain, FileProcessor, DocumentRAG
        route_chain = RouteChain()
        file_processor = FileProcessor()
        doc_rag = DocumentRAG()
    except Exception as e:
        logger.warning(f"文件处理模块初始化失败: {e}")
        route_chain = file_processor = doc_rag = None

    return db, agent, cache, route_chain, file_processor, doc_rag


# ============================================================================
# 工具函数
# ============================================================================

def format_number(n: float) -> str:
    """格式化大数字，如 1234567 -> 123.5万"""
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    elif n >= 10000:
        return f"{n / 10000:.1f}万"
    elif n >= 1000:
        return f"{n / 1000:.1f}k"
    return f"{n:.0f}"


def get_color_by_category(category: str) -> str:
    """根据类别返回颜色"""
    colors = {
        "基础聚合": "#3498db",
        "多表关联": "#2ecc71",
        "时间分析": "#e74c3c",
        "条件过滤": "#f39c12",
        "高级分析": "#9b59b6",
        "排序分析": "#1abc9c",
        "复杂分析": "#e67e22",
    }
    return colors.get(category, "#95a5a6")


# ============================================================================
# 大屏控制指令解析
# ============================================================================

DASHBOARD_CONTROL_PROMPT = """
你是一个智能大屏控制器。用户的输入可能包含对当前大屏显示的控制指令。
请解析用户意图，返回 JSON 格式的控制指令。

可用的控制操作:
1. region_filter: "全部" | "广东" | "北京" | "上海" | "浙江" | "江苏" | "四川" ...
2. view_mode: "daily" (日视图) | "monthly" (月视图) | "category" (类别视图)
3. chart_type: "bar" (柱状图) | "line" (折线图) | "pie" (饼图)

用户输入: {user_input}

如果输入是纯数据查询问题（如"销售额是多少"），不包含对图表的控制意图，
则返回: {{"type": "query", "intent": "data_query", "text": "{user_input}"}}

如果输入包含控制意图（如"聚焦广东"、"切换到月度视图"、"用饼图显示"），
则返回: {{"type": "control", "intent": "dashboard_control", "region_filter": "广东", "view_mode": "monthly", "chart_type": "pie"}}

只输出 JSON，不要其他内容。
"""


def parse_dashboard_control(user_input: str) -> Dict:
    """
    解析用户输入是否包含大屏控制指令。

    返回:
        {"type": "query"} 或 {"type": "control", ... 控制参数 }
    """
    try:
        from agent import LLMClient
        llm = LLMClient()
        prompt = DASHBOARD_CONTROL_PROMPT.format(user_input=user_input)
        response = llm.generate(prompt)
        # 提取 JSON
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception as e:
        logger.warning(f"控制指令解析失败: {e}")
    return {"type": "query", "intent": "data_query"}


# ============================================================================
# 面板 1: KPI 指标卡
# ============================================================================

def render_kpi_cards(db):
    """渲染顶部 KPI 指标卡片"""
    try:
        total_users = db.query("SELECT COUNT(*) as cnt FROM users").iloc[0]["cnt"]
        total_orders = db.query("SELECT COUNT(*) as cnt FROM orders").iloc[0]["cnt"]
        total_products = db.query("SELECT COUNT(*) as cnt FROM products").iloc[0]["cnt"]
        total_revenue = db.query("SELECT COALESCE(SUM(total_amount), 0) as total FROM orders WHERE status = '已完成'").iloc[0]["total"]
    except Exception:
        total_users = total_orders = total_products = total_revenue = 0

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        padding: 20px; border-radius: 10px; text-align: center;">
                <h3 style="color: white; margin: 0; font-size: 14px;">👥 总用户数</h3>
                <p style="color: white; margin: 0; font-size: 32px; font-weight: bold;">
                    {format_number(total_users)}</p>
            </div>
            """, unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                        padding: 20px; border-radius: 10px; text-align: center;">
                <h3 style="color: white; margin: 0; font-size: 14px;">📦 总订单数</h3>
                <p style="color: white; margin: 0; font-size: 32px; font-weight: bold;">
                    {format_number(total_orders)}</p>
            </div>
            """, unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
                        padding: 20px; border-radius: 10px; text-align: center;">
                <h3 style="color: white; margin: 0; font-size: 14px;">🏷️ 商品种类</h3>
                <p style="color: white; margin: 0; font-size: 32px; font-weight: bold;">
                    {format_number(total_products)}</p>
            </div>
            """, unsafe_allow_html=True,
        )

    with col4:
        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);
                        padding: 20px; border-radius: 10px; text-align: center;">
                <h3 style="color: white; margin: 0; font-size: 14px;">💰 已完成总销售额</h3>
                <p style="color: white; margin: 0; font-size: 32px; font-weight: bold;">
                    ¥{format_number(total_revenue)}</p>
            </div>
            """, unsafe_allow_html=True,
        )


# ============================================================================
# 面板 2: 趋势图表
# ============================================================================

def render_sales_trend(db, view_mode: str):
    """根据视图模式渲染销售趋势图"""
    st.markdown("### 📈 销售趋势分析")

    try:
        if view_mode == "monthly":
            df = db.query("""
                SELECT strftime('%Y-%m', order_date) AS period,
                       SUM(total_amount) AS sales,
                       COUNT(*) AS orders
                FROM orders
                WHERE status = '已完成'
                GROUP BY period
                ORDER BY period
            """)
        else:
            df = db.query("""
                SELECT order_date AS period,
                       SUM(total_amount) AS sales,
                       COUNT(*) AS orders
                FROM orders
                WHERE status = '已完成'
                GROUP BY order_date
                ORDER BY order_date
            """)

        if not df.empty:
            df["sales"] = pd.to_numeric(df["sales"], errors="coerce")
            df["orders"] = pd.to_numeric(df["orders"], errors="coerce")

            fig = make_subplots(specs=[[{"secondary_y": True}]])
            fig.add_trace(
                go.Bar(x=df["period"], y=df["sales"], name="销售额", marker_color="#3498db"),
                secondary_y=False,
            )
            fig.add_trace(
                go.Scatter(x=df["period"], y=df["orders"], name="订单数",
                          mode="lines+markers", marker_color="#e74c3c"),
                secondary_y=True,
            )
            fig.update_layout(
                height=350,
                hovermode="x unified",
                margin=dict(l=0, r=0, t=20, b=0),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            fig.update_xaxes(title_text="时间")
            fig.update_yaxes(title_text="销售额 (元)", secondary_y=False)
            fig.update_yaxes(title_text="订单数", secondary_y=True)
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.info(f"暂无趋势数据: {e}")


def render_category_chart(db, chart_type: str):
    """渲染商品类别分析图表"""
    st.markdown("### 🏷️ 商品类别分析")

    try:
        df = db.query("""
            SELECT p.category,
                   COUNT(DISTINCT o.order_id) AS order_count,
                   SUM(o.quantity) AS total_qty,
                   ROUND(SUM(o.total_amount), 2) AS total_sales
            FROM orders o
            JOIN products p ON o.product_id = p.product_id
            WHERE o.status = '已完成'
            GROUP BY p.category
            ORDER BY total_sales DESC
        """)

        if not df.empty:
            if chart_type == "pie":
                fig = px.pie(
                    df, values="total_sales", names="category",
                    title="各品类销售额占比",
                    color_discrete_sequence=px.colors.qualitative.Set3,
                )
            elif chart_type == "bar":
                fig = px.bar(
                    df, x="category", y="total_sales",
                    title="各品类销售额",
                    color="total_sales",
                    color_continuous_scale="Blues",
                    text_auto=".2s",
                )
            else:
                fig = px.line(
                    df, x="category", y="total_sales",
                    title="各品类销售趋势",
                    markers=True,
                )
            fig.update_layout(height=350, margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.info(f"暂无类别数据: {e}")


def render_region_chart(db, region_filter: str):
    """渲染地区销售图表"""
    st.markdown("### 🗺️ 地区销售分析")

    region_where = f"AND u.province = '{region_filter}'" if region_filter != "全部" else ""

    try:
        df = db.query(f"""
            SELECT u.province,
                   COUNT(DISTINCT o.order_id) AS order_count,
                   ROUND(SUM(o.total_amount), 2) AS total_sales,
                   ROUND(AVG(o.total_amount), 2) AS avg_amount
            FROM orders o
            JOIN users u ON o.user_id = u.user_id
            WHERE o.status = '已完成' {region_where}
            GROUP BY u.province
            ORDER BY total_sales DESC
            LIMIT 15
        """)

        if not df.empty:
            fig = px.bar(
                df, x="province", y="total_sales",
                color="total_sales",
                color_continuous_scale="Viridis",
                text_auto=".2s",
                title=f"各省销售额{' (' + region_filter + ')' if region_filter != '全部' else ''}",
            )
            fig.update_layout(height=350, margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.info(f"暂无地区数据: {e}")


# ============================================================================
# 面板 3: 明细数据表
# ============================================================================

def render_detail_table(db):
    """渲染订单明细数据"""
    st.markdown("### 📋 订单明细数据")

    try:
        # 只取最近 100 条显示
        df = db.query("""
            SELECT o.order_id, o.order_date, u.username AS 用户名,
                   p.product_name AS 商品, p.category AS 类别,
                   o.quantity AS 数量, o.total_amount AS 金额,
                   o.status AS 状态, o.payment_method AS 支付方式
            FROM orders o
            JOIN users u ON o.user_id = u.user_id
            JOIN products p ON o.product_id = p.product_id
            ORDER BY o.order_date DESC
            LIMIT 100
        """)

        if not df.empty:
            # 使用 st.dataframe 替代 AgGrid（避免额外依赖问题）
            st.dataframe(
                df,
                use_container_width=True,
                height=400,
                column_config={
                    "金额": st.column_config.NumberColumn(format="¥%.2f"),
                },
            )

            # 导出按钮
            csv = df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 导出 CSV",
                data=csv,
                file_name=f"orders_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )
    except Exception as e:
        st.info(f"暂无明细数据: {e}")


# ============================================================================
# 面板 4: 缓存监控
# ============================================================================

def render_cache_monitor():
    """渲染缓存监控面板"""
    st.markdown("### ⚡ 缓存性能监控")

    try:
        from cache import get_cache
        cache = get_cache()
        stats = cache.get_stats()

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("综合命中率", f"{stats['hit_rate']:.1f}%",
                     help="L1 + L2 综合缓存命中率")
        with col2:
            st.metric("L1 精确命中率", f"{stats['l1_hit_rate']:.1f}%",
                     help="完全相同的提问命中率")
        with col3:
            st.metric("L2 语义命中率", f"{stats['l2_hit_rate']:.1f}%",
                     help="语义相似提问命中率")
        with col4:
            st.metric("总查询次数", stats["total_queries"])

        # 缓存命中率仪表盘
        hit_rate = stats["hit_rate"] / 100
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=hit_rate * 100,
            title={"text": "缓存命中率"},
            delta={"reference": 50},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "darkblue"},
                "steps": [
                    {"range": [0, 30], "color": "#ffcccc"},
                    {"range": [30, 60], "color": "#ffffcc"},
                    {"range": [60, 100], "color": "#ccffcc"},
                ],
                "threshold": {
                    "line": {"color": "red", "width": 4},
                    "value": 90,
                },
            },
        ))
        fig.update_layout(height=200, margin=dict(l=30, r=30, t=50, b=0))
        st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.info(f"缓存监控暂时不可用: {e}")


# ============================================================================
# 面板 5: 查询日志
# ============================================================================

def render_query_log():
    """渲染查询日志"""
    st.markdown("### 📝 最近查询记录")

    logs = st.session_state.get("query_log", [])
    if not logs:
        st.info("暂无查询记录")
        return

    log_df = pd.DataFrame(logs)
    display_cols = [c for c in ["时间", "问题", "状态", "耗时(秒)", "缓存"] if c in log_df.columns]
    if display_cols:
        st.dataframe(log_df[display_cols].tail(20), use_container_width=True, height=300)


# ============================================================================
# 面板 6: 评估报告展示
# ============================================================================

def render_eval_report():
    """渲染评估报告"""
    st.markdown("### 📊 自动化评估报告")

    try:
        import os
        report_path = "eval_results/eval_report.json"
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                report = json.load(f)

            metrics = report.get("overall_metrics", {})

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("执行准确率", f"{metrics.get('execution_accuracy', 0):.1f}%",
                         help="SQL 执行结果与标准答案一致的比率")
            with col2:
                st.metric("SQL 语法正确率", f"{metrics.get('sql_syntax_validity', 0):.1f}%",
                         help="生成的 SQL 能被数据库正常执行的比率")
            with col3:
                st.metric("测试用例数", metrics.get("total_valid", 0))

            # 分类表现条形图
            cat_metrics = report.get("category_metrics", {})
            if cat_metrics:
                cat_df = pd.DataFrame([
                    {"类别": cat, "准确率": v["accuracy"]}
                    for cat, v in cat_metrics.items()
                ])
                fig = px.bar(
                    cat_df, x="类别", y="准确率",
                    color="准确率",
                    color_continuous_scale="RdYlGn",
                    range_color=[0, 100],
                    text_auto=".1f",
                )
                fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("尚未运行评估，请在终端执行: py -3 evaluator.py")
    except Exception as e:
        st.info(f"评估报告加载失败: {e}")


# ============================================================================
# 智能对话处理
# ============================================================================

def process_question(question: str, agent, route_chain=None, file_processor=None, doc_rag=None):
    """
    处理用户问题：Route Chain 分流 → 大屏控制 / 文件分析 / SQL 查询。
    """
    uploaded_content = st.session_state.get("uploaded_content")
    uploaded_filename = st.session_state.get("uploaded_filename")
    has_file = uploaded_content is not None

    # Route Chain：判断是查数据库还是分析文件
    if route_chain and has_file:
        route, reason = route_chain.classify(question, has_uploaded_file=True)
        if route == "doc":
            # 走文档分析路由
            if doc_rag and uploaded_content:
                try:
                    doc_rag.load(uploaded_content)
                    result = doc_rag.query(question, use_llm=True)
                    st.session_state.query_log.append({
                        "时间": datetime.now().strftime("%H:%M:%S"),
                        "问题": question[:30] + ("..." if len(question) > 30 else ""),
                        "状态": "✅" if result.get("error") is None else "❌",
                        "耗时(秒)": "-",
                        "缓存": "文件分析",
                    })
                    return {
                        "type": "doc_analysis",
                        "answer": result.get("answer", ""),
                        "error": result.get("error"),
                    }
                except Exception as e:
                    return {"type": "error", "error": str(e)}

    # 先尝试解析大屏控制
    control = parse_dashboard_control(question)
    if control.get("type") == "control":
        ds = st.session_state.dashboard_state
        if control.get("region_filter"):
            ds["region_filter"] = control["region_filter"]
        if control.get("view_mode"):
            ds["view_mode"] = control["view_mode"]
        if control.get("chart_type"):
            ds["chart_type"] = control["chart_type"]
        return {
            "type": "control",
            "message": f"✅ 已切换视图：地区={ds['region_filter']}, 模式={ds['view_mode']}, 图表={ds['chart_type']}",
        }

    # 否则作为数据查询
    try:
        result = agent.run(question)
        # 记录日志
        st.session_state.query_log.append({
            "时间": datetime.now().strftime("%H:%M:%S"),
            "问题": question[:30] + ("..." if len(question) > 30 else ""),
            "状态": "✅" if result.get("error") is None else "❌",
            "耗时(秒)": f"{result.get('execution_time', 0):.2f}",
            "缓存": result.get("cache_source", "无") if result.get("cache_hit") else "无",
        })
        return result
    except Exception as e:
        return {"error": str(e), "type": "error"}


def render_chat_panel(agent, route_chain=None, file_processor=None, doc_rag=None):
    """渲染智能对话面板"""
    st.markdown("### 🐱 智能数据分析助手")
    st.markdown("输入自然语言问题，AI 自动生成 SQL 并展示结果。")

    # 显示已上传的文件
    if st.session_state.get("uploaded_filename"):
        st.info(f"📎 已加载文件: {st.session_state.uploaded_filename} — 可直接询问文件内容")

    # 输入区域
    col1, col2 = st.columns([5, 1])
    with col1:
        user_input = st.text_input(
            "💬 输入问题（也可输入大屏控制指令如「聚焦广东」「切换月度视图」）:",
            key="user_question",
            placeholder="例：统计每个省份的订单总金额",
        )
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        submit = st.button("🚀 查询", type="primary", use_container_width=True)

    # 快捷问题
    quick_questions = [
        "统计每个省份的订单总金额",
        "查询消费最多的前5名用户",
        "统计每个月的销售额",
        "查询各个商品类别的销售数量",
    ]
    cols = st.columns(4)
    for i, q in enumerate(quick_questions):
        with cols[i]:
            if st.button(q, key=f"quick_{i}", use_container_width=True):
                user_input = q
                submit = True

    # 处理查询
    if submit and user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})

        with st.spinner("🤔 正在思考并生成 SQL..."):
            result = process_question(user_input, agent, route_chain, file_processor, doc_rag)

        if result.get("type") == "control":
            st.session_state.messages.append({
                "role": "assistant", "content": result["message"],
            })
            st.rerun()
        elif result.get("type") == "doc_analysis":
            # 文件分析结果
            assistant_msg = "📄 **文件分析结果**\n\n"
            if result.get("answer"):
                assistant_msg += result["answer"]
            if result.get("error"):
                assistant_msg += f"\n\n❌ **错误**: {result['error']}"
            st.session_state.messages.append({
                "role": "assistant", "content": assistant_msg,
            })
        elif result.get("type") == "error":
            st.session_state.messages.append({
                "role": "assistant",
                "content": f"❌ **处理失败**: {result.get('error', '未知错误')}",
            })
        else:
            assistant_msg = f"**SQL:** ```sql\n{result.get('sql', '无')}\n```\n\n"
            if result.get("cache_hit"):
                assistant_msg += f"⚡ **缓存命中**: [{result['cache_source']}] "
            if result.get("execution_time"):
                assistant_msg += f"⏱️ **耗时**: {result['execution_time']:.2f}s  \n"
            if result.get("token_estimate"):
                assistant_msg += f"📊 **估算 Token**: {result['token_estimate']}  \n"

            if result.get("error"):
                assistant_msg += f"❌ **错误**: {result['error']}"
            elif result.get("result"):
                rows = result["result"]
                assistant_msg += f"📋 **结果**: 共 {len(rows)} 行  \n"

            # 如果有关键操作追踪，展示简略信息
            trace = result.get("trace")
            if trace and trace.get("spans"):
                n_spans = len(trace["spans"])
                total_t = trace.get("total_time", 0)
                assistant_msg += f"🔍 **链路追踪**: {n_spans} 个操作, 总耗时 {total_t:.2f}s  \n"

            st.session_state.last_result = result
            st.session_state.messages.append({"role": "assistant", "content": assistant_msg})

    # 显示聊天历史
    for msg in st.session_state.messages[-10:]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 如果有结果，展示数据表格
    last_result = st.session_state.get("last_result")
    if last_result and last_result.get("result"):
        st.markdown("---")
        st.markdown("#### 📋 查询结果")
        try:
            df = pd.DataFrame(last_result["result"])
            st.dataframe(df, use_container_width=True, height=300)

            # 如果结果适合可视化，自动生成图表
            if len(df.columns) >= 2 and len(df) <= 50:
                numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
                category_cols = df.select_dtypes(include=["object"]).columns.tolist()
                if numeric_cols and category_cols:
                    fig = px.bar(
                        df, x=category_cols[0], y=numeric_cols[0],
                        title="查询结果可视化",
                        color=numeric_cols[0],
                        color_continuous_scale="Viridis",
                    )
                    st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.text(f"结果预览: {last_result['result'][:3]}")


# ============================================================================
# 侧边栏
# ============================================================================

def render_sidebar(agent, route_chain=None, file_processor=None, doc_rag=None):
    """渲染侧边栏"""
    with st.sidebar:
        st.markdown("# 🐱 数据分析 Agent")
        st.markdown("---")

        # 大屏控制面板
        st.markdown("### 🎛️ 大屏控制器")
        ds = st.session_state.dashboard_state

        ds["region_filter"] = st.selectbox(
            "📍 地区筛选",
            ["全部", "广东", "北京", "上海", "浙江", "江苏", "四川", "湖北", "山东"],
            index=["全部", "广东", "北京", "上海", "浙江", "江苏", "四川", "湖北", "山东"].index(ds["region_filter"]),
        )
        ds["view_mode"] = st.radio(
            "📊 视图模式",
            ["daily", "monthly", "category"],
            format_func=lambda x: {"daily": "日视图", "monthly": "月视图", "category": "类别视图"}[x],
            index=["daily", "monthly", "category"].index(ds["view_mode"]),
            horizontal=True,
        )
        ds["chart_type"] = st.radio(
            "📈 图表类型",
            ["bar", "line", "pie"],
            format_func=lambda x: {"bar": "柱状图", "line": "折线图", "pie": "饼图"}[x],
            index=["bar", "line", "pie"].index(ds["chart_type"]),
            horizontal=True,
        )

        st.markdown("---")
        st.markdown("💡 **提示**: 你也可以在对话中输入")
        st.markdown("- <<聚焦广东>>")
        st.markdown("- <<切换到月度视图>>")
        st.markdown("- <<用饼图展示>>")

        # ====== 文件上传区 ======
        st.markdown("---")
        st.markdown("### 📁 文件上传")
        uploaded_file = st.file_uploader(
            "上传 PDF 或 Excel 文件进行分析",
            type=["pdf", "xlsx", "xls", "csv"],
            key="file_uploader",
        )

        if uploaded_file is not None and file_processor:
            try:
                file_bytes = uploaded_file.read()
                content = file_processor.process(file_bytes, uploaded_file.name)
                if content.error:
                    st.error(f"解析失败: {content.error}")
                else:
                    st.session_state.uploaded_content = content
                    st.session_state.uploaded_filename = uploaded_file.name

                    # 显示文件摘要
                    if content.file_type == "pdf":
                        st.success(f"📄 PDF 已解析: {len(content.pages)} 页")
                    else:
                        st.success(f"📊 {uploaded_file.name} 已解析: {content.row_count} 行")

                    with st.expander("文件预览"):
                        if content.summary:
                            st.text(content.summary[:500])

                    if doc_rag:
                        doc_rag.load(content)

                    # 文件问答提示
                    st.info("💡 现在可以在对话中询问文件内容了！")
            except Exception as e:
                st.error(f"文件处理失败: {e}")

        # 清除已上传的文件
        if st.session_state.get("uploaded_content"):
            if st.button("🗑️ 清除文件", use_container_width=True):
                st.session_state.uploaded_content = None
                st.session_state.uploaded_filename = None
                st.rerun()

        # ====== 系统状态 ======
        st.markdown("---")
        st.markdown("### 🖥️ 系统状态")

        # LangSmith 追踪状态
        try:
            from tracing import is_tracing_enabled
            tracing_on = is_tracing_enabled()
            st.caption(f"🔍 LangSmith: {'🟢 已启用' if tracing_on else '⚪ 未启用'}")
        except Exception:
            pass

        try:
            from cache import get_cache
            cache = get_cache()
            stats = cache.get_stats()
            st.metric("缓存命中率", f"{stats['hit_rate']:.1f}%")
        except Exception:
            st.info("缓存未连接")

        if st.button("🔄 清空缓存", use_container_width=True):
            try:
                from cache import get_cache
                get_cache().clear()
                st.success("缓存已清空")
                time.sleep(0.5)
                st.rerun()
            except Exception as e:
                st.error(f"清空失败: {e}")

        if st.button("🏗️ 重建向量索引", use_container_width=True):
            try:
                from vector_store import SchemaVectorStore
                store = SchemaVectorStore()
                store.build_index()
                st.success("向量索引重建完成")
            except Exception as e:
                st.error(f"重建失败: {e}")

        # 数据库状态
        try:
            from database import get_db
            db = get_db()
            info = db.get_table_info()
            for tbl in info:
                st.caption(f"📁 {tbl['table_name']}: {tbl['row_count']} 行")
        except Exception:
            pass


# ============================================================================
# 主布局
# ============================================================================

def main():
    """主渲染函数"""
    init_session_state()

    # 初始化资源
    db, agent, cache, route_chain, file_processor, doc_rag = init_resources()
    st.session_state.agent_ready = True

    # 获取大屏状态
    ds = st.session_state.dashboard_state

    # ====== 顶部标题 ======
    st.markdown(
        """
        <h1 style='text-align: center; color: #2c3e50; margin-bottom: 0;'>
            🐱 企业级智能数据分析指挥中心
        </h1>
        <p style='text-align: center; color: #7f8c8d; margin-top: 0;'>
            Text-to-SQL Agent · 语义缓存 · 混合检索 · 自动化评估
        </p>
        <hr style='margin-top: 0;'>
        """,
        unsafe_allow_html=True,
    )

    # ====== 侧边栏 ======
    render_sidebar(agent, route_chain, file_processor, doc_rag)

    # ====== KPI 卡片 ======
    render_kpi_cards(db)

    # ====== 主内容区域 ======
    tab1, tab2, tab3, tab4 = st.tabs([
        "💬 智能对话", "📊 数据大屏", "⚙️ 系统监控", "📋 评估报告"
    ])

    with tab1:
        render_chat_panel(agent, route_chain, file_processor, doc_rag)

    with tab2:
        # 大屏内容
        st.markdown("## 📊 智能数据大屏")

        # 两列布局：趋势 + 类别
        col_left, col_right = st.columns(2)

        with col_left:
            render_sales_trend(db, ds["view_mode"])

        with col_right:
            render_category_chart(db, ds["chart_type"])

        # 地区分析
        render_region_chart(db, ds["region_filter"])

        # 明细数据
        render_detail_table(db)

    with tab3:
        st.markdown("## ⚙️ 系统监控")

        col_left, col_right = st.columns(2)
        with col_left:
            render_cache_monitor()
        with col_right:
            st.markdown("### 📊 Token 消耗估算")

            # Token 统计
            total_tokens = sum(
                log.get("token_estimate", 0)
                for log in st.session_state.get("query_log", [])
            )
            st.metric("总消耗 (估算)", f"{total_tokens:,} tokens")

            # 如果日志有空，模拟展示
            log_count = len(st.session_state.get("query_log", []))
            st.metric("查询次数", log_count)

            # 最近查询平均耗时
            query_times = [
                log.get("耗时(秒)", 0)
                for log in st.session_state.get("query_log", [])
            ]
            if query_times:
                avg_time = sum(float(t) for t in query_times if t) / len(query_times)
                st.metric("平均查询耗时", f"{avg_time:.2f}s")

        render_query_log()

        # 链路追踪信息
        st.markdown("### 🔍 链路追踪记录")
        try:
            last_result = st.session_state.get("last_result")
            if last_result and last_result.get("trace"):
                trace = last_result["trace"]
                spans = trace.get("spans", [])
                if spans:
                    span_df = pd.DataFrame(spans)
                    st.dataframe(span_df, use_container_width=True, height=200)
                    st.caption(f"总耗时: {trace.get('total_time', 0):.3f}s | 操作数: {len(spans)}")
                else:
                    st.info("暂无追踪记录")
            else:
                st.info("执行一次查询后在此处显示链路追踪详情")
        except Exception as e:
            st.info(f"追踪信息暂不可用: {e}")

        # 系统架构信息
        with st.expander("🏗️ 系统架构信息"):
            st.markdown("""
            ```
            ┌─ Route Chain ─────────────────────────────────────────────┐
            │  用户提问 + (可选: 文件上传)                                │
            │    → SQL_ROUTE (数据库查询)       → Text-to-SQL Agent     │
            │    → DOCUMENT_ROUTE (文件分析)    → Document RAG          │
            │    → CONTROL (大屏控制)           → Dashboard Control     │
            └──────────────────────────────────────────────────────────┘

            Text-to-SQL Agent 内部链路:
            用户提问 → 混合检索(BM25+向量+RRF) → CoT Prompt → LLM SQL生成
                ↓                                                      ↓
            L1/L2缓存 ← 结果返回 ← 执行SQL ← sqlglot校验 ← 自我修正(×3)
                                              ↑
                                        Error Recovery Chain

            可观测性:
            LangSmith 追踪: Schema检索 → Prompt构建 → LLM调用 → SQL执行
            """
            )

    with tab4:
        render_eval_report()

    # ====== 自动刷新 ======
    auto_refresh = st.checkbox("🔄 自动刷新 (30秒)", value=False)
    if auto_refresh:
        time.sleep(30)
        st.rerun()


# ============================================================================
# 入口
# ============================================================================

if __name__ == "__main__":
    main()
