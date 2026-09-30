"""
Jalani Control Tower — Configuration
Loads all settings from environment variables with sensible defaults.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Simulator
    simulator_url: str = "http://localhost:8000"

    # Database
    database_path: str = "./data/jalani.db"

    # Intelligence Service
    intelligence_url: str = "http://localhost:8081"

    # Logging
    log_level: str = "INFO"

    # LLM (optional)
    llm_enabled: bool = False
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o-mini"

    # Polling
    poll_interval_ms: int = 50  # How often to check for new ticks

    # Resilience
    circuit_breaker_threshold: int = 5
    circuit_breaker_recovery_s: int = 30
    retry_max_attempts: int = 3
    request_timeout_s: float = 5.0

    class Config:
        env_file = ".env"


settings = Settings()
