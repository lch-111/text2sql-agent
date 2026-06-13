"""
==============================================================================
数据库模块 — 统一数据库管理器 (SQLite / PostgreSQL / MySQL)
==============================================================================
设计思路：
  1. 支持 SQLite / PostgreSQL / MySQL 三种数据库。
  2. 系统不自动创建或填充任何数据，所有数据来自：
     - 用户通过前端连接的外部数据库
     - 用户上传的 CSV / Excel 文件（自动建表）
  3. 提供统一的 `DatabaseManager` 封装所有数据库操作，
     支持运行时动态切换连接。
==============================================================================
"""

import json
import logging
import os
from typing import List, Tuple, Optional, Dict, Any
from pathlib import Path

import pandas as pd
from sqlalchemy import text as sa_text, inspect as sa_inspect

from core.config import CONFIG

logger = logging.getLogger("database")
_CONNECTION_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "connection.json")

class DatabaseManager:
    """
    统一数据库管理器，支持 SQLite / PostgreSQL / MySQL。

    设计思路：
    - 使用单例模式确保全局共用同一个连接
    - 所有查询返回 pandas DataFrame，便于上层分析和展示
    - 自动根据 DB_TYPE 环境变量选择数据库类型
    - 提供 DDL 和注释的提取方法，供 RAG 引擎使用
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.db_cfg = CONFIG.db
        self.db_type = self.db_cfg.db_type
        self._engine = None
        self._connection = None
        # 运行时自定义连接参数（通过 UI 设置，覆盖 env 配置）
        self._custom_connection_params = None
        # 尝试加载持久化的连接参数
        self._load_saved_connection()

    def _load_saved_connection(self):
        """从持久化文件加载上次保存的连接参数"""
        try:
            if os.path.exists(_CONNECTION_FILE):
                with open(_CONNECTION_FILE, "r") as f:
                    params = json.load(f)
                # 验证参数完整
                required = ["db_type", "host", "port", "database", "user", "password"]
                if all(k in params for k in required):
                    self._custom_connection_params = params
                    logger.info(f"[数据库] 已恢复上次的连接: {params.get('db_type')} @ {params.get('host')}:{params.get('port')}/{params.get('database')}")
        except Exception as e:
            logger.warning(f"[数据库] 加载持久化连接失败: {e}")

    def _save_connection_params(self):
        """持久化保存当前连接参数到文件"""
        if not self._custom_connection_params:
            return
        try:
            os.makedirs(os.path.dirname(_CONNECTION_FILE), exist_ok=True)
            with open(_CONNECTION_FILE, "w") as f:
                json.dump(self._custom_connection_params, f)
            logger.info("[数据库] 连接参数已持久化保存")
        except Exception as e:
            logger.warning(f"[数据库] 持久化连接保存失败: {e}")

    def _clear_saved_connection(self):
        """清除持久化的连接参数文件"""
        try:
            if os.path.exists(_CONNECTION_FILE):
                os.remove(_CONNECTION_FILE)
                logger.info("[数据库] 已清除持久化连接参数")
        except Exception as e:
            logger.warning(f"[数据库] 清除持久化连接失败: {e}")

    def _get_engine_url(self, overrides: dict = None) -> str:
        """
        根据配置或运行时覆盖参数生成 SQLAlchemy 引擎 URL。

        参数:
            overrides: 可选覆盖参数字典，包含 db_type/host/port/database/user/password
        """
        cfg = overrides or {}
        db_type = cfg.get("db_type", self.db_type)

        if db_type == "postgres":
            host = cfg.get("host") or self.db_cfg.pg_host
            port = cfg.get("port") or self.db_cfg.pg_port
            database = cfg.get("database") or self.db_cfg.pg_database
            user = cfg.get("user") or self.db_cfg.pg_user
            password = cfg.get("password", "") if "password" in cfg else self.db_cfg.pg_password
            return (
                f"postgresql://{user}:{password}"
                f"@{host}:{port}/{database}"
            )
        elif db_type == "mysql":
            host = cfg.get("host") or self.db_cfg.mysql_host
            port = cfg.get("port") or self.db_cfg.mysql_port
            database = cfg.get("database") or self.db_cfg.mysql_database
            user = cfg.get("user") or self.db_cfg.mysql_user
            password = cfg.get("password", "") if "password" in cfg else self.db_cfg.mysql_password
            return (
                f"mysql+pymysql://{user}:{password}"
                f"@{host}:{port}/{database}"
                "?charset=utf8mb4"
            )
        else:
            # SQLite
            db_path = Path(cfg.get("database") or self.db_cfg.db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{db_path.resolve()}"

    @property
    def engine(self):
        """懒加载 SQLAlchemy 引擎"""
        if self._engine is None:
            from sqlalchemy import create_engine
            url = self._get_engine_url(self._custom_connection_params)
            db_type = self._get_active_db_type()
            if db_type == "sqlite":
                self._engine = create_engine(url, pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=3600, connect_args={"check_same_thread": False})
            else:
                self._engine = create_engine(url, pool_size=5, max_overflow=10, pool_pre_ping=True, pool_recycle=3600, connect_args={"connect_timeout": 5})
        return self._engine

    @property
    def active_db_type(self) -> str:
        """获取当前活跃的数据库类型（优先返回运行时切换的，否则返回配置值）"""
        if self._custom_connection_params:
            return self._custom_connection_params.get("db_type", self.db_type)
        return self.db_type

    def _get_active_db_type(self) -> str:
        """Legacy internal accessor"""
        return self.active_db_type

    @property
    def conn(self):
        """获取原生数据库连接（兼容旧接口）"""
        if self._connection is None:
            self._connection = self.engine.connect()
        return self._connection

    # ========================================================================
    # 运行时连接切换
    # ========================================================================

    def switch_connection(self, db_type: str, host: str = None, port: int = None,
                          database: str = None, user: str = None, password: str = ""):
        """
        运行时切换到新的数据库连接。

        关闭现有连接，设置新的连接参数，下次访问 engine/conn 时自动重建。
        """
        self.close()
        self._custom_connection_params = {
            "db_type": db_type,
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
        }
        # 立即验证新连接
        try:
            _ = self.engine
            _ = self.conn
            self._save_connection_params()  # 持久化保存
            return True
        except Exception as e:
            # 连接失败，回滚
            self.close()
            self._custom_connection_params = None
            raise RuntimeError(f"数据库连接失败: {e}")

    def reset_connection(self):
        """重置为环境变量配置的默认连接"""
        self.close()
        self._custom_connection_params = None
        self._clear_saved_connection()

    def get_connection_info(self) -> dict:
        """获取当前连接信息"""
        if self._custom_connection_params:
            params = self._custom_connection_params
            return {
                "connected": self._engine is not None,
                "db_type": params.get("db_type", ""),
                "host": params.get("host", ""),
                "port": params.get("port"),
                "database": params.get("database", ""),
                "user": params.get("user", ""),
            }
        # 返回环境变量配置的信息
        if self.active_db_type == "mysql":
            return {
                "connected": self._engine is not None,
                "db_type": "mysql",
                "host": self.db_cfg.mysql_host,
                "port": self.db_cfg.mysql_port,
                "database": self.db_cfg.mysql_database,
                "user": self.db_cfg.mysql_user,
            }
        elif self.active_db_type == "postgres":
            return {
                "connected": self._engine is not None,
                "db_type": "postgres",
                "host": self.db_cfg.pg_host,
                "port": self.db_cfg.pg_port,
                "database": self.db_cfg.pg_database,
                "user": self.db_cfg.pg_user,
            }
        else:
            return {
                "connected": self._engine is not None,
                "db_type": "sqlite",
                "host": None,
                "port": None,
                "database": self.db_cfg.db_path,
                "user": None,
            }

    def get_table_names(self) -> list:
        """获取当前数据库中的所有表名"""
        try:
            inspector = sa_inspect(self.engine)
            return inspector.get_table_names()
        except Exception:
            return []

    @staticmethod
    def test_connection(db_type: str, host: str = None, port: int = None,
                        database: str = None, user: str = None, password: str = "",
                        timeout: int = 5) -> dict:
        """
        测试数据库连接是否可用（不修改当前实例状态）。

        返回:
            {"success": True, "version": "...", "latency_ms": 123}
            或 {"success": False, "message": "错误信息"}
        """
        import time
        from sqlalchemy import create_engine, text

        # 临时构造引擎 URL
        if db_type == "sqlite":
            db_path = database or ":memory:"
            url = f"sqlite:///{db_path}"
        elif db_type == "postgres":
            url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        else:
            url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"

        start = time.time()
        try:
            connect_args = {}
            if db_type != "sqlite":
                connect_args["connect_timeout"] = timeout
            engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            version = None
            try:
                with engine.connect() as c:
                    if db_type == "mysql":
                        result = c.execute(text("SELECT VERSION() AS v"))
                    elif db_type == "postgres":
                        result = c.execute(text("SELECT version() AS v"))
                    else:
                        result = c.execute(text("SELECT sqlite_version() AS v"))
                    version = result.fetchone()[0]
            except Exception:
                pass
            engine.dispose()
            latency = round((time.time() - start) * 1000, 1)
            return {"success": True, "version": version, "latency_ms": latency}
        except Exception as e:
            return {"success": False, "message": str(e)}

    # ========================================================================
    # 初始化（本系统不自动创建或填充数据，所有数据来自用户连接的外部数据库或上传文件）
    # ========================================================================

    def initialize(self):
        """占位：不自动创建或填充数据，由用户自行连接数据库或上传文件。"""
        return self

    # ========================================================================
    # 元数据提取
    # ========================================================================

    def get_table_ddl(self) -> Dict[str, str]:
        """
        获取所有表的完整 DDL 语句。

        返回: { table_name: create_table_sql }
        """
        tables = self.get_table_names()
        if not tables:
            return {}
        result = {}

        if self.active_db_type == "sqlite":
            # SQLite: query sqlite_master which already returns ALL tables
            cursor = self.conn.execute(
                sa_text("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            )
            for row in cursor.fetchall():
                result[row[0]] = row[1]
        elif self.active_db_type == "mysql":
            for table in tables:
                cursor = self.conn.execute(sa_text(f"SHOW CREATE TABLE {table}"))
                row = cursor.fetchone()
                if row:
                    result[table] = row[1]  # SHOW CREATE TABLE 的第二列
        else:
            # PostgreSQL
            for table in tables:
                cursor = self.conn.execute(
                    sa_text(f"SELECT pg_get_ddl('{table}'::regclass)")
                )
                row = cursor.fetchone()
                if row:
                    result[table] = row[0]

        return result

    def get_table_info(self) -> List[Dict[str, Any]]:
        """
        获取各表的详细元数据，包含字段名、类型、注释说明。
        注释说明基于字段命名约定和业务知识生成。

        返回示例:
        [
            {
                "table_name": "users",
                "columns": [
                    {"name": "user_id", "type": "INTEGER", "comment": "用户唯一标识"},
                    ...
                ],
                "primary_key": "user_id",
                "row_count": 500,
                "sample_rows": [...]
            },
            ...
        ]
        """
        # 内置字段注释映射
        column_comments = {
            "users": {
                "user_id": "用户唯一标识",
                "username": "用户名",
                "email": "电子邮箱",
                "age": "年龄",
                "gender": "性别（男/女）",
                "city": "所在城市",
                "province": "所在省份",
                "registration_date": "注册日期（ISO格式）",
                "vip_level": "会员等级：普通会员/白银会员/黄金会员/钻石会员",
                "is_active": "是否活跃（1=活跃, 0=非活跃）",
            },
            "products": {
                "product_id": "商品唯一标识",
                "product_name": "商品名称",
                "category": "商品类别：电子产品/服装鞋帽/食品饮料/家居用品/图书文具/运动户外",
                "price": "零售价（元）",
                "cost": "成本价（元）",
                "stock": "当前库存量",
                "description": "商品描述",
                "created_at": "上架日期",
            },
            "orders": {
                "order_id": "订单唯一标识",
                "user_id": "用户ID（关联users表）",
                "product_id": "商品ID（关联products表）",
                "quantity": "购买数量",
                "total_amount": "订单总金额（元）",
                "order_date": "下单日期（ISO格式）",
                "status": "订单状态：已完成/待发货/已取消/退款中",
                "payment_method": "支付方式：微信支付/支付宝/银行卡/货到付款",
            },
        }

        table_names = self.get_table_names()
        if not table_names:
            return []
        result = []

        # 使用 SQLAlchemy Inspector 获取跨数据库兼容的元数据
        inspector = sa_inspect(self.engine)

        for table in table_names:
            # 获取字段信息（跨数据库兼容）
            try:
                columns_meta = inspector.get_columns(table)
            except Exception:
                continue
            pk_constraint = inspector.get_pk_constraint(table)
            pk_cols = pk_constraint.get("constrained_columns", [])
            primary_key = pk_cols[0] if pk_cols else None

            columns = []
            for col in columns_meta:
                col_name = col["name"]
                col_type = str(col["type"])
                col_comment = column_comments.get(table, {}).get(
                    col_name, f"{table}表的{col_name}字段"
                )
                columns.append({
                    "name": col_name,
                    "type": col_type,
                    "comment": col_comment,
                    "nullable": col.get("nullable", True),
                    "default": col.get("default"),
                })

            # 获取行数
            try:
                cursor = self.conn.execute(sa_text(f"SELECT COUNT(*) FROM \"{table}\""))
                row_count = cursor.scalar()
            except Exception:
                row_count = 0

            # 获取示例行
            try:
                df = self.query(f"SELECT * FROM \"{table}\" LIMIT 3")
                sample_rows = df.to_dict(orient="records") if not df.empty else []
            except Exception:
                sample_rows = []

            # 获取不同值统计（对类别型字段）
            distinct_stats = {}
            try:
                for field in columns_meta:
                    fname = field["name"]
                    ftype = str(field["type"]).lower()
                    if any(t in ftype for t in ["char", "text", "varchar"]):
                        cursor = self.conn.execute(
                            sa_text(f"SELECT \"{fname}\", COUNT(*) as cnt FROM \"{table}\" GROUP BY \"{fname}\" ORDER BY cnt DESC LIMIT 20")
                        )
                        distinct_stats[fname] = {
                            row[0]: row[1] for row in cursor.fetchall()
                        }
            except Exception:
                pass

            result.append({
                "table_name": table,
                "columns": columns,
                "primary_key": primary_key,
                "row_count": row_count,
                "sample_rows": sample_rows,
                "distinct_stats": distinct_stats,
            })

        return result

    # ========================================================================
    # 查询接口
    # ========================================================================

    def query(self, sql: str, params: Tuple = ()) -> pd.DataFrame:
        """
        执行 SQL 查询，返回 DataFrame。

        参数:
            sql: SQL 查询语句
            params: 参数化查询的参数元组

        返回:
            pandas.DataFrame 格式的查询结果
        """
        try:
            df = pd.read_sql_query(sql, self.conn, params=params)
            return df
        except Exception as e:
            raise RuntimeError(f"SQL 执行失败: {e}\nSQL: {sql}") from e

    def query_readonly(self, sql: str, params: Tuple = ()) -> pd.DataFrame:
        """
        在只读事务中执行 SQL 查询，防止意外修改数据。

        对 SQLite 设置 PRAGMA query_only = ON；
        对 MySQL 设置 SET TRANSACTION READ ONLY。

        参数:
            sql: SQL 查询语句
            params: 参数化查询的参数元组

        返回:
            pandas.DataFrame 格式的查询结果
        """
        try:
            if self.active_db_type == "sqlite":
                self.conn.execute(sa_text("PRAGMA query_only = ON;"))
                self.conn.commit()
            elif self.active_db_type == "mysql":
                # MySQL 下不设置只读事务（已有事务中会报错），
                # 安全由上游 SQLValidator 保证（只允许 SELECT）
                pass

            df = pd.read_sql_query(sql, self.conn, params=params)
            return df
        except Exception as e:
            raise RuntimeError(f"SQL 执行失败: {e}\nSQL: {sql}") from e
        finally:
            if self.active_db_type == "sqlite":
                try:
                    self.conn.execute(sa_text("PRAGMA query_only = OFF;"))
                    self.conn.commit()
                except Exception:
                    pass

    def execute(self, sql: str, params: Tuple = ()) -> Optional[int]:
        """
        执行 INSERT/UPDATE/DELETE 操作。

        返回受影响的行数。
        """
        cursor = self.conn.execute(sa_text(sql), params)
        self.conn.commit()
        return cursor.rowcount

    def close(self):
        """关闭数据库连接"""
        if self._connection:
            self._connection.close()
            self._connection = None
        if self._engine:
            self._engine.dispose()
            self._engine = None

    def __del__(self):
        self.close()


# ============================================================================
# 便捷函数
# ============================================================================

def get_db() -> DatabaseManager:
    """获取数据库管理器单例"""
    return DatabaseManager()


