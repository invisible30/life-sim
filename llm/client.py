"""LLM 客户端封装

支持 OpenAI 兼容接口（MiniMax / OpenAI / 其他）和 Anthropic。
带指数退避重试和超时控制。
"""
from __future__ import annotations

import os
import asyncio
import time
import random
import logging
from dataclasses import dataclass, field
from typing import Any

import openai
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


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
    # Anthropic-only
    anthropic_base_url: str = ""  # 默认用官方, 可走 proxy


class LLMClient:
    """统一 LLM 客户端（带重试）"""

    def __init__(self, cfg: LLMConfig | None = None):
        if cfg is None:
            cfg = self._load_from_env()
        self.cfg = cfg
        self.call_count = 0
        self.retry_count = 0  # 总重试次数
        self.failure_count = 0  # 永久失败次数
        self._client: openai.AsyncOpenAI | Any = None
        self._budget_lock = asyncio.Lock()  # issue #15: 守护 max_total_calls 增量
        self._init_client()

    @staticmethod
    def _load_from_env() -> LLMConfig:
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
                anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", ""),
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
        elif self.cfg.provider == "anthropic":
            import anthropic
            kwargs = {
                "api_key": self.cfg.api_key,
                "timeout": self.cfg.request_timeout,
                "max_retries": 0,  # 我们自己实现重试
            }
            if self.cfg.anthropic_base_url:
                kwargs["base_url"] = self.cfg.anthropic_base_url
            self._client = anthropic.AsyncAnthropic(**kwargs)

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
            # 没有 openai SDK, 走通用网络层判断
            pass
        # Anthropic SDK 异常
        try:
            import anthropic
            if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
                return True
            if isinstance(exc, anthropic.RateLimitError):
                return True
            if isinstance(exc, anthropic.InternalServerError):
                return True
            if isinstance(exc, anthropic.APIStatusError):
                # 5xx 重试; 4xx 中只有 408/429 重试
                if exc.status_code >= 500:
                    return True
                if exc.status_code in (408, 429):
                    return True
                return False
        except ImportError:
            # 没有 anthropic SDK 也走通用网络层判断
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

    async def _chat_anthropic_once(
        self,
        system: str,
        user: str,
        temperature: float | None,
        max_tokens: int | None,
    ) -> str:
        """单次请求（不重试）"""
        assert self._client is not None
        resp = await self._client.messages.create(
            model=self.cfg.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=temperature if temperature is not None else self.cfg.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.cfg.max_tokens,
        )
        # resp.content is a list of content blocks; concatenate text blocks
        parts = []
        for block in resp.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts).strip()

    async def _chat_with_retry(
        self,
        once_fn,
        system: str,
        user: str,
        temperature: float | None,
        max_tokens: int | None,
    ) -> str:
        """通用指数退避重试, 适配 OpenAI / Anthropic 两种 provider

        注意: call_count 已在 chat() 入口 reserve 过了, 这里不重复 +1
        (issue #15: 改用预订式 reserve, 重试失败也算 budget 是 by design)
        """
        last_exc: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            try:
                result = await once_fn(system, user, temperature, max_tokens)
                if attempt > 0:
                    logger.info("↻ LLM 重试 %d 次后成功 (累计 %d calls)", attempt, self.call_count)
                return result
            except Exception as e:
                last_exc = e
                retryable = self._is_retryable(e)
                if not retryable or attempt >= self.cfg.max_retries:
                    self.failure_count += 1
                    if retryable:
                        logger.error("✗ LLM 重试 %d 次后仍失败: %s: %s",
                                     self.cfg.max_retries, type(e).__name__, e)
                    else:
                        logger.error("✗ LLM 不可重试错误: %s: %s", type(e).__name__, e)
                    return f"[LLM_ERROR] {type(e).__name__}: {e}"

                # 默认 backoff: 指数 + jitter
                delay = min(
                    self.cfg.retry_max_delay,
                    self.cfg.retry_base_delay * (2 ** attempt),
                )
                delay = delay * (0.5 + random.random() * 0.5)

                # issue #17: 如果服务器返回 Retry-After, 用它当 minimum delay
                retry_after = self._parse_retry_after(e)
                if retry_after is not None:
                    delay = max(delay, min(retry_after, self.cfg.retry_max_delay))
                    logger.info("↻ 收到 Retry-After: %ss, 实际 wait %.1fs", retry_after, delay)

                self.retry_count += 1
                logger.info("↻ LLM 调用失败 (%s: %s), %.1fs 后重试 (%d/%d)",
                            type(e).__name__, str(e)[:60], delay,
                            attempt + 1, self.cfg.max_retries)
                await asyncio.sleep(delay)
        return f"[LLM_ERROR] {type(last_exc).__name__}: {last_exc}" if last_exc else "[LLM_ERROR] unknown"

    @staticmethod
    def _parse_retry_after(exc: Exception) -> float | None:
        """从 429/503 响应里抽 Retry-After header, 返回秒数.

        支持两种格式:
        - delta-seconds: "30" (秒)
        - HTTP-date: "Wed, 21 Oct 2026 07:28:00 GMT" (绝对时间)

        Returns None 如果 header 不存在 / 解析失败.
        """
        resp = getattr(exc, "response", None)
        if resp is None:
            return None
        headers = getattr(resp, "headers", None)
        if headers is None:
            return None
        # httpx.Headers 是 case-insensitive, 但保险起见两种都查
        raw = headers.get("retry-after") if hasattr(headers, "get") else None
        if raw is None:
            return None
        raw = raw.strip()
        # 1) 纯数字 -> 秒
        try:
            return float(raw)
        except ValueError:
            pass
        # 2) HTTP-date
        try:
            from email.utils import parsedate_to_datetime
            from datetime import datetime, timezone
            target = parsedate_to_datetime(raw)
            if target is None:
                return None
            now = datetime.now(timezone.utc)
            delta = (target - now).total_seconds()
            return max(0.0, delta)
        except Exception:
            return None

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """单轮对话，返回 assistant 文本"""
        # issue #15: budget 预订式 reserve. check + 增量 在同一临界区, 然后才
        # 放锁让其他 coroutine 进来. 如果 20 个并发都到达这里, 锁会序列化它们,
        # 只有前 max_total_calls 个能 reserve 成功.
        # 失败的请求(LLM_ERROR)也会被算入 budget, 这是 by design — 拒绝重试无限制.
        async with self._budget_lock:
            if self.call_count >= self.cfg.max_total_calls:
                return "[LLM_CALL_LIMIT_REACHED] 达到调用上限，跳过此次生成"
            self.call_count += 1  # 立即 reserve, 跟 check 是同一个临界区

        if self.cfg.provider == "openai":
            return await self._chat_with_retry(
                self._chat_openai_once, system, user, temperature, max_tokens
            )
        if self.cfg.provider == "anthropic":
            return await self._chat_with_retry(
                self._chat_anthropic_once, system, user, temperature, max_tokens
            )
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
