"""
==============================================================================
多模态 RAG 文件处理模块 — PDF/Excel 解析 + Route Chain
==============================================================================
设计思路：
  在企业数据分析场景中，用户可能上传文件（报表 PDF、数据 Excel）
  作为查询上下文。本模块实现：

  1. PDF 解析：提取文本，支持表格识别
  2. Excel 解析：读取表格数据，支持多 sheet
  3. Route Chain：智能判断用户意图是查数据库（SQL_ROUTE）
     还是分析文件（DOCUMENT_ROUTE），然后路由到对应的处理链路

  路由决策基于关键词匹配+LLM 分类（LLM 可用时）。
==============================================================================
"""

import io
import logging
import os
import re
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import pandas as pd

from config import CONFIG

logger = logging.getLogger("file_processor")


# ============================================================================
# 路由类型
# ============================================================================

class RouteType(str, Enum):
    """路由类型：查询数据库还是分析文件"""
    SQL_ROUTE = "sql"          # 查数据库
    DOCUMENT_ROUTE = "doc"     # 分析文件
    UNKNOWN = "unknown"        # 无法确定


# ============================================================================
# 文件解析结果
# ============================================================================

@dataclass
class FileContent:
    """文件解析后的结构化内容"""
    filename: str
    file_type: str                  # "pdf", "excel", "csv"
    pages: List[Dict] = field(default_factory=list)       # PDF pages: [{page_num, text, tables}]
    sheets: Dict[str, pd.DataFrame] = field(default_factory=dict)  # Excel sheets
    summary: str = ""               # 自动生成的摘要
    row_count: int = 0
    error: Optional[str] = None


# ============================================================================
# PDF 处理器
# ============================================================================

class PDFProcessor:
    """
    PDF 文件解析器。

    使用 PyPDF2 提取文本内容。如安装了 pdfplumber 则同时提取表格。
    """

    def __init__(self):
        self._pdfplumber_available = False
        self._check_dependencies()

    def _check_dependencies(self):
        """检查 PDF 相关依赖是否安装"""
        try:
            import PyPDF2  # noqa
        except ImportError:
            logger.warning("[PDF] PyPDF2 未安装，PDF 解析不可用")
            self._pypdf2_available = False
        else:
            self._pypdf2_available = True

        try:
            import pdfplumber  # noqa
            self._pdfplumber_available = True
        except ImportError:
            self._pdfplumber_available = False

    def is_available(self) -> bool:
        """检查 PDF 解析功能是否可用"""
        return self._pypdf2_available

    def parse(self, file_bytes: bytes, filename: str) -> FileContent:
        """
        解析 PDF 文件。

        参数:
            file_bytes: PDF 文件二进制内容
            filename: 文件名

        返回:
            FileContent 对象
        """
        if not self._pypdf2_available:
            return FileContent(
                filename=filename,
                file_type="pdf",
                error="PyPDF2 未安装，无法解析 PDF",
            )

        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            content = FileContent(filename=filename, file_type="pdf")
            full_text_parts = []

            for page_num, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                page_data = {
                    "page_num": page_num,
                    "text": text,
                    "tables": [],
                }
                content.pages.append(page_data)
                full_text_parts.append(f"【第{page_num}页】\n{text}")

                # 统计字符数
                if text.strip():
                    content.row_count += len(text.split("\n"))

            # 如果有 pdfplumber，进一步提取表格
            if self._pdfplumber_available:
                try:
                    import pdfplumber
                    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                        for i, page in enumerate(pdf.pages):
                            tables = page.extract_tables()
                            if tables and i < len(content.pages):
                                for table in tables:
                                    if table:
                                        df = pd.DataFrame(table[1:], columns=table[0])
                                        content.pages[i]["tables"].append(df)
                                        sheet_name = f"page{i+1}_table{len(content.pages[i]['tables'])}"
                                        content.sheets[sheet_name] = df
                                        content.row_count += len(df)
                except Exception as e:
                    logger.warning(f"[PDF] pdfplumber 表格提取失败: {e}")

            # 生成摘要
            full_text = "\n".join(full_text_parts)
            content.summary = self._generate_summary(full_text, content)

            logger.info(
                f"[PDF] 解析完成: {filename} | "
                f"{len(reader.pages)} 页 | {len(full_text)} 字符"
            )
            return content

        except Exception as e:
            logger.error(f"[PDF] 解析失败: {e}")
            return FileContent(
                filename=filename,
                file_type="pdf",
                error=f"PDF 解析失败: {e}",
            )

    def _generate_summary(self, full_text: str, content: FileContent) -> str:
        """生成 PDF 内容摘要"""
        # 取前 2000 字符作为摘要
        preview = full_text[:2000]
        lines = [
            f"文件: {content.filename}",
            f"页数: {len(content.pages)}",
            f"总字符数: {len(full_text)}",
            f"包含表格: {len(content.sheets)} 个",
            "",
            "【内容预览】",
            preview,
        ]
        return "\n".join(lines)


