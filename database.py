"""
==============================================================================
数据库模块 — 模拟电商数仓 (SQLite / PostgreSQL / MySQL)
==============================================================================
设计思路：
  1. 默认使用 SQLite 作为原型数据库，方便本地开发和演示。
  2. 可通过 DB_TYPE 环境变量切换为 PostgreSQL 或 MySQL。
  3. 包含 users / orders / products 三张标准电商事实表与维度表。
  3. 预设了 1000+ 条模拟数据，覆盖多种复杂业务场景：
     - 连续 N 天购买用户
     - 客单价分布
     - 区域销售对比
     - 会员等级贡献度
  4. 提供统一的 `DatabaseManager` 封装所有数据库操作，
     便于后续切换为 PostgreSQL/MySQL 等生产级数据库。
==============================================================================
"""

import json
import logging
import random
import datetime
import os
from typing import List, Tuple, Optional, Dict, Any
from pathlib import Path

import pandas as pd
from sqlalchemy import text as sa_text, inspect as sa_inspect

from config import CONFIG

logger = logging.getLogger("database")

# ============================================================================
# 模拟数据生成器
# ============================================================================

# ---- 用户维度数据 ----
_CITIES = [
    ("北京市", "北京"), ("上海市", "上海"), ("广州市", "广东"), ("深圳市", "广东"),
    ("杭州市", "浙江"), ("南京市", "江苏"), ("成都市", "四川"), ("武汉市", "湖北"),
    ("重庆市", "重庆"), ("西安市", "陕西"), ("长沙市", "湖南"), ("郑州市", "河南"),
    ("青岛市", "山东"), ("苏州市", "江苏"), ("昆明市", "云南"), ("合肥市", "安徽"),
    ("厦门市", "福建"), ("天津市", "天津"), ("宁波市", "浙江"), ("福州市", "福建"),
    ("哈尔滨", "黑龙江"), ("长春市", "吉林"), ("沈阳市", "辽宁"), ("大连市", "辽宁"),
]

_CATEGORIES = ["电子产品", "服装鞋帽", "食品饮料", "家居用品", "图书文具", "运动户外"]
_VIP_LEVELS = ["普通会员", "白银会员", "黄金会员", "钻石会员"]
_PAYMENT_METHODS = ["微信支付", "支付宝", "银行卡", "货到付款"]
_ORDER_STATUSES = ["已完成", "已完成", "已完成", "已完成", "待发货", "已取消", "退款中"]
# 对应的权重分布（必须与 _ORDER_STATUSES 长度一致）
_ORDER_STATUS_WEIGHTS = [30, 25, 20, 10, 10, 3, 2]

_PRODUCT_NAMES = {
    "电子产品": [
        "无线蓝牙耳机", "智能手表Pro", "便携充电宝", "4K高清投影仪",
        "机械键盘K1", "无线游戏鼠标", "USB-C扩展坞", "智能体脂秤",
        "降噪耳机Pro", "平板电脑支架",
    ],
    "服装鞋帽": [
        "纯棉T恤", "修身牛仔裤", "轻薄羽绒服", "商务休闲皮鞋",
        "运动跑鞋Air", "速干运动短裤", "羊绒围巾", "棒球帽经典款",
        "真皮腰带", "休闲帆布鞋",
    ],
    "食品饮料": [
        "有机绿茶礼盒", "阿拉比卡咖啡豆", "坚果混合装", "黑巧克力72%",
        "蜂蜜柚子茶", "进口红酒珍藏", "每日坚果30包", "即食燕麦片",
        "新疆红枣500g", "龙井明前茶",
    ],
    "家居用品": [
        "记忆棉枕头", "乳胶床垫保护罩", "智能台灯Lite", "扫地机器人",
        "不锈钢保温杯", "日式餐具套装", "香薰加湿器", "电动牙刷H1",
        "纯棉四件套", "收纳箱三件套",
    ],
    "图书文具": [
        "Python深度学习", "数据仓库实战", "SQL必知必会", "算法导论",
        "手帐本礼盒", "钢笔F尖", "彩色马克笔12色", "Kindle保护壳",
        "时间管理手帐", "极简项目管理",
    ],
    "运动户外": [
        "瑜伽垫加厚", "可调节哑铃套装", "跑步腰包", "户外登山杖",
        "速干运动T恤", "运动水壶750ml", "跳绳计数款", "护膝运动髌骨带",
        "露营帐篷2人", "折叠露营椅",
    ],
}


