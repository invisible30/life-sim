"""LLM 客户端封装

支持 OpenAI 兼容接口（MiniMax / OpenAI / 其他）和 Anthropic。
"""
from __future__ import annotations

import os
import asyncio
import time
from dataclasses import dataclass
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


class LLMClient:
    """统一 LLM 客户端"""
    
    def __init__(self, cfg: LLMConfig | None = None):
        if cfg is None:
            cfg = self._load_from_env()
        self.cfg = cfg
        self.call_count = 0
        self._client: openai.AsyncOpenAI | None = None
        self._init_client()
    
    def _load_from_env(self) -> LLMConfig:
        provider = os.getenv("LLM_PROVIDER", "openai").lower()
        if provider == "openai":
            return LLMConfig(
                provider="openai",
                model=os.getenv("OPENAI_MODEL", "MiniMax-M2.7-highspeed"),
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.minimaxi.com/v1"),
                api_key=os.getenv("OPENAI_API_KEY", ""),
                temperature=float(os.getenv("LIFE_TEMPERATURE", "0.8")),
                max_total_calls=int(os.getenv("LIFE_MAX_LLM_CALLS", "800")),
            )
        elif provider == "anthropic":
            return LLMConfig(
                provider="anthropic",
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
                api_key=os.getenv("ANTHROPIC_API_KEY", ""),
                temperature=float(os.getenv("LIFE_TEMPERATURE", "0.8")),
                max_total_calls=int(os.getenv("LIFE_MAX_LLM_CALLS", "800")),
            )
        raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
    
    def _init_client(self):
        if self.cfg.provider == "openai":
            self._client = openai.AsyncOpenAI(
                api_key=self.cfg.api_key,
                base_url=self.cfg.base_url,
            )
        # anthropic 留给后续
    
    @property
    def remaining_calls(self) -> int:
        return self.cfg.max_total_calls - self.call_count
    
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
    
    async def _chat_openai(
        self,
        system: str,
        user: str,
        temperature: float | None,
        max_tokens: int | None,
    ) -> str:
        assert self._client is not None
        try:
            resp = await self._client.chat.completions.create(
                model=self.cfg.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature if temperature is not None else self.cfg.temperature,
                max_tokens=max_tokens if max_tokens is not None else self.cfg.max_tokens,
            )
            self.call_count += 1
            content = resp.choices[0].message.content or ""
            return content.strip()
        except Exception as e:
            return f"[LLM_ERROR] {type(e).__name__}: {e}"
    
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