# ============================================================================
# Excel 处理器
# ============================================================================

class ExcelProcessor:
    """
    Excel 文件解析器。

    使用 pandas 读取所有 sheet，支持 .xlsx 和 .csv。
    """

    def parse(self, file_bytes: bytes, filename: str) -> FileContent:
        """
        解析 Excel 文件。

        参数:
            file_bytes: Excel 文件二进制内容
            filename: 文件名

        返回:
            FileContent 对象
        """
        content = FileContent(filename=filename, file_type="excel")
        ext = Path(filename).suffix.lower()

        try:
            if ext == ".csv":
                # CSV 文件
                df = pd.read_csv(io.BytesIO(file_bytes))
                content.sheets["Sheet1"] = df
                content.row_count = len(df)
                content.summary = self._format_excel_summary(content)
                logger.info(f"[Excel] CSV 解析完成: {filename} | {len(df)} 行")
            else:
                # Excel 文件（.xlsx, .xls）
                excel_file = io.BytesIO(file_bytes)
                sheets_dict = pd.read_excel(excel_file, sheet_name=None, engine="openpyxl")
                for sheet_name, df in sheets_dict.items():
                    content.sheets[sheet_name] = df
                    content.row_count += len(df)
                content.summary = self._format_excel_summary(content)
                logger.info(
                    f"[Excel] 解析完成: {filename} | "
                    f"{len(sheets_dict)} 个 sheet | {content.row_count} 行"
                )

        except Exception as e:
            logger.error(f"[Excel] 解析失败: {e}")
            content.error = f"Excel 解析失败: {e}"

        return content

    def _format_excel_summary(self, content: FileContent) -> str:
        """生成 Excel 内容的结构化摘要"""
        parts = [
            f"文件: {content.filename}",
            f"Sheet 数量: {len(content.sheets)}",
        ]

        for sheet_name, df in content.sheets.items():
            cols = ", ".join(str(c) for c in df.columns[:10])
            parts.extend([
                f"\n【{sheet_name}】",
                f"  行数: {len(df)}",
                f"  列数: {len(df.columns)}",
                f"  字段: {cols}{'...' if len(df.columns) > 10 else ''}",
            ])

        return "\n".join(parts)


# ============================================================================
# 文件处理器（统一入口）
# ============================================================================

class FileProcessor:
    """
    统一文件处理器。

    自动识别文件类型并调用对应的解析器。
    """

    def __init__(self):
        self.pdf_processor = PDFProcessor()
        self.excel_processor = ExcelProcessor()

    def process(self, file_bytes: bytes, filename: str) -> FileContent:
        """
        处理上传的文件。

        参数:
            file_bytes: 文件二进制内容
            filename: 原始文件名

        返回:
            解析后的 FileContent
        """
        ext = Path(filename).suffix.lower()

        if ext == ".pdf":
            return self.pdf_processor.parse(file_bytes, filename)
        elif ext in (".xlsx", ".xls", ".csv"):
            content = self.excel_processor.parse(file_bytes, filename)
            content.file_type = "excel"
            return content
        else:
            return FileContent(
                filename=filename,
                file_type=ext.lstrip("."),
                error=f"不支持的文件格式: {ext}（支持 PDF、Excel、CSV）",
            )

    def file_to_dataframe(self, file_bytes: bytes, filename: str) -> Optional[pd.DataFrame]:
        """
        将上传文件转换为 DataFrame（适用于 Excel/CSV）。

        返回第一个 sheet 的内容，PDF 返回 None。
        """
        content = self.process(file_bytes, filename)
        if content.error:
            return None
        if content.sheets:
            # 返回第一个 sheet
            first_sheet = list(content.sheets.values())[0]
            return first_sheet
        return None


# ============================================================================
# Route Chain — 智能路由
# ============================================================================