def _random_date(start: datetime.date, end: datetime.date) -> datetime.date:
    """在 [start, end] 之间生成随机日期"""
    delta = end - start
    offset = random.randint(0, delta.days)
    return start + datetime.timedelta(days=offset)


def _generate_users(n: int = 500) -> List[Tuple]:
    """生成 n 条用户模拟数据"""
    users = []
    start_date = datetime.date(2023, 1, 1)
    end_date = datetime.date(2024, 12, 31)

    for i in range(1, n + 1):
        city, province = random.choice(_CITIES)
        reg_days = (end_date - start_date).days
        reg_offset = random.randint(0, reg_days)
        reg_date = start_date + datetime.timedelta(days=reg_offset)

        # VIP 等级：注册超过 1 年的用户更可能为高等级会员
        days_since_reg = (end_date - reg_date).days
        if days_since_reg > 365:
            vip_weights = [0.20, 0.35, 0.30, 0.15]  # 老用户高等级概率高
        elif days_since_reg > 180:
            vip_weights = [0.40, 0.35, 0.20, 0.05]
        else:
            vip_weights = [0.65, 0.25, 0.08, 0.02]

        users.append((
            i,
            f"user_{i:04d}",
            f"user{i:04d}@example.com",
            random.randint(18, 65),
            random.choice(["男", "女"]),
            city,
            province,
            reg_date.isoformat(),
            random.choices(_VIP_LEVELS, weights=vip_weights, k=1)[0],
            1 if random.random() < 0.85 else 0,  # 85% 活跃用户
        ))
    return users


def _generate_products(n_per_category: int = 10) -> List[Tuple]:
    """为每个品类生成 n 条商品数据"""
    products = []
    pid = 1
    for category, names in _PRODUCT_NAMES.items():
        for name in names:
            base_price = round(random.uniform(19.9, 999.0), 2)
            cost = round(base_price * random.uniform(0.3, 0.7), 2)
            products.append((
                pid,
                name,
                category,
                base_price,
                cost,
                random.randint(10, 2000),
                f"{category}类商品：{name}",
                _random_date(datetime.date(2023, 1, 1), datetime.date(2024, 6, 30)).isoformat(),
            ))
            pid += 1
    return products


