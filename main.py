"""
==============================================================================
企业级智能数据分析 Agent — 主入口
==============================================================================
项目结构:
├── main.py          # 程序入口（CLI 模式 & 启动大屏）
├── config.py        # 全局配置
├── database.py      # SQLite/PostgreSQL 数仓（建表 + 播种 + 元数据）
├── vector_store.py  # TF-IDF Schema 语义检索
├── hybrid_search.py # BM25 + 向量检索 + RRF 重排序
├── sql_validator.py # sqlglot 语法校验 + 表/字段存在性检查
├── cache.py         # L1 精确缓存 + L2 语义缓存
├── agent.py         # Text-to-SQL Agent + CoT + 自我修正
├── tracing.py       # LangSmith 链路追踪
├── file_processor.py# PDF/Excel 文件解析 + Route Chain
├── evaluator.py     # Golden Dataset + 自动化评估
├── dashboard.py     # Streamlit 交互界面 & 智能大屏 + 文件上传
│
├── data/            # 数据目录
│   ├── retail_warehouse.db  # SQLite 数仓
│   ├── golden_dataset.json  # 评估测试集
│   └── init.sql             # PostgreSQL 初始化脚本
├── logs/            # 日志目录
└── eval_results/    # 评估报告输出
==============================================================================
"""

import sys
import argparse
import logging


def setup_logging(debug: bool = False):
    """配置日志"""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_init():
    """初始化数据库和向量库"""
    print("=" * 60)
    print("  初始化数据库和向量库...")
    print("=" * 60)

    from database import DatabaseManager
    db = DatabaseManager()
    db.initialize()

    from vector_store import SchemaVectorStore
    vs = SchemaVectorStore()
    vs.build_index()

    print("\n[OK] 初始化完成！")
    print("  数据库: data/retail_warehouse.db")
    print("  向量库: TF-IDF 索引（基于 jieba 分词）")
    print("  缓存:   Redis/fakeredis（L1 精确 + L2 语义）")


def cmd_query(sql: str):
    """执行原始 SQL 查询"""
    from database import get_db
    db = get_db()
    df = db.query(sql)
    print(df.to_string(index=False))
    print(f"\n共 {len(df)} 行")


def cmd_ask(question: str, no_cache: bool = False):
    """用自然语言提问"""
    from agent import get_agent
    agent = get_agent()
    result = agent.run(question, use_cache=not no_cache)

    print(f"\n{'='*60}")
    print(f"  问题: {result['question']}")
    print(f"{'='*60}")
    print(f"  SQL: {result['sql']}")
    print(f"  耗时: {result['execution_time']:.2f}s")
    if result.get('cache_hit'):
        print(f"  缓存: HIT ({result.get('cache_source', '')})")
    else:
        print("  缓存: MISS")

    if result.get("error"):
        print(f"  [ERROR] {result['error']}")
    else:
        rows = result.get("result", [])
        print(f"  结果: {len(rows)} 行")
        if rows:
            import pandas as pd
            df = pd.DataFrame(rows)
            print("\n" + df.to_string(index=False))


def cmd_eval():
    """运行自动化评估"""
    from evaluator import Evaluator
    from agent import TextToSQLAgent

    evaluator = Evaluator()
    agent = TextToSQLAgent()

    print("开始自动化评估...\n")
    evaluator.run_evaluation(agent)
    report = evaluator.generate_report()
    evaluator.print_summary(report)
    evaluator.save_report(report)


def cmd_dashboard():
    """启动 Streamlit 大屏"""
    import subprocess
    import sys as _sys
    print("启动 Streamlit 大屏...")
    _sys.argv = ["streamlit", "run", "dashboard.py", "--server.port=8501", "--server.headless=true"]
    subprocess.run(_sys.argv)


def cmd_trace():
    """查看追踪配置状态"""
    from tracing import init_tracing, is_tracing_enabled
    enabled = init_tracing()
    if enabled:
        from config import CONFIG
        cfg = CONFIG.tracing
        print("[Tracing] LangSmith 追踪已启用")
        print(f"  Project:  {cfg.project}")
        print(f"  Endpoint: {cfg.endpoint}")
    else:
        print("[Tracing] LangSmith 追踪未启用")
        print("  设置 LANGSMITH_TRACING=true 和 LANGSMITH_API_KEY 以启用")


def main():
    parser = argparse.ArgumentParser(
        description="企业级智能数据分析 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py init                         # 初始化数据库和向量库
  python main.py ask "各省销售额"              # 自然语言查询
  python main.py ask "各省销售额" --no-cache   # 跳过缓存
  python main.py eval                         # 运行评估
  python main.py dashboard                    # 启动大屏
  python main.py trace                        # 查看追踪状态
  python main.py query "SELECT * FROM users LIMIT 5"  # 原始 SQL
        """,
    )

    parser.add_argument("--debug", action="store_true", help="开启调试日志")

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # init
    subparsers.add_parser("init", help="初始化数据库和向量库")

    # query
    query_parser = subparsers.add_parser("query", help="执行原始 SQL")
    query_parser.add_argument("sql", type=str, help="SQL 语句")

    # ask
    ask_parser = subparsers.add_parser("ask", help="自然语言提问")
    ask_parser.add_argument("question", type=str, help="问题描述")
    ask_parser.add_argument("--no-cache", action="store_true", help="跳过缓存")

    # eval
    subparsers.add_parser("eval", help="运行自动化评估")

    # dashboard
    subparsers.add_parser("dashboard", help="启动 Streamlit 大屏")

    # trace
    subparsers.add_parser("trace", help="查看 LangSmith 追踪状态")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    setup_logging(args.debug)

    if args.command == "init":
        cmd_init()
    elif args.command == "query":
        cmd_query(args.sql)
    elif args.command == "ask":
        cmd_ask(args.question, args.no_cache)
    elif args.command == "eval":
        cmd_eval()
    elif args.command == "dashboard":
        cmd_dashboard()
    elif args.command == "trace":
        cmd_trace()


if __name__ == "__main__":
    main()