class RouteChain:
    """
    路由链：判断用户意图是查数据库还是分析文件。

    分类逻辑：
    1. 如果用户上传了文件，优先走 DOCUMENT_ROUTE
    2. 如果用户问题中包含数据库查询关键词，走 SQL_ROUTE
    3. 如果用户问题引用文件内容，走 DOCUMENT_ROUTE
    4. 如果有 LLM 可用，使用 LLM 进行分类
    """

    # 数据库查询关键词（命中任意即走 SQL_ROUTE）
    SQL_KEYWORDS = [
        "查询", "统计", "计算", "汇总", "搜索", "找", "查看", "显示",
        "订单", "用户", "商品", "产品", "销售", "收入", "利润",
        "多少", "哪个", "哪些", "几个", "平均", "最高", "最低",
        "排名", "占比", "趋势", "对比", "环比", "同比",
        "select", "sql", "数据库", "表", "数据",
        "省份", "城市", "类别", "会员", "等级",
    ]

    # 文件分析关键词（命中任意即走 DOCUMENT_ROUTE）
    DOC_KEYWORDS = [
        "文件", "文档", "pdf", "excel", "报表", "上传",
        "提取", "解析", "读取", "导入", "导出",
        "这张表", "这个文件", "附件", "sheet",
    ]

    def __init__(self):
        self._llm = None

    def _init_llm(self):
        """延迟初始化 LLM 客户端"""
        if self._llm is not None:
            return
        try:
            from agent import LLMClient
            self._llm = LLMClient()
        except Exception:
            self._llm = False  # False 表示不可用

    def classify(
        self,
        question: str,
        has_uploaded_file: bool = False,
        use_llm: bool = False,
    ) -> Tuple[RouteType, str]:
        """
        分类用户意图并返回路由决策。

        参数:
            question: 用户问题
            has_uploaded_file: 是否已上传文件
            use_llm: 是否使用 LLM 辅助分类

        返回:
            (route_type, reasoning)
        """
        q_lower = question.lower().strip()

        # ---- 规则 1: 如果上传了文件且问题涉及该文件 ----
        if has_uploaded_file:
            doc_keyword_hit = any(kw in q_lower for kw in self.DOC_KEYWORDS)
            # 如果问题不包含明确的 SQL 关键词，走文档路由
            sql_keyword_hit = any(kw in q_lower for kw in self.SQL_KEYWORDS)
            if doc_keyword_hit or not sql_keyword_hit:
                return (RouteType.DOCUMENT_ROUTE, "用户已上传文件且问题涉及文件内容")
            # 如果既有文件又有 SQL 关键词，使用 LLM 判断
            if use_llm:
                return self._llm_classify(question)

        # ---- 规则 2: 基于关键词分类 ----
        sql_score = sum(1 for kw in self.SQL_KEYWORDS if kw in q_lower)
        doc_score = sum(1 for kw in self.DOC_KEYWORDS if kw in q_lower)

        # 如果问题很短（< 5 个字），默认走文档路由（可能是文件相关对话）
        if len(question) < 5 and has_uploaded_file:
            return (RouteType.DOCUMENT_ROUTE, "问题简短，默认走文档路由")

        # SQL 关键词占优
        if sql_score > doc_score:
            return (RouteType.SQL_ROUTE, f"SQL 关键词匹配 {sql_score} 个")

        # 文档关键词占优
        if doc_score > sql_score:
            return (RouteType.DOCUMENT_ROUTE, f"文档关键词匹配 {doc_score} 个")

        # 分数相同或都为 0，使用 LLM 辅助
        if use_llm:
            return self._llm_classify(question)

        # 默认：走 SQL 路由
        return (RouteType.SQL_ROUTE, "默认路由：走 SQL 查询")

    def _llm_classify(self, question: str) -> Tuple[RouteType, str]:
        """使用 LLM 进行分类"""
        self._init_llm()
        if not self._llm:
            return (RouteType.SQL_ROUTE, "LLM 不可用，默认 SQL 路由")

        prompt = f"""判断以下用户意图是「数据库查询」还是「文件分析」。

分类规则：
- 数据库查询：用户想从数据库里查数据，涉及查询、统计、计算等
- 文件分析：用户想分析上传的文件/文档内容

用户问题：{question}

请只输出「数据库查询」或「文件分析」，不要输出其他内容。"""

        try:
            response = self._llm.generate(prompt)
            if "文件" in response:
                return (RouteType.DOCUMENT_ROUTE, f"LLM 分类: {response[:50]}")
            return (RouteType.SQL_ROUTE, f"LLM 分类: {response[:50]}")
        except Exception as e:
            logger.warning(f"[RouteChain] LLM 分类失败: {e}")
            return (RouteType.SQL_ROUTE, "LLM 分类失败，默认 SQL 路由")


# ============================================================================
# 文档 RAG 引擎（基于上传文件的内容回答问题）
# ============================================================================