def _generate_orders(
    users: List[Tuple], products: List[Tuple], n: int = 1000
) -> List[Tuple]:
    """
    生成 n 条订单模拟数据。

    复杂场景预设：
    - 部分高频用户在短周期内密集下单（用于"连续购买"场景分析）
    - 存在大额订单（客单价 > 2000）用于异常检测
    - 部分用户退单/取消订单，用于流失分析
    """
    orders = []
    user_ids = [u[0] for u in users]
    product_ids = [p[0] for p in products]
    product_price_map = {p[0]: p[3] for p in products}

    start_date = datetime.date(2023, 1, 1)
    end_date = datetime.date(2024, 12, 31)

    # ---- 场景 1：高频用户（前 5%）在特定时间段密集下单 ----
    high_freq_users = random.sample(user_ids, max(1, len(user_ids) // 20))
    high_freq_periods = {}  # user_id -> (start, end)
    for uid in high_freq_users:
        period_start = _random_date(
            datetime.date(2023, 6, 1), datetime.date(2024, 6, 1)
        )
        period_end = period_start + datetime.timedelta(days=random.randint(10, 20))
        high_freq_periods[uid] = (period_start, period_end)

    # ---- 场景 2：部分用户集中在特定月份大量下单（双11/618场景） ----
    promotion_users = random.sample(user_ids, max(1, len(user_ids) // 10))

    for i in range(1, n + 1):
        user_id = random.choice(user_ids)
        product_id = random.choice(product_ids)
        quantity = random.choices([1, 1, 1, 2, 2, 3, 5], weights=[30, 20, 20, 15, 8, 5, 2])[0]
        unit_price = product_price_map[product_id]
        total_amount = round(unit_price * quantity, 2)

        # ---- 日期生成逻辑 ----
        if user_id in high_freq_periods:
            # 高频用户在指定周期内密集下单
            period_start, period_end = high_freq_periods[user_id]
            order_date = _random_date(period_start, period_end)
        elif user_id in promotion_users:
            # 促销用户集中在 6月 或 11月
            month = random.choice([6, 11])
            year = random.choice([2023, 2024])
            promo_start = datetime.date(year, month, 1)
            promo_end = datetime.date(year, month, 28)
            order_date = _random_date(promo_start, promo_end)
        else:
            order_date = _random_date(start_date, end_date)

        status = random.choices(_ORDER_STATUSES, weights=_ORDER_STATUS_WEIGHTS, k=1)[0]
        payment = random.choice(_PAYMENT_METHODS)

        orders.append((
            i,
            user_id,
            product_id,
            quantity,
            total_amount,
            order_date.isoformat(),
            status,
            payment,
        ))

    # 按日期排序
    orders.sort(key=lambda x: x[5])
    # 重置 order_id
    orders = [(i,) + o[1:] for i, o in enumerate(orders, 1)]
    return orders


# ============================================================================
# 数据库管理器
# ============================================================================

# 持久化连接参数文件路径
_CONNECTION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "connection.json")

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
                self._engine = create_engine(url, connect_args={"check_same_thread": False})
            else:
                self._engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
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
    # 建表 & 初始化
    # ========================================================================

    def create_tables(self):
        """创建三张核心数据表（自动适配 SQLite / MySQL / PostgreSQL）"""
        # 根据数据库类型选择 DDL
        if self.active_db_type == "mysql":
            ddl_statements = """
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER AUTO_INCREMENT PRIMARY KEY,
                username        VARCHAR(255) NOT NULL,
                email           VARCHAR(255),
                age             INTEGER,
                gender          VARCHAR(50),
                city            VARCHAR(100),
                province        VARCHAR(100),
                registration_date VARCHAR(50),
                vip_level       VARCHAR(50) DEFAULT '普通会员',
                is_active       INTEGER DEFAULT 1
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

            CREATE TABLE IF NOT EXISTS products (
                product_id      INTEGER AUTO_INCREMENT PRIMARY KEY,
                product_name    VARCHAR(255) NOT NULL,
                category        VARCHAR(100),
                price           DOUBLE,
                cost            DOUBLE,
                stock           INTEGER DEFAULT 0,
                description     TEXT,
                created_at      VARCHAR(50)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

            CREATE TABLE IF NOT EXISTS orders (
                order_id        INTEGER AUTO_INCREMENT PRIMARY KEY,
                user_id         INTEGER NOT NULL,
                product_id      INTEGER NOT NULL,
                quantity        INTEGER DEFAULT 1,
                total_amount    DOUBLE,
                order_date      VARCHAR(50),
                status          VARCHAR(50) DEFAULT '已完成',
                payment_method  VARCHAR(50),
                FOREIGN KEY (user_id)    REFERENCES users(user_id),
                FOREIGN KEY (product_id) REFERENCES products(product_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        elif self.active_db_type == "postgres":
            ddl_statements = """
            CREATE TABLE IF NOT EXISTS users (
                user_id         SERIAL PRIMARY KEY,
                username        TEXT NOT NULL,
                email           TEXT,
                age             INTEGER,
                gender          TEXT,
                city            TEXT,
                province        TEXT,
                registration_date TEXT,
                vip_level       TEXT DEFAULT '普通会员',
                is_active       INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS products (
                product_id      SERIAL PRIMARY KEY,
                product_name    TEXT NOT NULL,
                category        TEXT,
                price           DOUBLE PRECISION,
                cost            DOUBLE PRECISION,
                stock           INTEGER DEFAULT 0,
                description     TEXT,
                created_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                order_id        SERIAL PRIMARY KEY,
                user_id         INTEGER NOT NULL,
                product_id      INTEGER NOT NULL,
                quantity        INTEGER DEFAULT 1,
                total_amount    DOUBLE PRECISION,
                order_date      TEXT,
                status          TEXT DEFAULT '已完成',
                payment_method  TEXT,
                FOREIGN KEY (user_id)    REFERENCES users(user_id),
                FOREIGN KEY (product_id) REFERENCES products(product_id)
            );
            """
        else:
            ddl_statements = """
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT NOT NULL,
                email           TEXT,
                age             INTEGER,
                gender          TEXT,
                city            TEXT,
                province        TEXT,
                registration_date TEXT,
                vip_level       TEXT DEFAULT '普通会员',
                is_active       INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS products (
                product_id      INTEGER PRIMARY KEY,
                product_name    TEXT NOT NULL,
                category        TEXT,
                price           REAL,
                cost            REAL,
                stock           INTEGER DEFAULT 0,
                description     TEXT,
                created_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                order_id        INTEGER PRIMARY KEY,
                user_id         INTEGER NOT NULL,
                product_id      INTEGER NOT NULL,
                quantity        INTEGER DEFAULT 1,
                total_amount    REAL,
                order_date      TEXT,
                status          TEXT DEFAULT '已完成',
                payment_method  TEXT,
                FOREIGN KEY (user_id)    REFERENCES users(user_id),
                FOREIGN KEY (product_id) REFERENCES products(product_id)
            );
            """

        # 逐条执行 DDL
        for statement in ddl_statements.split(';'):
            stmt = statement.strip()
            if stmt and not stmt.startswith('--'):
                self.conn.execute(sa_text(stmt + ';'))

        # 创建索引（不同数据库的 IF NOT EXISTS 支持情况不同）
        index_sqls = [
            "CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(order_date)",
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
            "CREATE INDEX IF NOT EXISTS idx_orders_amount ON orders(total_amount)",
            "CREATE INDEX IF NOT EXISTS idx_users_province ON users(province)",
            "CREATE INDEX IF NOT EXISTS idx_users_vip ON users(vip_level)",
            "CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)",
        ]
        for idx_sql in index_sqls:
            try:
                self.conn.execute(sa_text(idx_sql))
            except Exception:
                # MySQL < 8.0 不支持 IF NOT EXISTS，忽略即可
                pass

        self.conn.commit()

    def seed_data(self):
        """
        初始化模拟数据。

        每次调用会先检查是否已有数据，避免重复插入。
        """
        # 用 SQLAlchemy text() 包裹查询
        cursor = self.conn.execute(sa_text("SELECT COUNT(*) FROM users"))
        if cursor.scalar() > 0:
            print("[数据库] 数据已存在，跳过播种。")
            return

        print("[数据库] 开始生成模拟数据...")

        users = _generate_users(500)
        products = _generate_products(10)  # 6 categories × 10 = 60 products
        orders = _generate_orders(users, products, 1200)

        # 使用 pandas to_sql 实现跨数据库批量插入
        user_cols = [
            "user_id", "username", "email", "age", "gender",
            "city", "province", "registration_date", "vip_level", "is_active",
        ]
        product_cols = [
            "product_id", "product_name", "category", "price",
            "cost", "stock", "description", "created_at",
        ]
        order_cols = [
            "order_id", "user_id", "product_id", "quantity",
            "total_amount", "order_date", "status", "payment_method",
        ]

        pd.DataFrame(users, columns=user_cols).to_sql(
            "users", self.conn, if_exists="append", index=False
        )
        pd.DataFrame(products, columns=product_cols).to_sql(
            "products", self.conn, if_exists="append", index=False
        )
        pd.DataFrame(orders, columns=order_cols).to_sql(
            "orders", self.conn, if_exists="append", index=False
        )
        self.conn.commit()

        print(f"[数据库] 数据生成完成！")
        print(f"  - 用户: {len(users)} 条")
        print(f"  - 商品: {len(products)} 条")
        print(f"  - 订单: {len(orders)} 条")

    def initialize(self):
        """一键初始化数据库：建表 + 播种"""
        self.create_tables()
        self.seed_data()
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


def preview_data():
    """快速预览各表数据"""
    db = get_db()
    for table in ["users", "products", "orders"]:
        print(f"\n{'='*60}")
        print(f"  表: {table}")
        print(f"{'='*60}")
        df = db.query(f"SELECT * FROM {table} LIMIT 5")
        print(df.to_string(index=False))


# ============================================================================
# 独立测试入口
# ============================================================================
if __name__ == "__main__":
    db = get_db()
    db.initialize()
    preview_data()

    # 打印元数据信息
    print("\n\n=== 表 DDL ===")
    for name, ddl in db.get_table_ddl().items():
        print(f"\n-- {name} --\n{ddl}")

    print("\n\n=== 字段注释 ===")
    for info in db.get_table_info():
        print(f"\n表: {info['table_name']} (共 {info['row_count']} 行)")
        for col in info["columns"]:
            print(f"  - {col['name']} ({col['type']}): {col['comment']}")
