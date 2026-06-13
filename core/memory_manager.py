"""
==============================================================================
Memory Manager — 跨对话 + 同对话长期记忆（隐私隔离）
==============================================================================
设计思路：
  本模块为系统提供两层记忆存储：
    1. 全局记忆（跨对话）：成功字段映射、修正策略、业务规则偏好
    2. 对话级记忆（同对话）：当前对话内的字段映射、修正记录、偏好

  隐私隔离保障：
    - 只存储抽象映射 {field, display_value, db_value, count}，不存原始问题文本
    - Trace 只存 {error_type, error_pattern, solution}，不存完整 failed_sql
    - 对话级记忆自动 24h TTL 过期
    - clear_conversation() 在对话结束时清除所有会话数据

  存储后端：
    复用 core/cache.py 的 Redis 连接（支持真实 Redis / fakeredis）
==============================================================================
"""

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger("memory_manager")

# ============================================================================
# Redis Key 前缀常量
# ============================================================================

_GLOBAL_FIELD_MAP   = "global:field_map"       # Hash: field|display_value → {"db_value","count"}
_GLOBAL_TRACE       = "global:trace"             # List: 每项 json{"error_type","error_pattern","solution","timestamp"}
_GLOBAL_PREFERENCE  = "global:preference"        # Hash: filter_key=filter_value → count

_SESSION_FIELD_MAP  = "session:{conv_id}:field_map"   # Hash, TTL=24h
_SESSION_TRACE      = "session:{conv_id}:trace"       # List, TTL=24h
_SESSION_PREFERENCE = "session:{conv_id}:preference"  # Hash, TTL=24h

_CONV_TTL_SECONDS = 86400  # 24 小时


def _session_field_key(conv_id: str) -> str:
    """生成对话级字段映射的 Redis key"""
    return f"session:{conv_id}:field_map"


def _session_trace_key(conv_id: str) -> str:
    """生成对话级 Trace 的 Redis key"""
    return f"session:{conv_id}:trace"


def _session_preference_key(conv_id: str) -> str:
    """生成对话级偏好的 Redis key"""
    return f"session:{conv_id}:preference"


# ============================================================================
# MemoryManager
# ============================================================================

