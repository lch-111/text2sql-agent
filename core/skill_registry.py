"""
==============================================================================
Skill 注册表 — 为 Text-to-SQL Agent 提供插件化能力
==============================================================================
允许从外部 markdown 文件加载技能，动态注入到 SQL 生成 Prompt。

用法:
  from core.skill_registry import SkillRegistry
  registry = SkillRegistry()
  instructions = registry.get_instructions(question, schema)
  # 将 instructions 注入到 SQL 生成 Prompt
===============================================================================
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional, Set

logger = logging.getLogger("skill_registry")

SKILL_DIR = os.path.join(os.path.dirname(__file__), "skills")


class Skill:
    """单个技能定义"""

    def __init__(self, filepath: str):
        self.name = ""
        self.description = ""
        self.triggers: List[str] = []     # 关键词触发
        self.table_patterns: List[str] = []  # 表名匹配
        self.instructions = ""
        self._loaded = False
        self._load(filepath)

    def _load(self, filepath: str):
        """解析 markdown 格式的技能文件"""
        if not os.path.exists(filepath):
            return
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # 解析 frontmatter
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
        if not match:
            return

        frontmatter = match.group(1)
        body = match.group(2).strip()

        # 提取字段
        fm = {}
        for line in frontmatter.split("\n"):
            if ":" in line:
                key, val = line.split(":", 1)
                fm[key.strip()] = val.strip()

        self.name = fm.get("name", "")
        self.description = fm.get("description", "")
        self.instructions = body

        # 解析 triggers（逗号分隔的关键词列表）
        triggers_raw = fm.get("triggers", "")
        if triggers_raw:
            self.triggers = [t.strip() for t in triggers_raw.split(",") if t.strip()]

        # 解析 table_patterns
        tables_raw = fm.get("table_patterns", "")
        if tables_raw:
            self.table_patterns = [t.strip() for t in tables_raw.split(",") if t.strip()]

        self._loaded = True
        logger.info(f"[Skill] 已加载: {self.name}")

    def matches(self, question: str, tables: List[str] = None) -> bool:
        """判断该技能是否适用于当前查询"""
        if not self._loaded:
            return False
        q = question.lower()
        # 关键词触发
        for keyword in self.triggers:
            if keyword.lower() in q:
                return True
        # 表名匹配
        if tables and self.table_patterns:
            for pattern in self.table_patterns:
                for tbl in tables:
                    if pattern.lower() in tbl.lower():
                        return True
        return False

    def get_instruction_block(self) -> str:
        """获取格式化后的指令文本"""
        if not self._loaded or not self.instructions:
            return ""
        return f"\n【技能 - {self.name}】\n{self.instructions}\n"


class SkillRegistry:
    """
    Skill 注册表 — 扫描 skills 目录，管理与注入技能。

    用法:
        registry = SkillRegistry()
        instructions = registry.get_instructions("查询广东销售额", ["sales_order"])
        prompt += instructions
    """

    def __init__(self, skill_dir: str = None):
        self.skill_dir = skill_dir or SKILL_DIR
        self.skills: List[Skill] = []
        self._scan()

    def _scan(self):
        """扫描 skills 目录加载所有技能"""
        if not os.path.isdir(self.skill_dir):
            logger.warning(f"[Skill] 目录不存在: {self.skill_dir}")
            return
        for fname in os.listdir(self.skill_dir):
            if fname.endswith(".md"):
                fpath = os.path.join(self.skill_dir, fname)
                skill = Skill(fpath)
                if skill._loaded:
                    self.skills.append(skill)
        logger.info(f"[Skill] 共加载 {len(self.skills)} 个技能")

    def get_instructions(self, question: str, tables: List[str] = None) -> str:
        """获取匹配技能的指令文本"""
        parts = []
        for skill in self.skills:
            if skill.matches(question, tables):
                block = skill.get_instruction_block()
                if block:
                    parts.append(block)
        return "\n".join(parts)

    def list_skills(self) -> List[Dict]:
        """列出所有可用技能"""
        return [
            {
                "name": s.name,
                "description": s.description,
                "triggers": s.triggers,
                "table_patterns": s.table_patterns,
            }
            for s in self.skills if s._loaded
        ]
