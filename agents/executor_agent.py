"""
==============================================================================
Executor Agent — SQL 安全执行器
==============================================================================
设计思路：
  Executor Agent 是 SQL 执行前的最后一道关口。
  它在执行前强制调用 SQLGuard 做物理安全拦截，
  确保任何危险操作在代码层被直接阻断。

  同时集成了 SQL 优化器（可选），在执行前输出优化建议。
==============================================================================
"""

import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd

from core.database import get_db
from harness.sql_guard import SQLGuard, SQLSafetyError

logger = logging.getLogger("executor_agent")


class ExecutorAgent:
    """
    SQL 安全执行器。

    职责：
    1. 执行前调用 SQLGuard.validate(sql) 做物理拦截
    2. 通过 DatabaseManager 执行查询（只读事务）
    3. 集成 SQL 优化器（可选）
    4. 统一异常处理与日志

    用法:
        executor = ExecutorAgent()
        df = executor.execute("SELECT * FROM orders")
    """

    def __init__(self, sql_guard: Optional[SQLGuard] = None):
        """
        初始化 Executor Agent。

        参数:
            sql_guard: SQLGuard 安全拦截器实例（默认创建新实例）
        """
        self.guard = sql_guard or SQLGuard()
        self.db = get_db()
        self.sql_optimizer = None

        # 尝试加载 SQL 优化器
        try:
            from services.sql_optimizer import SQLOptimizer
            self.sql_optimizer = SQLOptimizer(
                enabled=True
            )
            logger.info("[ExecutorAgent] SQL 优化器已加载")
        except Exception:
            logger.debug("[ExecutorAgent] SQL 优化器未加载")

        logger.info("[ExecutorAgent] 初始化完成")

    def execute(
        self, sql: str, analyze: bool = True
    ) -> pd.DataFrame:
        """
        安全执行 SQL 查询。

        完整流程：
        1. SQLGuard 物理安全拦截
        2. 可选：SQL 优化分析（仅日志输出）
        3. 通过只读事务执行查询
        4. 返回 DataFrame

        参数:
            sql: 待执行的 SQL 查询语句
            analyze: 是否执行 SQL 优化分析（默认 True）

        返回:
            pandas.DataFrame 格式的查询结果

        异常:
            SQLSafetyError: SQL 被安全拦截
            RuntimeError: 数据库执行失败
        """
        if not sql or not sql.strip():
            raise ValueError("SQL 语句为空")

        logger.info(f"[ExecutorAgent] 执行 SQL:\n{sql[:200]}...")

        # ---- Step 1: 物理安全拦截 ----
        self.guard.validate(sql)
        logger.info("[ExecutorAgent] SQL 安全校验通过")

        # ---- Step 2: SQL 优化分析（可选） ----
        if analyze and self.sql_optimizer is not None:
            try:
                opt_result = self.sql_optimizer.analyze(sql)
                if opt_result.get("suggestions"):
                    for s in opt_result["suggestions"]:
                        logger.info(
                            f"[ExecutorAgent] 优化建议 "
                            f"[{s['severity']}]: {s['message']}"
                        )
            except Exception as e:
                logger.debug(f"[ExecutorAgent] 优化分析异常: {e}")

        # ---- Step 3: 执行查询 ----
        try:
            df = self.db.query_readonly(sql)
            logger.info(
                f"[ExecutorAgent] 执行成功，返回 {len(df)} 行"
            )
            return df
        except Exception as e:
            error_msg = f"SQL 执行失败: {e}"
            logger.error(f"[ExecutorAgent] {error_msg}")
            raise RuntimeError(error_msg) from e

    def execute_with_result(
        self, sql: str, analyze: bool = True
    ) -> Dict:
        """
        执行 SQL 并返回结构化结果。

        参数:
            sql: SQL 查询语句
            analyze: 是否执行 SQL 优化分析

        返回:
            {
                "result": List[Dict] | None,
                "columns": List[str] | None,
                "error": str | None,
            }
        """
        try:
            df = self.execute(sql, analyze=analyze)
            return {
                "result": df.to_dict(orient="records") if not df.empty else [],
                "columns": df.columns.tolist() if not df.empty else [],
                "error": None,
                "row_count": len(df),
            }
        except SQLSafetyError as e:
            logger.critical(f"[ExecutorAgent] 安全拦截: {e.message}")
            return {
                "result": None,
                "columns": None,
                "error": f"SQL 被安全拦截: {e.message}",
                "row_count": 0,
            }
        except Exception as e:
            logger.error(f"[ExecutorAgent] 执行异常: {e}")
            return {
                "result": None,
                "columns": None,
                "error": str(e),
                "row_count": 0,
            }
