"""Runtime configuration, read once from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    provider: str = "auto"  # "auto" (Vultr if key set, else Gemini), "vultr", or "gemini"
    vultr_base_url: str = "https://api.vultrinference.com/v1"
    vultr_api_key: str = ""
    vultr_model: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    sandbox_image: str = "glassbox-sandbox:latest"
    sandbox_runtime: str | None = None  # "runsc" enables gVisor
    sandbox_timeout_s: int = 25
    sandbox_memory: str = "512m"
    max_upload_bytes: int = 2_000_000
    max_attempts: int = 3
    max_concurrent: int = 2
    rate_limit_per_min: int = 12

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        return cls(
            provider=e.get("LLM_PROVIDER", "auto"),
            vultr_base_url=e.get("VULTR_BASE_URL", cls.vultr_base_url),
            vultr_api_key=e.get("VULTR_INFERENCE_API_KEY", ""),
            vultr_model=e.get("VULTR_MODEL", cls.vultr_model),
            gemini_api_key=e.get("GEMINI_API_KEY") or e.get("GOOGLE_API_KEY", ""),
            gemini_model=e.get("DS_LLM_MODEL") or e.get("GEMINI_MODEL") or cls.gemini_model,
            sandbox_image=e.get("SANDBOX_IMAGE", cls.sandbox_image),
            sandbox_runtime=e.get("SANDBOX_RUNTIME") or None,
            sandbox_timeout_s=int(e.get("SANDBOX_TIMEOUT_S", "25")),
            sandbox_memory=e.get("SANDBOX_MEMORY", "512m"),
            max_upload_bytes=int(e.get("MAX_UPLOAD_BYTES", "2000000")),
            max_attempts=int(e.get("MAX_ATTEMPTS", "3")),
            max_concurrent=int(e.get("MAX_CONCURRENT", "2")),
            rate_limit_per_min=int(e.get("RATE_LIMIT_PER_MIN", "12")),
        )
