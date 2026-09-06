"""
==============================================================================
动态评估数据集构造器 — 基于当前连接库自动生成评估用例 【核心亮点】
==============================================================================
设计思路：
  传统评估依赖硬编码 Golden Dataset（针对某一张业务表的固定用例），
  当用户连接任意数据库时，标准 SQL 会引用不存在的表，评估分数必然失真为 0。

  本模块在评估启动时动态读取「当前连接库」的真实表/列/样例值，按 7 类
  模板启发式自动生成用例（自然语言问题 + 标准 SQL 答案），并逐条自校验
  （标准 SQL 必须在库上真实执行通过才进入数据集）。

  因此：任意连接的库（MySQL / SQLite / PostgreSQL）都能跑出有意义、可执行
  的评估，不依赖任何特定表结构；表/列不足时少出题并记录跳过原因。

  支持的三类 SQL 方言：
    - mysql    标识符用反引号，日期函数 DATE_FORMAT / YEAR
    - sqlite   标识符用双引号，日期函数 strftime
    - postgres 标识符用双引号，日期函数 to_char / EXTRACT（统一 ::date 转型）

对外唯一入口：
    build_eval_dataset(db=None, max_cases=24) -> EvalSnapshot
==============================================================================
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Iterator

from sqlalchemy import inspect as sa_inspect

from core.config import CONFIG
from core.database import get_db

logger = logging.getLogger("eval_dataset_builder")

# ============================================================================
# 常量定义
# ============================================================================

# 7 个评估分类（与前端报告/历史数据集保持一致，绝不新增分类命名）
CATEGORY_ORDER = [
    "基础聚合",
    "多表关联",
    "时间分析",
    "条件过滤",
    "高级分析",
    "排序分析",
    "复杂分析",
]

# 护栏：避免在超大真实库上 introspection/采样卡死
MAX_PROFILE_TABLES = 30       # 最多刻画 30 张表
MAX_PROFILE_COLS = 40         # 每张表最多刻画 40 列
MAX_DATE_SCAN_ROWS = 50000    # 日期文本列最多回读行数（Python 侧解析年份/月份）
LOW_CARD_LIMIT = 60           # distinct 值 ≤ 该值视为低基维（可安全做 GROUP BY / WHERE 样例）

# 语义角色关键词（英文按 token 整词匹配，中文按子串匹配，均对列名与注释生效）
MEASURE_EN = {
    "amount", "price", "money", "revenue", "sales", "quantity", "qty",
    "stock", "cost", "profit", "fee", "total", "sum", "value", "score",
    "weight", "income", "expense", "balance", "margin",
}
MEASURE_ZH = {
    "金额", "价格", "单价", "总额", "销量", "数量", "库存", "成本", "利润",
    "费用", "收入", "支出", "余额", "得分", "评分", "总额", "值",
}
DIM_EN = {
    "category", "type", "status", "city", "province", "region", "area",
    "district", "level", "channel", "gender", "state", "kind", "group",
    "class", "location", "country",
}
DIM_ZH = {
    "类别", "分类", "类型", "状态", "城市", "省份", "地区", "区域", "省",
    "等级", "渠道", "性别", "种类", "品牌",
}
ID_EN = {"id", "code", "number", "no", "sn", "key", "uuid"}
ID_ZH = {"编号", "编码", "代码", "序号", "号", "码"}
LABEL_EN = {"name", "title", "label", "product", "desc"}
LABEL_ZH = {"名称", "标题", "名字", "商品", "描述"}
DATE_EN = {"date", "time", "year", "month", "day", "datetime", "timestamp"}
DATE_ZH = {"日期", "时间", "年月", "年", "月", "日", "下单", "创建"}


# ============================================================================
# 数据画像结构
# ============================================================================

@dataclass
class ColProfile:
    """单列画像：类型归一 + 语义角色 + 惰性统计值"""
    name: str                       # 原始列名
    raw_type: str                   # DB 返回的原始类型字符串
    kind: str                       # int/float/text/date/bool/other
    comment: str                    # 列注释（尽力而为，可为空）
    is_pk: bool                     # 是否主键
    role: str = ""                  # measure/dim_lowcard/date/id/code/label/boolflag
    # 惰性统计（命中角色后才查询）
    distinct_top: List[str] = field(default_factory=list)   # 文本列高频 TOP 值
    distinct_count: Optional[int] = None                    # 文本列 distinct 值总数
    num_min: Optional[float] = None                         # 数值列 MIN
    num_max: Optional[float] = None                         # 数值列 MAX
    num_avg: Optional[float] = None                         # 数值列 AVG


@dataclass
class TableProfile:
    """单表画像"""
    name: str
    row_count: int = 0
    columns: List[ColProfile] = field(default_factory=list)
    pk_columns: List[str] = field(default_factory=list)


@dataclass
class JoinPair:
    """可执行 JOIN 对（子表.child_key = 父表.parent_key）"""
    child_table: str
    child_key: str
    parent_table: str
    parent_key: str


@dataclass
class EvalSnapshot:
    """动态评估数据集快照"""
    cases: List[Dict]               # [{id, category, question, standard_sql, description}]
    skips: List[Dict]               # 每类不足/校验失败的原因记录
    meta: Dict                      # db_type / tables / 每类命中数 等元信息


# ============================================================================
# 方言助手
# ============================================================================

def qid(db_type: str, name: str) -> str:
    """按方言给标识符加引号（mysql 反引号，其余双引号）"""
    if db_type == "mysql":
        return "`%s`" % name
    return '"%s"' % name


def sq(value: Any) -> str:
    """字符串值安全转义（单引号翻倍）后作为 SQL 字面量"""
    return "'%s'" % str(value).replace("'", "''")


def month_expr(db_type: str, col_expr: str) -> str:
    """
    把日期列转成「月份整数 1..12」的方言表达式。

    设计说明：
      1. 单年锚定：时间分析模板都会带 year_expr = 具体年份 的过滤，
         故年内月份 1..12 唯一，整数即可无歧义地标识各月。
      2. 对齐生成器口径：实测 agent 对"按月统计"倾向输出整数月份
         （CAST(...) AS month），若标准答案返回 '2019-01' 字符串会造成
         格式不匹配的伪负例，故统一为整数。
      3. MySQL 分支刻意不用 DATE_FORMAT 的 '%Y-%m'：本项目 db.query 走
         pymysql，即使不带参数也会 `query % args` 格式化，SQL 中的裸 % 会抛
         "not enough arguments for format string"。MONTH() 完全避开 %。
    """
    if db_type == "mysql":
        return "MONTH(%s)" % col_expr
    if db_type == "postgres":
        return "EXTRACT(MONTH FROM %s::date)" % col_expr
    return "CAST(strftime('%%m', %s) AS INTEGER)" % col_expr


def year_expr(db_type: str, col_expr: str) -> str:
    """把日期列转成「年份整数值」的方言表达式"""
    if db_type == "mysql":
        return "YEAR(%s)" % col_expr
    if db_type == "postgres":
        return "EXTRACT(YEAR FROM %s::date)" % col_expr
    return "CAST(strftime('%%Y', %s) AS INTEGER)" % col_expr


def _num_literal(v: float) -> str:
    """数值字面量（去尾零、避免科学计数法）"""
    if float(v).is_integer():
        return str(int(v))
    return format(float(v), ".4f").rstrip("0").rstrip(".")


# ============================================================================
# 类型归一与角色启发式
# ============================================================================

def _normalize_kind(raw_type: str) -> str:
    """把任意数据库类型字符串归一为 int/float/text/date/bool/other"""
    t = (raw_type or "").lower()
    # 顺序敏感：先判日期/布尔，再判整型/浮点/文本
    if any(k in t for k in ("date", "time", "timestamp", "year", "datetime")):
        return "date"
    if any(k in t for k in ("bool", "boolean", "bit")):
        return "bool"
    if any(k in t for k in ("int", "integer", "bigint", "smallint", "mediumint", "tinyint")):
        return "int"
    if any(k in t for k in ("float", "double", "decimal", "numeric", "real")):
        return "float"
    if any(k in t for k in ("char", "text", "enum", "clob", "json", "set", "string", "varchar")):
        return "text"
    return "other"


def _split_tokens(name: str) -> set:
    """把英文列名切分为小写词（下划线/驼峰边界切分）"""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return {w for w in re.split(r"[^a-zA-Z0-9]+", name.lower()) if w}


def _hits_measure(name: str, comment: str) -> bool:
    """命中「数值度量」语义：英文整词 or 中文子串"""
    if name and any(k in name.lower() for k in MEASURE_ZH):
        return True
    if comment and any(k in comment.lower() for k in MEASURE_ZH):
        return True
    toks = _split_tokens(name) | _split_tokens(comment)
    return bool(toks & MEASURE_EN)


def _hits_dim(name: str, comment: str) -> bool:
    """命中「低基维/类别」语义"""
    if name and any(k in name.lower() for k in DIM_ZH):
        return True
    if comment and any(k in comment.lower() for k in DIM_ZH):
        return True
    toks = _split_tokens(name) | _split_tokens(comment)
    return bool(toks & DIM_EN)


def _hits_id(name: str, comment: str) -> bool:
    """命中「ID/编码」语义"""
    if name and any(k in name.lower() for k in ID_ZH):
        return True
    toks = _split_tokens(name) | _split_tokens(comment)
    return bool(toks & ID_EN)


def _hits_label(name: str, comment: str) -> bool:
    """命中「名称/标签」语义"""
    if name and any(k in name.lower() for k in LABEL_ZH):
        return True
    toks = _split_tokens(name) | _split_tokens(comment)
    return bool(toks & LABEL_EN)


def _hits_date_name(name: str) -> bool:
    """命中「日期」语义的列名"""
    if name and any(k in name.lower() for k in DATE_ZH):
        return True
    return bool(_split_tokens(name) & DATE_EN)


_DATE_RE = re.compile(r"^\s*(\d{4})[-/年.](\d{1,2})(?:[-/月.](\d{1,2}))?")


# ============================================================================
# Schema 快照 / 统计采样（全部只读 SELECT，直连当前 engine，不走缓存）
# ============================================================================

def _safe_query(db, sql: str) -> Optional[Any]:
    """执行只读查询，失败返回 None（供自校验与采样统一兜底）"""
    try:
        return db.query(sql)
    except Exception as e:  # noqa: BLE001 — 采样失败不应中断构造
        logger.debug("builder 查询失败: %s | %s", str(e)[:120], sql[:160])
        return None


def _col_stats(db, tbl: TableProfile, col: ColProfile, db_type: str) -> None:
    """
    按列类型惰性拉取统计：
      - 数值列：MIN / MAX / AVG
      - 文本列：高频 TOP 值 + COUNT(DISTINCT)
      - （日期列另由 _scan_date_column 单独处理）
    """
    q_col, q_tbl = qid(db_type, col.name), qid(db_type, tbl.name)
    if col.kind in ("int", "float"):
        df = _safe_query(db, f"SELECT MIN({q_col}) AS mn, MAX({q_col}) AS mx, AVG({q_col}) AS av FROM {q_tbl}")
        if df is not None and not df.empty:
            col.num_min = _to_float(df["mn"].iloc[0])
            col.num_max = _to_float(df["mx"].iloc[0])
            col.num_avg = _to_float(df["av"].iloc[0])
        return
    if col.kind == "text":
        # 文本列：拉高频 TOP 值（保证 WHERE 样例一定是库里真实存在的值）
        df = _safe_query(
            db,
            f"SELECT {q_col} AS v, COUNT(*) AS c FROM {q_tbl} "
            f"WHERE {q_col} IS NOT NULL AND TRIM({q_col}) <> '' "
            f"GROUP BY {q_col} ORDER BY c DESC LIMIT 8",
        )
        if df is not None and not df.empty:
            col.distinct_top = [str(v) for v in df["v"].tolist() if v is not None]
        # 低基判断需要 distinct 总数（文本大列不做全表统计时回退 None，视为高基）
        ddf = _safe_query(db, f"SELECT COUNT(DISTINCT {q_col}) AS c FROM {q_tbl}")
        if ddf is not None and not ddf.empty and ddf["c"].iloc[0] is not None:
            col.distinct_count = int(ddf["c"].iloc[0])


def _to_float(v: Any) -> Optional[float]:
    """数值结果安全转 float（NULL/异常返回 None）"""
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _scan_date_column(db, tbl: TableProfile, col: ColProfile, db_type: str) -> Optional[int]:
    """
    回读日期（文本或时间类型）列的前 N 行，在 Python 侧统计「年→出现月数」。
    返回一个保证存在 ≥2 个不同月份、且覆盖行数最多的年份；否则返回 None。

    因为月份归属在 Python 侧计算，对文本型日期（如 '2019-5-16'）与时间类型
    都能通用，且不依赖各库方言差异。
    """
    q_col, q_tbl = qid(db_type, col.name), qid(db_type, tbl.name)
    df = _safe_query(
        db,
        f"SELECT {q_col} AS v FROM {q_tbl} "
        f"WHERE {q_col} IS NOT NULL AND TRIM({q_col}) <> '' LIMIT {MAX_DATE_SCAN_ROWS}",
    )
    if df is None or df.empty:
        return None

    # 按「年 → 该年内出现的月份集合」聚合
    year_months: Dict[int, set] = {}
    for raw in df["v"].tolist():
        if raw is None:
            continue
        m = _DATE_RE.match(str(raw))
        if not m:
            continue
        y, mo = int(m.group(1)), int(m.group(2))
        year_months.setdefault(y, set()).add(mo)

    # 优先选「月份数最多且 ≥2」的年份；行覆盖最多的年更贴近真实业务重心
    best_y, best_spread = None, 1
    for y, months in year_months.items():
        if len(months) >= 2 and len(months) > best_spread:
            best_y, best_spread = y, len(months)
    return best_y


def _introspect(db, db_type: str) -> Tuple[List[TableProfile], List[Dict]]:
    """SQLAlchemy Inspector 直读当前连接库，构造表/列画像"""
    profiles: List[TableProfile] = []
    skips: List[Dict] = []

    insp = sa_inspect(db.engine)
    raw_tables = insp.get_table_names()
    if len(raw_tables) > MAX_PROFILE_TABLES:
        skips.append({"category": "*", "reason": f"表数 {len(raw_tables)} 超过护栏 {MAX_PROFILE_TABLES}，仅评估前 {MAX_PROFILE_TABLES} 张"})
        raw_tables = raw_tables[:MAX_PROFILE_TABLES]

    for t in raw_tables:
        try:
            cols_info = insp.get_columns(t)
            pk_info = insp.get_pk_constraint(t)
            pk_cols = list(pk_info.get("constrained_columns") or [])
        except Exception as e:  # noqa: BLE001
            skips.append({"category": "*", "reason": f"读取表 {t} 结构失败: {str(e)[:120]}"})
            continue

        # 行数（仅用作模板可用性参考，空表不出分组/关联题）
        row_df = _safe_query(db, f"SELECT COUNT(*) AS c FROM {qid(db_type, t)}")
        row_count = int(row_df["c"].iloc[0]) if (row_df is not None and not row_df.empty and row_df["c"].iloc[0] is not None) else 0

        tbl = TableProfile(name=t, row_count=row_count, pk_columns=pk_cols)
        for c in cols_info[:MAX_PROFILE_COLS]:
            name = str(c.get("name", ""))
            raw_type = str(c.get("type", ""))
            comment = str(c.get("comment") or "")
            tbl.columns.append(
                ColProfile(
                    name=name,
                    raw_type=raw_type,
                    kind=_normalize_kind(raw_type),
                    comment=comment,
                    is_pk=name in pk_cols,
                )
            )
        profiles.append(tbl)
    return profiles, skips


def _infer_roles(tables: List[TableProfile], db, db_type: str) -> None:
    """
    对每列推断语义角色，并惰性拉取模板需要的统计值。

    判定顺序（三层信号：类型 → 列名/注释关键词 → distinct 数据形状）：
      - 时间/布尔类型列：直接给 date / boolflag
      - 数值列：主键→id；命中度量词→measure；其余按可聚合度量兜底
      - 文本列：主键→id；命中 id/编码→code(id)；命中类别/维→低基维（需低基）
               ；命中名称→label；否则按 distinct 基数决定是否可当低基维
    """
    for tbl in tables:
        for col in tbl.columns:
            if col.kind == "date":
                col.role = "date"
                continue
            if col.kind == "bool":
                col.role = "boolflag"
                continue
            if col.is_pk:
                col.role = "id"
                continue
            if col.kind in ("int", "float"):
                # 非主键数值列：度量词命中或可聚合都当 measure
                if _hits_measure(col.name, col.comment) or _hits_id(col.name, col.comment):
                    pass
                col.role = "measure"
                _col_stats(db, tbl, col, db_type)
                continue
            if col.kind != "text":
                col.role = ""
                continue

            # ---- 文本列 ----
            if _hits_date_name(col.name):
                # 疑似文本型日期（如 order_date）：具体能否解析交由模板阶段
                # 的 _scan_date_column 复核，此处不做昂贵的 TOP 值采样
                col.role = "date"
                continue
            if _hits_id(col.name, col.comment):
                col.role = "id" if col.is_pk else "code"
                continue
            if _hits_label(col.name, col.comment):
                # 名称/标题类：只作展示列，不需要采样统计
                col.role = "label"
                continue

            # 可能作为低基维（分组/WHERE 样例值来源）的列才需要采样：
            #   先按列名命中判「类别维」；无信号则按 distinct 基数兜底。
            _col_stats(db, tbl, col, db_type)  # TOP 值 + COUNT(DISTINCT)
            if _hits_dim(col.name, col.comment):
                col.role = "dim_lowcard" if (col.distinct_count is not None and col.distinct_count <= LOW_CARD_LIMIT) else "label"
                continue
            if col.distinct_count is not None and col.distinct_count <= LOW_CARD_LIMIT:
                col.role = "dim_lowcard"
            elif col.distinct_count is not None and col.distinct_count <= 200:
                col.role = "label"
            else:
                col.role = "code"


def _find_join_pairs(db, tables: List[TableProfile], db_type: str, skips: List[Dict]) -> List[JoinPair]:
    """
    在多张表之间寻找可信 JOIN 键对，返回去重后的 (child, child_key, parent, parent_key)。

    匹配层级（优先高可信）：
      1. 数据库真实外键（子表.column == 父表.主键）
      2. 同名列（一列两表都有、且其中一侧为主键或含 id/code 词）
      3. 「code/id 角色列配对」+ 轻量 SQL 校验：
         子键未匹配到父键的行数 = 0，且父键值基本唯一 → 判定可 JOIN
         （用于适配 sales_order.product_number ↔ product_info.commodity_code
           这类列名完全无重叠的真实业务关联）
    """
    pairs: List[JoinPair] = []
    seen: set = set()

    def _add(child_t, child_k, parent_t, parent_k):
        key = (child_t, child_k, parent_t, parent_k)
        if key not in seen:
            seen.add(key)
            pairs.append(JoinPair(child_t, child_k, parent_t, parent_k))

    # ---- 层级 1：真外键 ----
    insp = sa_inspect(db.engine)
    for t in tables:
        try:
            for fk in insp.get_foreign_keys(t.name):
                for col in fk.get("constrained_columns") or []:
                    ref_t, ref_col = fk.get("referred_table"), (fk.get("referred_columns") or [None])[0]
                    if ref_t and ref_col:
                        _add(t.name, col, ref_t, ref_col)
        except Exception:  # noqa: BLE001
            continue

    # ---- 层级 2：同名列（一侧是主键，或列名含 id/code 词）----
    if not pairs:
        for a in tables:
            for b in tables:
                if a.name == b.name:
                    continue
                cols_a = {c.name for c in a.columns}
                cols_b = {c.name for c in b.columns}
                for shared in sorted(cols_a & cols_b):  # 排序保证每次构造结果确定
                    ca = next(c for c in a.columns if c.name == shared)
                    cb = next(c for c in b.columns if c.name == shared)
                    # 双方都是主键的同名键（如两张表各自的自增 id）几乎必然是伪关联：
                    # 两侧都是本表代理键，拼接没有业务语义。跳过，留给层级 3 按数据校验。
                    if shared in a.pk_columns and shared in b.pk_columns:
                        continue
                    if shared in a.pk_columns:
                        _add(b.name, shared, a.name, shared)
                    elif shared in b.pk_columns:
                        _add(a.name, shared, b.name, shared)
                    elif _hits_id(shared, "") or ca.is_pk or cb.is_pk:
                        _add(a.name, shared, b.name, shared)

    # ---- 层级 3：code/id 角色列配对 + SQL 校验 ----
    if not pairs:
        candidates: List[Tuple[str, str, str, str]] = []
        for a in tables:
            code_a = [c for c in a.columns if c.role in ("code", "id") and not c.is_pk]
            for b in tables:
                if a.name == b.name:
                    continue
                # a 作为子表：a 的 code 列 与 b 的某键列配对
                b_keys = [c for c in b.columns if c.is_pk or c.role in ("code", "id")]
                for ck in code_a:
                    for pk in b_keys:
                        candidates.append((a.name, ck.name, b.name, pk.name))
        # 最多校验 6 对，控制构造耗时
        for child_t, child_k, parent_t, parent_k in candidates[:6]:
            cq, ckq, pq, pkq = qid(db_type, child_t), qid(db_type, child_k), qid(db_type, parent_t), qid(db_type, parent_k)
            # 匹配率 = 能 JOIN 上的子表行数 / 子表总行数（≥95% 才视为可信外键语义，
            # 允许极少孤儿数据，避免因 1~2 条历史脏数据整类被跳过）
            probe = _safe_query(
                db,
                f"SELECT (SELECT COUNT(*) FROM {cq}) AS ct, COUNT(*) AS mt "
                f"FROM {cq} JOIN {pq} ON {ckq} = {pkq}",
            )
            if probe is None or probe.empty:
                continue
            ct = int(probe["ct"].iloc[0]) if probe["ct"].iloc[0] is not None else 0
            mt = int(probe["mt"].iloc[0]) if probe["mt"].iloc[0] is not None else 0
            if ct > 0 and mt >= 0.95 * ct:
                _add(child_t, child_k, parent_t, parent_k)

    # ---- 方向去重 ----
    # 启发式配对常出现「同一对表正反两个方向都通过校验」的镜像 pair。此时只保留
    # 「子表行数更大」的方向：行数大的一侧通常是明细/事实表，适合作为 child（提供
    # 数值度量），行数小的一侧是维度表，作为 parent（提供低基维用于 GROUP BY）。
    if len(pairs) > 1:
        best_by_tables = {}

        def _rc(tname: str) -> int:
            tp = next((t for t in tables if t.name == tname), None)
            return tp.row_count if tp else 0

        for p in pairs:
            key = tuple(sorted((p.child_table, p.parent_table)))
            prev = best_by_tables.get(key)
            if prev is None or _rc(p.child_table) > _rc(prev.child_table):
                best_by_tables[key] = p
        pairs = list(best_by_tables.values())

    if not pairs:
        skips.append({"category": "多表关联", "reason": "未找到可信 JOIN 键（无外键/同名列，且列名配对校验未通过）"})
    return pairs


# ============================================================================
# 候选用例结构
# ============================================================================

@dataclass
class Candidate:
    """模板产出的候选用例（尚未自校验/分配 id）"""
    category: str
    question: str
    sql: str
    description: str


def _need_nonempty(tbl: TableProfile) -> bool:
    """分组/数值模板要求表非空，避免生成空转用例"""
    return tbl.row_count > 0


# ============================================================================
# 各分类模板生成器（返回迭代器）
# ============================================================================

def tpl_base_aggregate(tables: List[TableProfile], db, db_type: str) -> Iterator[Candidate]:
    """基础聚合：COUNT 总数 / 单表 AVG / 低基维分组聚合"""
    for tbl in tables:
        # 1) 行数总数（空表也算：COUNT 恒为 0，仍可执行）
        yield Candidate(
            "基础聚合",
            f"查询表 {tbl.name} 一共有多少条记录？",
            f"SELECT COUNT(*) AS cnt FROM {qid(db_type, tbl.name)}",
            "全表行数统计",
        )
        if not _need_nonempty(tbl):
            continue
        cols = [c for c in tbl.columns if c.role == "measure" and c.num_avg is not None]
        if cols:
            m = cols[0]
            yield Candidate(
                "基础聚合",
                f"查询表 {tbl.name} 中字段 {m.name} 的平均值",
                f"SELECT AVG({qid(db_type, m.name)}) AS avg_val FROM {qid(db_type, tbl.name)}",
                "数值列平均值",
            )
        # 2) 分组聚合：低基维 + 数值度量
        dims = [c for c in tbl.columns if c.role == "dim_lowcard"]
        meas = [c for c in tbl.columns if c.role == "measure" and c.num_max is not None]
        if dims and meas:
            d, m = dims[0], meas[0]
            example = f"（如 {d.distinct_top[0]}）" if d.distinct_top else ""
            yield Candidate(
                "基础聚合",
                f"按字段 {d.name}{example}分组，统计表 {tbl.name} 中 {m.name} 的总和，从高到低排列",
                f"SELECT {qid(db_type, d.name)}, SUM({qid(db_type, m.name)}) AS agg_val "
                f"FROM {qid(db_type, tbl.name)} GROUP BY {qid(db_type, d.name)} "
                f"ORDER BY agg_val DESC",
                "分组求和",
            )


def tpl_multi_join(tables: List[TableProfile], db, db_type: str, pairs: List[JoinPair]) -> Iterator[Candidate]:
    """多表关联：JOIN 后按父表低基维分组聚合子表度量"""
    if not _need_join_tables(tables):
        return
    for pair in pairs:
        child = _table_of(tables, pair.child_table)
        parent = _table_of(tables, pair.parent_table)
        if not child or not parent:
            continue
        dims = [c for c in parent.columns if c.role == "dim_lowcard"]
        meas = [c for c in child.columns if c.role == "measure" and c.num_max is not None]
        if not dims or not meas:
            continue
        d, m = dims[0], meas[0]
        example = f"（如 {d.distinct_top[0]}）" if d.distinct_top else ""
        yield Candidate(
            "多表关联",
            f"把表 {child.name} 通过字段 {pair.child_key} 与表 {parent.name}（字段 {pair.parent_key}）关联，"
            f"按 {parent.name} 的字段 {d.name}{example}分组，统计 {child.name} 中 {m.name} 的总和，从高到低排列",
            f"SELECT {qid(db_type, parent.name)}.{qid(db_type, d.name)}, SUM({qid(db_type, child.name)}.{qid(db_type, m.name)}) AS agg_val "
            f"FROM {qid(db_type, child.name)} "
            f"JOIN {qid(db_type, parent.name)} "
            f"ON {qid(db_type, child.name)}.{qid(db_type, pair.child_key)} = {qid(db_type, parent.name)}.{qid(db_type, pair.parent_key)} "
            f"GROUP BY {qid(db_type, parent.name)}.{qid(db_type, d.name)} "
            f"ORDER BY agg_val DESC",
            "双表 JOIN 分组聚合",
        )


def _need_join_tables(tables: List[TableProfile]) -> bool:
    return any(t.row_count > 0 for t in tables)


def _table_of(tables: List[TableProfile], name: str) -> Optional[TableProfile]:
    return next((t for t in tables if t.name == name), None)


def tpl_time_analysis(tables: List[TableProfile], db, db_type: str) -> Iterator[Candidate]:
    """时间分析：在存在真实日期列且某年有 ≥2 个月的表上，按月统计（年份锚定）"""
    for tbl in tables:
        if not _need_nonempty(tbl):
            continue
        date_cols = [c for c in tbl.columns if c.role == "date"]
        for dcol in date_cols:
            year = _scan_date_column(db, tbl, dcol, db_type)
            if not year:
                continue
            q_col = qid(db_type, dcol.name)
            q_tbl = qid(db_type, tbl.name)
            # 按月统计行数（始终可生成）
            yield Candidate(
                "时间分析",
                f"统计 {year} 年每个月在表 {tbl.name} 中的记录数，结果按月份从早到晚排列",
                f"SELECT {month_expr(db_type, q_col)} AS month, COUNT(*) AS cnt FROM {q_tbl} "
                f"WHERE {year_expr(db_type, q_col)} = {year} "
                f"GROUP BY {month_expr(db_type, q_col)} ORDER BY month",
                "按年-月统计数量",
            )
            # 有度量列时再出一题：按月求和
            meas = [c for c in tbl.columns if c.role == "measure" and c.num_max is not None]
            if meas:
                m = meas[0]
                yield Candidate(
                    "时间分析",
                    f"统计 {year} 年每个月表 {tbl.name} 中 {m.name} 的总和，结果按月份从早到晚排列",
                    f"SELECT {month_expr(db_type, q_col)} AS month, SUM({qid(db_type, m.name)}) AS agg_val FROM {q_tbl} "
                    f"WHERE {year_expr(db_type, q_col)} = {year} "
                    f"GROUP BY {month_expr(db_type, q_col)} ORDER BY month",
                    "按年-月求和",
                )
            break  # 每表最多处理一个日期列


def tpl_filter(tables: List[TableProfile], db, db_type: str) -> Iterator[Candidate]:
    """条件过滤：等于真实样例值 / 数值大于样本中位阈值"""
    for tbl in tables:
        if not _need_nonempty(tbl):
            continue
        dims = [c for c in tbl.columns if c.role == "dim_lowcard" and c.distinct_top]
        if dims:
            d = dims[0]
            v = d.distinct_top[0]
            yield Candidate(
                "条件过滤",
                f"查询表 {tbl.name} 中字段 {d.name} 等于 '{v}' 的记录数量",
                f"SELECT COUNT(*) AS cnt FROM {qid(db_type, tbl.name)} "
                f"WHERE {qid(db_type, d.name)} = {sq(v)}",
                "等值条件计数",
            )
        meas = [c for c in tbl.columns if c.role == "measure" and c.num_min is not None and c.num_max is not None]
        if meas:
            m = meas[0]
            mid = (m.num_min + m.num_max) / 2.0
            # 大结果集必须 LIMIT 50 且题干写明：实测生成器对"记录很多"的查询习惯补
            # LIMIT 50，若标准答案不限行会因行数不匹配被判伪负例。
            yield Candidate(
                "条件过滤",
                f"查询表 {tbl.name} 中字段 {m.name} 大于 {_num_literal(mid)} 的记录，按该字段从高到低取前 50 条",
                f"SELECT * FROM {qid(db_type, tbl.name)} WHERE {qid(db_type, m.name)} > {_num_literal(mid)} "
                f"ORDER BY {qid(db_type, m.name)} DESC LIMIT 50",
                "数值范围过滤",
            )


def tpl_advanced(tables: List[TableProfile], db, db_type: str) -> Iterator[Candidate]:
    """
    高级分析：两个低基维字段间做「分组占比」统计。
    计算公式在问题中明确给出，生成器可复现同一 CASE 口径 → 公平可测。
    """
    for tbl in tables:
        if not _need_nonempty(tbl):
            continue
        dims = [c for c in tbl.columns if c.role == "dim_lowcard"]
        if len(dims) < 2:
            continue
        d1, d2 = dims[0], dims[1]
        v = d2.distinct_top[0] if d2.distinct_top else ""
        if not v:
            continue
        q_tbl = qid(db_type, tbl.name)
        g_expr = qid(db_type, d1.name)
        val_expr = qid(db_type, d2.name)
        yield Candidate(
            "高级分析",
            f"按表 {tbl.name} 的字段 {d1.name} 分组，统计每组中字段 {d2.name} 等于 '{v}' 的记录占比（百分比，保留2位小数），从高到低排列",
            f"SELECT {g_expr}, COUNT(*) AS total, "
            f"SUM(CASE WHEN {val_expr} = {sq(v)} THEN 1 ELSE 0 END) AS sub_cnt, "
            f"ROUND(100.0 * SUM(CASE WHEN {val_expr} = {sq(v)} THEN 1 ELSE 0 END) / COUNT(*), 2) AS rate "
            f"FROM {q_tbl} GROUP BY {g_expr} ORDER BY rate DESC",
            "分组内条件占比",
        )


def tpl_rank(tables: List[TableProfile], db, db_type: str) -> Iterator[Candidate]:
    """排序分析：数值度量最高的前 N / 最低的前 N"""
    for tbl in tables:
        if not _need_nonempty(tbl):
            continue
        meas = [c for c in tbl.columns if c.role == "measure" and c.num_max is not None]
        labels = [c for c in tbl.columns if c.role in ("label", "code", "dim_lowcard")]
        if not meas:
            continue
        m = meas[0]
        q_tbl = qid(db_type, tbl.name)
        sel = qid(db_type, m.name)
        if labels:
            lbl = labels[0]
            sel = f"{qid(db_type, lbl.name)}, {qid(db_type, m.name)}"
        yield Candidate(
            "排序分析",
            f"查询表 {tbl.name} 中字段 {m.name} 最高的前 5 条记录",
            f"SELECT {sel} FROM {q_tbl} ORDER BY {qid(db_type, m.name)} DESC LIMIT 5",
            "数值降序 TOP5",
        )
        yield Candidate(
            "排序分析",
            f"查询表 {tbl.name} 中字段 {m.name} 最低的前 5 条记录",
            f"SELECT {sel} FROM {q_tbl} ORDER BY {qid(db_type, m.name)} ASC LIMIT 5",
            "数值升序 TOP5",
        )


def tpl_complex(tables: List[TableProfile], db, db_type: str) -> Iterator[Candidate]:
    """复杂分析：高于均值子查询 / GROUP BY + HAVING"""
    for tbl in tables:
        if not _need_nonempty(tbl):
            continue
        meas = [c for c in tbl.columns if c.role == "measure" and c.num_avg is not None and c.num_max is not None]
        if meas:
            m = meas[0]
            q_col = qid(db_type, m.name)
            # 大结果集补 LIMIT 50 + 题干写明，口径同「数值范围过滤」模板
            yield Candidate(
                "复杂分析",
                f"查询表 {tbl.name} 中字段 {m.name} 高于该字段平均值的记录，按该字段从高到低取前 50 条",
                f"SELECT {q_col} FROM {qid(db_type, tbl.name)} "
                f"WHERE {q_col} > (SELECT AVG({q_col}) FROM {qid(db_type, tbl.name)}) "
                f"ORDER BY {q_col} DESC LIMIT 50",
                "高于均值子查询",
            )
        dims = [c for c in tbl.columns if c.role == "dim_lowcard"]
        meas2 = [c for c in tbl.columns if c.role == "measure" and c.num_max is not None]
        if dims and meas2:
            d, m = dims[0], meas2[0]
            q_d = qid(db_type, d.name)
            q_m = qid(db_type, m.name)
            yield Candidate(
                "复杂分析",
                f"按表 {tbl.name} 的字段 {d.name} 分组，只保留出现至少 2 条的组，统计每组 {m.name} 的总和，从高到低排列",
                f"SELECT {q_d}, SUM({q_m}) AS agg_val FROM {qid(db_type, tbl.name)} "
                f"GROUP BY {q_d} HAVING COUNT(*) >= 2 ORDER BY agg_val DESC",
                "分组过滤 HAVING",
            )


# ============================================================================
# 自校验 + 配额选择
# ============================================================================

def _validate_candidate(db, cand: Candidate) -> bool:
    """
    自校验：候选标准 SQL 必须能在当前库真实执行且不抛错，才允许进入数据集。
    这是杜绝「FROM users 跑不了的题」的关键防线（方言/列名错误在此暴露）。
    """
    df = _safe_query(db, cand.sql)
    return df is not None


def _select_cases(candidates: List[Candidate], max_cases: int) -> List[Dict]:
    """
    配额式选取：按 7 类 round-robin 轮流取题，保证分类覆盖、总数 ≤ max_cases。
    候选不足的类别不强行凑数（前端按 Object.keys 兼容缺失分类）。
    """
    per_cat: Dict[str, list] = {}
    for cat in CATEGORY_ORDER:
        per_cat[cat] = [c for c in candidates if c.category == cat]

    quota = -(-max_cases // len(CATEGORY_ORDER))  # ceil(max/7)，防止除零由常量保证
    picked: List[Candidate] = []
    cat_used = {cat: 0 for cat in CATEGORY_ORDER}
    cat_idx = 0
    order = [c for c in CATEGORY_ORDER if per_cat[c]]  # 只保留有候选的分类
    while len(picked) < max_cases and order:
        progressed = False
        for cat in order:
            if len(picked) >= max_cases:
                break
            if per_cat[cat] and cat_used[cat] < quota:
                picked.append(per_cat[cat].pop(0))
                cat_used[cat] += 1
                progressed = True
        if not progressed:
            break

    cases: List[Dict] = []
    for i, c in enumerate(picked, 1):
        cases.append(
            {
                "id": "Q%03d" % i,
                "category": c.category,
                "question": c.question,
                "standard_sql": c.sql,
                "description": c.description,
            }
        )
    return cases


# ============================================================================
# 对外统一入口
# ============================================================================

def build_eval_dataset(db=None, max_cases: int = None) -> EvalSnapshot:
    """
    动态构造评估数据集。

    参数:
        db: 数据库管理器（默认取全局 get_db() 单例）
        max_cases: 最大用例数（默认取 CONFIG.eval.max_cases，再兜底 24）

    返回:
        EvalSnapshot: {cases, skips, meta}
    """
    if max_cases is None:
        max_cases = int(getattr(getattr(CONFIG, "eval", None), "max_cases", 24) or 24)
    db = db or get_db()
    db_type = str(getattr(db, "active_db_type", "sqlite")).lower()

    if db_type not in ("mysql", "sqlite", "postgres"):
        return EvalSnapshot(
            cases=[],
            skips=[{"category": "*", "reason": f"不支持的数据库类型: {db_type}"}],
            meta={"db_type": db_type, "tables": [], "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "category_counts": {}},
        )

    skips: List[Dict] = []
    candidates: List[Candidate] = []
    tables: List[TableProfile] = []

    try:
        tables, skip_t = _introspect(db, db_type)
        skips.extend(skip_t)
        _infer_roles(tables, db, db_type)

        if not tables:
            skips.append({"category": "*", "reason": "当前连接库没有可用表"})

        # 仅当存在有数据的表时才尝试 JOIN（空表对无评估意义）
        pairs = _find_join_pairs(db, tables, db_type, skips) if any(t.row_count > 0 for t in tables) else []

        generators = [
            tpl_base_aggregate(tables, db, db_type),
            tpl_multi_join(tables, db, db_type, pairs),
            tpl_time_analysis(tables, db, db_type),
            tpl_filter(tables, db, db_type),
            tpl_advanced(tables, db, db_type),
            tpl_rank(tables, db, db_type),
            tpl_complex(tables, db, db_type),
        ]
        for gen in generators:
            for cand in gen:
                if not _validate_candidate(db, cand):
                    skips.append({"category": cand.category, "sql": cand.sql[:120], "reason": "标准 SQL 自校验执行失败，已剔除"})
                    continue
                candidates.append(cand)
                # 每类候选数量上限，防止超大库产生海量候选拖慢构造
                if sum(1 for c in candidates if c.category == cand.category) >= 12:
                    break
    except Exception as e:  # noqa: BLE001 — 连接失败/异常整体降级为空数据集
        logger.warning("[eval-dataset-builder] 构造数据集失败: %s", e)
        skips.append({"category": "*", "reason": f"构建评估数据集异常: {str(e)[:200]}"})

    cases = _select_cases(candidates, max_cases)
    counts: Dict[str, int] = {}
    for c in cases:
        counts[c["category"]] = counts.get(c["category"], 0) + 1

    meta = {
        "db_type": db_type,
        "tables": [t.name for t in tables],
        "candidate_count": len(candidates),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "category_counts": counts,
    }
    return EvalSnapshot(cases=cases, skips=skips, meta=meta)