class MemoryManager:
    """
    长期记忆管理器。

    提供全局（跨对话）和对话级两层记忆的读写接口。
    所有存储操作通过 Redis 完成，与 core/cache.py 中的 SemanticCache 复用同一连接。

    用法:
        from core.cache import get_cache
        mm = get_cache().memory_manager
        mm.set_global_mapping("status", "已完成", "已完成")
        val = mm.get_global_mapping("status", "已完成")  # → "已完成"
    """

    def __init__(self, redis_client):
        """
        参数:
            redis_client: 从 SemanticCache 获取的 Redis 客户端（真实 Redis / fakeredis）
        """
        self._redis = redis_client
        logger.info("[MemoryManager] 初始化完成")

    # ========================================================================
    # 全局映射 — 跨对话字段/值缓存
    # ========================================================================

    def set_global_mapping(
        self,
        field: str,
        display_value: str,
        db_value: str,
    ) -> None:
        """
        记录一条成功的"口语→字段值"映射到全局缓存。

        隐私设计：
          - 不存储原始用户问题
          - 只存储 {field, display_value, db_value, count}
          - 相同映射重复存入时 count 递增

        参数:
            field: 数据库字段名（如 "status"）
            display_value: 用户在自然语言中使用的值（如 "已完成"）
            db_value: 数据库中实际存储的值（如 "已完成"）
        """
        key = f"{field}|{display_value}"
        try:
            raw = self._redis.hget(_GLOBAL_FIELD_MAP, key)
            if raw:
                entry = json.loads(raw)
                entry["count"] = entry.get("count", 0) + 1
                entry["db_value"] = db_value  # 允许覆盖更新
            else:
                entry = {
                    "db_value": db_value,
                    "count": 1,
                    "first_seen": datetime.now().isoformat(),
                }
            self._redis.hset(_GLOBAL_FIELD_MAP, key, json.dumps(entry, ensure_ascii=False))
            logger.info(
                f"[MemoryManager] 全局映射已记录: {field}='{display_value}' → '{db_value}'"
                f" (累计 {entry['count']} 次)"
            )
        except Exception as e:
            logger.warning(f"[MemoryManager] 写入全局映射失败: {e}")

    def get_global_mapping(self, field: str, display_value: str) -> Optional[str]:
        """
        查询全局字段映射。

        隐私设计：
          仅当映射被成功验证至少 2 次（count >= 2）时才返回，
          确保单次偶然匹配不会造成错误记忆。

        参数:
            field: 数据库字段名
            display_value: 用户在自然语言中使用的值

        返回:
            数据库中的实际值，不存在或 count < 2 返回 None
        """
        key = f"{field}|{display_value}"
        try:
            raw = self._redis.hget(_GLOBAL_FIELD_MAP, key)
            if raw:
                entry = json.loads(raw)
                if entry.get("count", 0) >= 2:
                    return entry["db_value"]
            return None
        except Exception as e:
            logger.warning(f"[MemoryManager] 读取全局映射失败: {e}")
            return None

    def get_all_global_mappings(self) -> List[Dict]:
        """
        获取所有 count >= 2 的全局映射。

        返回:
            [{"field": "status", "display_value": "已完成",
              "db_value": "已完成", "count": 3}, ...]
        """
        mappings = []
        try:
            all_items = self._redis.hgetall(_GLOBAL_FIELD_MAP) or {}
            for composite_key, raw in all_items.items():
                try:
                    entry = json.loads(raw)
                    if entry.get("count", 0) >= 2 and "|" in composite_key:
                        field, display_value = composite_key.split("|", 1)
                        mappings.append({
                            "field": field,
                            "display_value": display_value,
                            "db_value": entry["db_value"],
                            "count": entry["count"],
                        })
                except (json.JSONDecodeError, KeyError):
                    continue
            mappings.sort(key=lambda x: x["count"], reverse=True)
        except Exception as e:
            logger.warning(f"[MemoryManager] 读取全局映射列表失败: {e}")
        return mappings

    # ========================================================================
    # 全局 Trace — 跨对话错误修正策略
    # ========================================================================

    def add_global_trace(
        self,
        error_type: str,
        error_pattern: str,
        solution_abstract: str,
    ) -> None:
        """
        记录一条抽象的错误修正策略到全局 Trace 库。

        隐私设计：
          - 不存储原始对话内容
          - 不存储完整 failed_sql
          - 只存储错误类型、错误模式（如字段名）、抽象修正方案

        参数:
            error_type: "unknown_column" | "syntax_error" | "execution_error"
            error_pattern: 错误的关键特征（如 "字段名 province"）
            solution_abstract: 泛化后的修正方案（如 "WHERE province = <column_value>"）
        """
        entry = {
            "error_type": error_type,
            "error_pattern": error_pattern,
            "solution": solution_abstract,
            "timestamp": datetime.now().isoformat(),
        }
        try:
            self._redis.rpush(_GLOBAL_TRACE, json.dumps(entry, ensure_ascii=False))
            # 限制全局 Trace 条数（最多 200）
            self._redis.ltrim(_GLOBAL_TRACE, -200, -1)
            logger.info(
                f"[MemoryManager] 全局 Trace 已记录: [{error_type}] "
                f"{error_pattern[:60]} → {solution_abstract[:80]}"
            )
        except Exception as e:
            logger.warning(f"[MemoryManager] 写入全局 Trace 失败: {e}")

    def search_global_traces(self, error_message: str, limit: int = 3) -> List[Dict]:
        """
        检索与当前错误消息匹配的全局 Trace。

        使用关键词匹配：将 error_message 分词后与 error_pattern 比对。

        参数:
            error_message: 当前错误消息
            limit: 最大返回条数

        返回:
            [{"error_type": ..., "error_pattern": ..., "solution": ..., "timestamp": ...}, ...]
        """
        if not error_message:
            return []
        try:
            trace_raws = self._redis.lrange(_GLOBAL_TRACE, 0, -1) or []
        except Exception:
            return []

        # 提取 error_message 中的关键词（字段名、表名等）
        keywords = self._extract_keywords(error_message)
        if not keywords:
            return []

        matches = []
        for raw in trace_raws:
            try:
                entry = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                pattern = entry.get("error_pattern", "")
                # 判断是否有关键词匹配
                if any(kw.lower() in pattern.lower() for kw in keywords):
                    matches.append(entry)
            except (json.JSONDecodeError, AttributeError):
                continue

        matches.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return matches[:limit]

    # ========================================================================
    # 全局偏好 — 跨对话业务规则积累
    # ========================================================================

    def add_global_preference(self, filter_key: str, filter_value: str) -> None:
        """
        记录一条全局业务规则偏好（如 status='已完成' 被重复使用）。

        参数:
            filter_key: 过滤字段名（如 "status"）
            filter_value: 过滤值（如 "已完成"）
        """
        composite = f"{filter_key}={filter_value}"
        try:
            count = self._redis.hincrby(_GLOBAL_PREFERENCE, composite, 1)
            logger.info(
                f"[MemoryManager] 全局偏好已记录: {composite}"
                f" (累计 {count} 次)"
            )
        except Exception as e:
            logger.warning(f"[MemoryManager] 写入全局偏好失败: {e}")

    def get_global_preferences(self, min_count: int = 2) -> List[Dict]:
        """
        获取高频全局偏好。

        参数:
            min_count: 最低出现次数（默认 2，避免单次偶然匹配）

        返回:
            [{"filter_key": "status", "filter_value": "已完成", "count": 3}, ...]
        """
        preferences = []
        try:
            all_items = self._redis.hgetall(_GLOBAL_PREFERENCE) or {}
            for composite, count_str in all_items.items():
                count = int(count_str) if count_str else 0
                if count >= min_count and "=" in composite:
                    key, val = composite.split("=", 1)
                    preferences.append({
                        "filter_key": key,
                        "filter_value": val,
                        "count": count,
                    })
            preferences.sort(key=lambda x: x["count"], reverse=True)
        except Exception as e:
            logger.warning(f"[MemoryManager] 读取全局偏好失败: {e}")
        return preferences

    # ========================================================================
    # 对话级映射 — 同对话字段/值缓存
    # ========================================================================

    def set_conv_mapping(
        self,
        conv_id: str,
        field: str,
        display_value: str,
        db_value: str,
    ) -> None:
        """
        记录当前对话内的字段映射。

        参数:
            conv_id: 对话唯一标识
            field: 数据库字段名
            display_value: 用户口语化值
            db_value: 数据库实际值
        """
        key = f"{field}|{display_value}"
        entry = {"db_value": db_value, "count": 1, "timestamp": datetime.now().isoformat()}
        try:
            rkey = _session_field_key(conv_id)
            # 如果已有，递增 count
            existing = self._redis.hget(rkey, key)
            if existing:
                try:
                    existing_entry = json.loads(existing)
                    existing_entry["count"] = existing_entry.get("count", 0) + 1
                    existing_entry["db_value"] = db_value
                    entry = existing_entry
                except json.JSONDecodeError:
                    pass
            self._redis.hset(rkey, key, json.dumps(entry, ensure_ascii=False))
            self._redis.expire(rkey, _CONV_TTL_SECONDS)
            logger.info(
                f"[MemoryManager] 对话映射已记录 [{conv_id[:12]}]: "
                f"{field}='{display_value}' → '{db_value}'"
            )
        except Exception as e:
            logger.warning(f"[MemoryManager] 写入对话映射失败: {e}")

    def get_conv_mapping(self, conv_id: str, field: str, display_value: str) -> Optional[str]:
        """
        查询当前对话内的字段映射。

        参数:
            conv_id: 对话唯一标识
            field: 数据库字段名
            display_value: 用户口语化值

        返回:
            数据库实际值，不存在返回 None
        """
        key = f"{field}|{display_value}"
        try:
            raw = self._redis.hget(_session_field_key(conv_id), key)
            if raw:
                entry = json.loads(raw)
                return entry["db_value"]
            return None
        except Exception as e:
            logger.warning(f"[MemoryManager] 读取对话映射失败: {e}")
            return None

    def get_all_conv_mappings(self, conv_id: str) -> Dict[str, str]:
        """
        获取当前对话的所有字段映射。

        返回:
            {"field|display_value": "db_value", ...}
        """
        result = {}
        try:
            all_items = self._redis.hgetall(_session_field_key(conv_id)) or {}
            for composite_key, raw in all_items.items():
                try:
                    entry = json.loads(raw)
                    result[composite_key] = entry["db_value"]
                except (json.JSONDecodeError, KeyError):
                    continue
        except Exception as e:
            logger.warning(f"[MemoryManager] 读取对话映射列表失败: {e}")
        return result

    # ========================================================================
    # 对话级 Trace
    # ========================================================================

    def add_conv_trace(
        self,
        conv_id: str,
        error_type: str,
        error_pattern: str,
        solution_abstract: str,
    ) -> None:
        """
        记录当前对话内的错误修正记录。

        参数:
            conv_id: 对话唯一标识
            error_type: 错误类型
            error_pattern: 错误特征（如字段名）
            solution_abstract: 泛化修正方案
        """
        entry = {
            "error_type": error_type,
            "error_pattern": error_pattern,
            "solution": solution_abstract,
            "timestamp": datetime.now().isoformat(),
        }
        try:
            rkey = _session_trace_key(conv_id)
            self._redis.rpush(rkey, json.dumps(entry, ensure_ascii=False))
            self._redis.expire(rkey, _CONV_TTL_SECONDS)
            # 限制条数
            self._redis.ltrim(rkey, -50, -1)
        except Exception as e:
            logger.warning(f"[MemoryManager] 写入对话 Trace 失败: {e}")

    def search_conv_traces(self, conv_id: str, error_message: str, limit: int = 3) -> List[Dict]:
        """
        检索当前对话内匹配的错误修正。

        参数:
            conv_id: 对话唯一标识
            error_message: 当前错误消息
            limit: 最大返回条数

        返回:
            [{"error_type": ..., "error_pattern": ..., "solution": ..., "timestamp": ...}, ...]
        """
        if not conv_id or not error_message:
            return []
        try:
            trace_raws = self._redis.lrange(_session_trace_key(conv_id), 0, -1) or []
        except Exception:
            return []

        keywords = self._extract_keywords(error_message)
        if not keywords:
            return []

        matches = []
        for raw in trace_raws:
            try:
                entry = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                if any(kw.lower() in entry.get("error_pattern", "").lower() for kw in keywords):
                    matches.append(entry)
            except (json.JSONDecodeError, AttributeError):
                continue

        matches.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return matches[:limit]

    # ========================================================================
    # 对话级偏好
    # ========================================================================

    def record_conv_preference(self, conv_id: str, filter_key: str, filter_value: str) -> None:
        """
        记录当前对话中使用的过滤条件。

        参数:
            conv_id: 对话唯一标识
            filter_key: 过滤字段名
            filter_value: 过滤值
        """
        composite = f"{filter_key}={filter_value}"
        try:
            rkey = _session_preference_key(conv_id)
            self._redis.hincrby(rkey, composite, 1)
            self._redis.expire(rkey, _CONV_TTL_SECONDS)
        except Exception as e:
            logger.warning(f"[MemoryManager] 记录对话偏好失败: {e}")

    def get_conv_preferences(self, conv_id: str) -> List[Dict]:
        """
        获取当前对话内的偏好。

        返回:
            [{"filter_key": "status", "filter_value": "已完成", "count": 2}, ...]
        """
        preferences = []
        try:
            all_items = self._redis.hgetall(_session_preference_key(conv_id)) or {}
            for composite, count_str in all_items.items():
                count = int(count_str) if count_str else 0
                if "=" in composite:
                    key, val = composite.split("=", 1)
                    preferences.append({
                        "filter_key": key,
                        "filter_value": val,
                        "count": count,
                    })
        except Exception as e:
            logger.warning(f"[MemoryManager] 读取对话偏好失败: {e}")
        return preferences

    # ========================================================================
    # 提升与清理
    # ========================================================================

    def promote_to_global(self, conv_id: str) -> int:
        """
        将当前对话中的高频有效规则提升为全局知识。

        提升规则：
          - count >= 2 的字段映射 → 提升为全局映射（重置 count = 2）
          - count >= 2 的偏好 → 提升为全局偏好

        参数:
            conv_id: 对话唯一标识

        返回:
            提升的条目数
        """
        promoted = 0

        # 1. 提升字段映射
        try:
            conv_mappings = self._redis.hgetall(_session_field_key(conv_id)) or {}
            for composite_key, raw in conv_mappings.items():
                try:
                    entry = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())
                    if entry.get("count", 0) >= 2 and "|" in composite_key:
                        field, display_value = composite_key.split("|", 1)
                        # 以 count=2 写入全局（代表已验证至少 2 次）
                        global_raw = self._redis.hget(_GLOBAL_FIELD_MAP, composite_key)
                        if global_raw:
                            global_entry = json.loads(global_raw)
                            global_entry["count"] = global_entry.get("count", 0) + entry["count"]
                            global_entry["db_value"] = entry["db_value"]
                        else:
                            global_entry = {
                                "db_value": entry["db_value"],
                                "count": 2,
                                "first_seen": datetime.now().isoformat(),
                            }
                        self._redis.hset(
                            _GLOBAL_FIELD_MAP,
                            composite_key,
                            json.dumps(global_entry, ensure_ascii=False),
                        )
                        promoted += 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        except Exception as e:
            logger.warning(f"[MemoryManager] 提升映射失败: {e}")

        # 2. 提升偏好
        try:
            conv_prefs = self._redis.hgetall(_session_preference_key(conv_id)) or {}
            for composite, count_str in conv_prefs.items():
                count = int(count_str) if count_str else 0
                if count >= 2 and "=" in composite:
                    self._redis.hincrby(_GLOBAL_PREFERENCE, composite, count)
                    promoted += 1
        except Exception as e:
            logger.warning(f"[MemoryManager] 提升偏好失败: {e}")

        if promoted > 0:
            logger.info(f"[MemoryManager] 对话 [{conv_id[:12]}] 提升 {promoted} 条规则至全局")
        return promoted

    def clear_conversation(self, conv_id: str) -> None:
        """
        清除指定对话的所有记忆数据。

        在以下场景调用：
          - 用户在历史侧栏切换对话
          - 用户删除对话
          - 对话自然过期前主动清理

        参数:
            conv_id: 对话唯一标识
        """
        try:
            keys_to_delete = [
                _session_field_key(conv_id),
                _session_trace_key(conv_id),
                _session_preference_key(conv_id),
            ]
            for key in keys_to_delete:
                self._redis.delete(key)
            logger.info(f"[MemoryManager] 已清除对话记忆: [{conv_id[:12]}]")
        except Exception as e:
            logger.warning(f"[MemoryManager] 清除对话记忆失败: {e}")

    # ========================================================================
    # 组合记忆上下文 — 供 Generator Prompt 注入
    # ========================================================================

    def build_memory_context(self, query: str, conv_id: Optional[str] = None) -> Dict[str, str]:
        """
        为 Generator 构建完整的记忆上下文。

        同时检索全局和对话级记忆，返回可直接注入 Prompt 的文本块。

        参数:
            query: 用户当前问题（用于匹配 Trace）
            conv_id: 当前对话 ID（可选，仅在对话内有值）

        返回:
            {
                "field_hints": "【已识别字段映射】...",
                "trace_hints": "【历史修正参考】...",
                "preference_hints": "【常用查询模式】...",
            }
        """
        context = {
            "field_hints": "",
            "trace_hints": "",
            "preference_hints": "",
        }

        # ---- 1. 字段映射提示 ----
        field_parts = []

        # 全局映射
        global_mappings = self.get_all_global_mappings()
        for m in global_mappings:
            field_parts.append(f"- {m['field']}: '{m['display_value']}' → '{m['db_value']}'")

        # 对话级映射
        if conv_id:
            conv_mappings = self.get_all_conv_mappings(conv_id)
            for composite_key, db_value in conv_mappings.items():
                if "|" in composite_key:
                    field, display_value = composite_key.split("|", 1)
                    # 避免与全局重复
                    if not any(
                        m["field"] == field and m["display_value"] == display_value
                        for m in global_mappings
                    ):
                        field_parts.append(f"- {field}: '{display_value}' → '{db_value}'")

        if field_parts:
            context["field_hints"] = "【已识别字段映射】\n" + "\n".join(field_parts)

        # ---- 2. Trace 提示 ----
        trace_parts = []

        # 全局 Trace
        global_traces = self.search_global_traces(query)
        for t in global_traces:
            trace_parts.append(
                f"- [{t.get('error_type', '?')}] {t.get('error_pattern', '')} "
                f"→ {t.get('solution', '')}"
            )

        # 对话级 Trace
        if conv_id:
            conv_traces = self.search_conv_traces(conv_id, query)
            for t in conv_traces:
                # 避免与全局重复
                if not any(
                    g.get("error_pattern") == t.get("error_pattern")
                    for g in global_traces
                ):
                    trace_parts.append(
                        f"- [{t.get('error_type', '?')}] {t.get('error_pattern', '')} "
                        f"→ {t.get('solution', '')}"
                    )

        if trace_parts:
            context["trace_hints"] = "【历史修正参考】\n" + "\n".join(trace_parts)

        # ---- 3. 偏好提示 ----
        pref_parts = []

        global_prefs = self.get_global_preferences(min_count=2)
        for p in global_prefs:
            pref_parts.append(f"- {p['filter_key']} = '{p['filter_value']}' (已使用 {p['count']} 次)")

        if conv_id:
            conv_prefs = self.get_conv_preferences(conv_id)
            for p in conv_prefs:
                if p["count"] >= 1 and not any(
                    gp["filter_key"] == p["filter_key"] and gp["filter_value"] == p["filter_value"]
                    for gp in global_prefs
                ):
                    pref_parts.append(f"- {p['filter_key']} = '{p['filter_value']}'（当前对话已使用 {p['count']} 次）")

        if pref_parts:
            context["preference_hints"] = "【常用查询模式】\n" + "\n".join(pref_parts)

        return context

    # ========================================================================
    # 内部工具
    # ========================================================================

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """
        从错误消息或查询文本中提取关键词。

        提取规则：
          - "Unknown column 'xxx'" → 提取 xxx
          - 一般文本：提取被引号包裹的标识符 + 非停用词
        """
        keywords = []

        # 提取引号包裹的内容
        quoted = re.findall(r"['`]\s*(\w+)\s*['`]", text)
        keywords.extend(quoted)

        # 提取简单的英文单词（表名、列名）
        words = re.findall(r"\b([a-zA-Z_]\w{2,})\b", text)
        keywords.extend(words)

        # 去重
        seen = set()
        result = []
        for kw in keywords:
            lower = kw.lower()
            if lower not in seen and len(lower) >= 2:
                seen.add(lower)
                result.append(kw)
        return result
