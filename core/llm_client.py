"""
==============================================================================
LLM 客户端封装 — 所有模型调用的唯一入口
==============================================================================
设计思路：
  本模块提供统一的云端 API 调用接口（OpenAI 兼容格式）。
  所有 Agent 必须通过 BaseLLMClient 调用模型，禁止直接使用 requests。

  支持的调用方式：
    - generate(prompt) → 纯文本生成
    - generate(prompt, system_prompt) → 带系统提示的对话生成
    - generate_json(prompt) → 调用并解析 JSON 响应

  所有调用自动处理：连接重试、超时、日志记录、异常包装。
==============================================================================
"""

import json
import logging
import os
import re
import time
from typing import Optional

import httpx

logger = logging.getLogger("llm_client")


class BaseLLMClient:
    """
    LLM 调用的统一基类。

    封装 OpenAI 兼容 API，提供同步 generate 接口。
    所有业务代码必须通过此类调用模型，不得自行实现底层 HTTP 请求。

    用法:
        client = BaseLLMClient(
            model="deepseek-v4-flash",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="sk-xxx",
        )
        reply = client.generate("你好", system_prompt="你是一名助手")
    """

    def __init__(
        self,
        model: str,
        base_url: str = "",
        api_key: str = "",
        name: str = "",
        timeout: int = 120,
        max_retries: int = 2,
    ):
        """
        初始化 LLM 客户端。

        参数:
            model: 模型名称（如 "deepseek-v4-flash", "glm-4-flash"）
            base_url: API 地址，默认从 OPENAI_BASE_URL 环境变量读取
            api_key: API Key，默认从 OPENAI_API_KEY 环境变量读取
            name: 客户端名称（用于日志区分）
            timeout: 单次请求超时秒数，默认 120 秒
            max_retries: 失败重试次数，默认 2 次
        """
        self.model = model
        self.name = name or model
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (
            base_url or os.getenv("OPENAI_BASE_URL", "")
        ).rstrip("/")

        if not self.base_url:
            self.base_url = "https://api.openai.com/v1"
        if not self.api_key:
            logger.warning(
                f"[LLMClient][{self.name}] OPENAI_API_KEY 未设置"
            )

        self._chat_url = f"{self.base_url}/chat/completions"
        logger.info(
            f"[LLMClient] 初始化: name={self.name}, "
            f"model={model}, base_url={self.base_url}"
        )

    def generate(self, prompt: str, system_prompt: str = "", timeout: int = None) -> str:
        """
        调用模型生成文本。

        参数:
            prompt: 用户输入提示
            system_prompt: 系统角色设定（可选）
            timeout: 请求超时秒数（None 则使用实例默认值）

        返回:
            模型生成的文本字符串

        异常:
            RuntimeError: 所有重试耗尽后仍失败时抛出
        """
        last_error = ""
        req_timeout = timeout if timeout is not None else self.timeout

        for attempt in range(self.max_retries + 1):
            try:
                start_time = time.time()

                messages = []
                if system_prompt:
                    messages.append(
                        {"role": "system", "content": system_prompt}
                    )
                messages.append({"role": "user", "content": prompt})

                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 2048,
                }

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }

                with httpx.Client(timeout=req_timeout) as client:
                    response = client.post(
                        self._chat_url, json=payload, headers=headers
                    )
                    response.raise_for_status()
                    data = response.json()

                result = data["choices"][0]["message"]["content"]
                elapsed = time.time() - start_time

                logger.info(
                    f"[LLMClient][{self.name}] 生成完成 "
                    f"({elapsed:.2f}s, {len(result)} 字符)"
                )
                return result.strip()

            except httpx.TimeoutException as e:
                last_error = f"超时: {e}"
                logger.warning(
                    f"[LLMClient][{self.name}] "
                    f"第 {attempt + 1} 次请求超时: {e}"
                )
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}: {e}"
                logger.warning(
                    f"[LLMClient][{self.name}] "
                    f"第 {attempt + 1} 次请求失败: {e}"
                )
            except Exception as e:
                last_error = str(e)
                logger.error(
                    f"[LLMClient][{self.name}] "
                    f"第 {attempt + 1} 次未知错误: {e}"
                )

            if attempt < self.max_retries:
                wait = (attempt + 1) * 2
                logger.info(
                    f"[LLMClient][{self.name}] 等待 {wait}s 后重试..."
                )
                time.sleep(wait)

        error_msg = f"LLM [{self.name}] 所有重试均已失败: {last_error}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    def generate_json(
        self, prompt: str, system_prompt: str = ""
    ) -> Optional[dict]:
        """
        调用模型并解析 JSON 响应。

        参数:
            prompt: 用户输入提示
            system_prompt: 系统角色设定（可选）

        返回:
            解析后的 JSON 字典，解析失败返回 None
        """
        text = self.generate(prompt, system_prompt)
        if not text:
            return None

        # 尝试从 ```json ... ``` 代码块中提取
        json_match = re.search(
            r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", text, re.DOTALL
        )
        if json_match:
            text = json_match.group(1)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
            logger.warning(
                f"[LLMClient][{self.name}] JSON 解析失败: {text[:200]}"
            )
            return None

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """估算文本的 Token 数（中文 1.5 token/字，英文 0.4 token/字母）"""
        chinese_chars = len(re.findall(r"[一-鿿]", text))
        english_chars = len(re.findall(r"[a-zA-Z]", text))
        return int(
            chinese_chars * 1.5 + english_chars * 0.4 + len(text) * 0.1
        )
