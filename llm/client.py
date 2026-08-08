"""LLM 客户端封装

支持 OpenAI 兼容接口（MiniMax / OpenAI / 其他）和 Anthropic。
带指数退避重试和超时控制。
"""
from __future__ import annotations

import os
import asyncio
import time
import random
from dataclasses import dataclass, field
from typing import Any

import openai
from dotenv import load_dotenv

load_dotenv()


@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "MiniMax-M2.7-highspeed"
    base_url: str = "https://api.minimaxi.com/v1"
    api_key: str = ""
    temperature: float = 0.8
    max_tokens: int = 1024
    max_total_calls: int = 800  # 整个人生的 LLM 调用上限
    # 重试参数
    max_retries: int = 4
    retry_base_delay: float = 1.5  # 第一次重试等待秒数
    retry_max_delay: float = 30.0
    request_timeout: float = 90.0  # 单次请求 timeout


class LLMClient:
    """统一 LLM 客户端（带重试）"""

    def __init__(self, cfg: LLMConfig | None = None):
        if cfg is None:
            cfg = self._load_from_env()
        self.cfg = cfg
        self.call_count = 0
        self.retry_count = 0  # 总重试次数
        self.failure_count = 0  # 永久失败次数
        self._client: openai.AsyncOpenAI | None = None
        self._init_client()

    def _load_from_env(self) -> LLMConfig:
        provider = os.getenv("LLM_PROVIDER", "openai").lower()
        retry_count = int(os.getenv("LIFE_LLM_MAX_RETRIES", "4"))
        timeout = float(os.getenv("LIFE_LLM_TIMEOUT", "90"))
        if provider == "openai":
            return LLMConfig(
                provider="openai",
                model=os.getenv("OPENAI_MODEL", "MiniMax-M2.7-highspeed"),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1"),
                api_key=os.getenv("OPENAI_API_KEY", ""),
                temperature=float(os.getenv("LIFE_TEMPERATURE", "0.8")),
                max_total_calls=int(os.getenv("LIFE_MAX_LLM_CALLS", "800")),
                max_retries=retry_count,
                request_timeout=timeout,
            )
        elif provider == "anthropic":
            return LLMConfig(
                provider="anthropic",
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
                api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                temperature=float(os.getenv("LIFE_TEMPERATURE", "0.8")),
                max_total_calls=int(os.getenv("LIFE_MAX_LLM_CALLS", "800")),
                max_retries=retry_count,
                request_timeout=timeout,
            )
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")

    def _init_client(self):
        if self.cfg.provider == "openai":
            self._client = openai.AsyncOpenAI(
                api_key=self.cfg.api_key,
                base_url=self.cfg.base_url,
                timeout=self.cfg.request_timeout,
                max_retries=0,  # 我们自己实现重试
            )

    @property
    def remaining_calls(self) -> int:
        return self.cfg.max_total_calls - self.call_count

    def _is_retryable(self, exc: Exception) -> bool:
        """判断异常是否可重试"""
        # OpenAI SDK 异常
        try:
            import openai
            if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
                return True
            if isinstance(exc, openai.RateLimitError):
                return True
            if isinstance(exc, openai.InternalServerError):
                return True
            if isinstance(exc, openai.APIStatusError):
                # 5xx 重试，4xx 不重试
                return exc.status_code >= 500
        except ImportError:
            pass
        # 网络层异常
        msg = str(exc).lower()
        if any(k in msg for k in ("timeout", "timed out", "connection", "reset", "broken pipe")):
            return True
        # 4xx 客户端错误不重试
        return False

    async def _chat_openai_once(
        self,
        system: str,
        user: str,
        temperature: float | None,
        max_tokens: int | None,
    ) -> str:
        """单次请求（不重试）"""
        assert self._client is not None
        resp = await self._client.chat.completions.create(
            model=self.cfg.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature if temperature is not None else self.cfg.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.cfg.max_tokens,
        )
        content = resp.choices[0].message.content or ""
        return content.strip()

    async def _chat_openai(
        self,
        system: str,
        user: str,
        temperature: float | None,
        max_tokens: int | None,
    ) -> str:
        """带指数退避的重试"""
        last_exc: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                result = await self._chat_openai_once(system, user, temperature, max_tokens)
                self.call_count += 1
                if attempt > 0:
                    # 重试成功，log 一下
                    print(f"  ↻ LLM 重试 {attempt} 次后成功 (累计 {self.call_count} calls)", flush=True)
                return result
            except Exception as e:
                last_exc = e
                retryable = self._is_retryable(e)
                if not retryable or attempt >= self.cfg.max_retries:
                    # 不可重试 OR 达到最大重试次数
                    self.failure_count += 1
                    if retryable:
                        print(f"  ✗ LLM 重试 {self.cfg.max_retries} 次后仍失败: {type(e).__name__}: {e}", flush=True)
                    else:
                        print(f"  ✗ LLM 不可重试错误: {type(e).__name__}: {e}", flush=True)
                    return f"[LLM_ERROR] {type(e).__name__}: {e}"
                # 指数退避
                delay = min(
                    self.cfg.retry_max_delay,
                    self.cfg.retry_base_delay * (2 ** attempt),
                )
                # 加 jitter 避免雷鸣
                delay = delay * (0.5 + random.random() * 0.5)
                self.retry_count += 1
                print(f"  ↻ LLM 调用失败 ({type(e).__name__}: {str(e)[:60]}),"
                      f" {delay:.1f}s 后重试 ({attempt + 1}/{self.cfg.max_retries})", flush=True)
                await asyncio.sleep(delay)
        # 不应该到这里
        return f"[LLM_ERROR] {type(last_exc).__name__}: {last_exc}" if last_exc else "[LLM_ERROR] unknown"

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """单轮对话，返回 assistant 文本"""
        if self.call_count >= self.cfg.max_total_calls:
            return "[LLM_CALL_LIMIT_REACHED] 达到调用上限，跳过此次生成"

        if self.cfg.provider == "openai":
            return await self._chat_openai(system, user, temperature, max_tokens)
        raise NotImplementedError(f"Provider {self.cfg.provider} not implemented")

    async def chat_batch(
        self,
        system: str,
        user_list: list[str],
        *,
        max_concurrent: int = 4,
    ) -> list[str]:
        """并发调用"""
        sem = asyncio.Semaphore(max_concurrent)

        async def _one(u: str) -> str:
            async with sem:
                return await self.chat(system, u)

        return await asyncio.gather(*[_one(u) for u in user_list])