class DocumentRAG:
    """
    文档 RAG 引擎。

    对已解析的文件内容执行简单检索，回答用户关于文件内容的问题。
    配合 LLM 可以完成摘要、分析等任务。
    """

    def __init__(self):
        self._current_content: Optional[FileContent] = None

    def load(self, content: FileContent):
        """加载解析后的文件内容"""
        self._current_content = content

    def get_all_text(self) -> str:
        """获取文件全部文本内容"""
        if not self._current_content:
            return ""

        parts = []

        # PDF 文本
        for page in self._current_content.pages:
            parts.append(page.get("text", ""))

        # Excel 数据
        for sheet_name, df in self._current_content.sheets.items():
            parts.append(f"\n【Sheet: {sheet_name}】")
            parts.append(df.to_string(max_rows=200))

        return "\n".join(parts)

    def query(
        self,
        question: str,
        use_llm: bool = True,
    ) -> Dict[str, Any]:
        """
        对文件内容回答问题。

        参数:
            question: 用户问题
            use_llm: 是否使用 LLM 生成回答

        返回:
            {"answer": str, "source": str, "error": str | None}
        """
        if not self._current_content:
            return {
                "answer": "",
                "source": "none",
                "error": "未加载任何文件",
            }

        if self._current_content.error:
            return {
                "answer": "",
                "source": "none",
                "error": self._current_content.error,
            }

        if not use_llm:
            # 简单关键词匹配
            answer = self._keyword_search(question)
            return {
                "answer": answer,
                "source": "keyword",
                "error": None,
            }

        # 使用 LLM 进行问答
        try:
            from agent import LLMClient
            llm = LLMClient()

            text_content = self.get_all_text()
            # 截断过长的内容（LLM 上下文限制）
            if len(text_content) > 12000:
                text_content = text_content[:6000] + "\n...(内容截断)..."

            prompt = f"""你是一位数据分析助手。用户上传了一个文件，请根据文件内容回答问题。

【文件摘要】
{self._current_content.summary}

【文件内容】
{text_content}

【问题】
{question}

请根据文件内容回答用户问题。如果文件内容无法回答该问题，请如实说明。"""

            response = llm.generate(prompt)
            return {
                "answer": response,
                "source": "llm",
                "error": None,
            }

        except Exception as e:
            logger.error(f"[DocumentRAG] LLM 问答失败: {e}")
            # 降级到关键词搜索
            answer = self._keyword_search(question)
            return {
                "answer": answer or "LLM 不可用，无法分析文件内容",
                "source": "keyword" if answer else "none",
                "error": str(e) if not answer else None,
            }

    def _keyword_search(self, question: str) -> str:
        """基于关键词的简单文件检索"""
        if not self._current_content:
            return ""

        text = self.get_all_text()
        q_words = set(re.findall(r'[一-鿿]+|[a-zA-Z_]+', question.lower()))

        if not q_words:
            return ""

        # 按行搜索相关段落
        lines = text.split("\n")
        scored_lines = []
        for line in lines:
            line_lower = line.lower()
            score = sum(1 for w in q_words if w in line_lower)
            if score > 0:
                scored_lines.append((score, line.strip()))

        if not scored_lines:
            return "未在文件内容中找到相关信息。"

        scored_lines.sort(key=lambda x: x[0], reverse=True)
        top_lines = [line for _, line in scored_lines[:10]]
        return "根据文件内容找到以下相关信息：\n" + "\n".join(top_lines)


# ============================================================================
# 便捷函数
# ============================================================================

def process_uploaded_file(file_bytes: bytes, filename: str) -> FileContent:
    """一键处理上传文件"""
    processor = FileProcessor()
    return processor.process(file_bytes, filename)


# ============================================================================
# 独立测试入口
# ============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 测试 Route Chain
    router = RouteChain()
    test_cases = [
        ("查询上个月的销售额", False),
        ("分析这个 Excel 文件", True),
        ("看看这个报表的数据", True),
        ("统计各省用户数量", False),
    ]

    print("=" * 60)
    print("Route Chain 测试")
    print("=" * 60)
    for question, has_file in test_cases:
        route, reason = router.classify(question, has_file)
        print(f"\n问题: '{question}' | 有文件: {has_file}")
        print(f"  → {route.value} ({reason})")

    # 测试 Excel 处理
    print("\n" + "=" * 60)
    print("Excel 处理测试")
    print("=" * 60)
    processor = FileProcessor()
    # 创建一个测试用的 Excel 文件
    test_df = pd.DataFrame({"name": ["A", "B"], "value": [1, 2]})
    buf = io.BytesIO()
    test_df.to_excel(buf, index=False)
    buf.seek(0)
    content = processor.process(buf.read(), "test.xlsx")
    print(f"  解析结果: {'成功' if not content.error else '失败'}")
    print(f"  Sheet 数: {len(content.sheets)}")
    print(f"  总行数: {content.row_count}")
