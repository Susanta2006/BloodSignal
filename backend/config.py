"""Environment-backed settings. No secrets live in code or the repo."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Keep explicit Settings(...) instances isolated from the developer's local .env.
    # The application factory opts into .env through get_settings().
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    frontend_origin: str = "http://localhost:5173"

    # Directory and inventory are independent providers.
    directory_source_mode: Literal["demo", "datagov", "google_places"] = "demo"
    datagov_api_key: SecretStr = SecretStr("")
    datagov_resource_id: str = "fced6df9-a360-4e08-8ca0-f283fc74ce15"
    datagov_api_base_url: str = "https://api.data.gov.in/resource"
    datagov_page_size: int = Field(100, ge=1, le=1000)
    datagov_max_pages: int = Field(5, ge=1, le=50)
    datagov_timeout_s: float = Field(8.0, gt=0, le=60)
    datagov_fallback_to_demo: bool = False

    google_maps_server_key: SecretStr = SecretStr("")

    # demo = synthetic fixtures only. live = e-RaktKosh only. They never mix or fall back.
    stock_source_mode: Literal["demo", "live"] = "live"
    stock_source_timeout_s: float = Field(8.0, gt=0, le=60)
    stock_source_max_attempts: int = Field(3, ge=1, le=5)
    retry_base_delay_s: float = Field(0.5, ge=0, le=10)

    # e-RaktKosh bulk endpoint. Source timestamps are timezone-naive; default to India time.
    # An empty deployment variable also falls back to Asia/Kolkata to avoid losing timestamps.
    eraktkosh_source_timezone: str = "Asia/Kolkata"

    # Opt-in Nominatim geocoding. Never required for stock-search success.
    geocoder_enabled: bool = False
    geocoder_contact: str = ""
    geocoder_base_url: str = "https://nominatim.openstreetmap.org/search"
    geocoder_timeout_s: float = Field(2.0, gt=0, le=10)
    geocoder_candidate_limit: int = Field(10, ge=1, le=50)
    geocoder_time_budget_s: float = Field(3.0, gt=0, le=15)

    freshness_hours: int = Field(24, ge=1, le=720)
    max_radius_km: int = Field(100, ge=1, le=500)
    ranking_freshness_weight: float = Field(0.1, ge=0, le=1)
    ranking_quantity_weight: float = Field(0.1, ge=0, le=1)
    ranking_distance_weight: float = Field(0.8, ge=0, le=1)
    ranking_distance_scale_km: float = Field(100.0, gt=0, le=10_000)
    rate_limit_per_minute: int = Field(30, ge=1)
    rate_limit_redis_url: str = ""
    max_request_body_bytes: int = Field(16_384, ge=1024, le=2_000_000)
    max_upload_body_bytes: int = Field(15_000_000, ge=1024, le=20_000_000)
    token_hash_secret: SecretStr = SecretStr("")
    voice_rate_limit_per_minute: int = Field(5, ge=1, le=100)
    lovable_ai_gateway_key: SecretStr = SecretStr("")
    trust_proxy_headers: bool = False
    metrics_token: str = ""

    # --- inventory cache / persistence (see docs/inventory-cache.md)
    stock_cache_ttl_seconds: int = Field(1200, ge=30, le=86_400)        # normal freshness window: 20 min
    max_stale_age_seconds: int = Field(7200, ge=0, le=604_800)          # stale fallback ceiling: 2 h; 0 disables
    refresh_lock_ttl_seconds: int = Field(60, ge=5, le=600)
    refresh_wait_seconds: float = Field(5.0, ge=0, le=30)               # how long a loser waits for the lock holder
    breaker_failure_threshold: int = Field(5, ge=1, le=100)
    breaker_cooldown_seconds: float = Field(30.0, ge=1, le=3600)
    refresh_admin_token: str = ""                                       # empty = manual refresh endpoint disabled
    agent_max_tool_calls: int = Field(8, ge=1, le=50)
    agent_max_workflow_seconds: float = Field(20.0, ge=1, le=120)

    llm_provider: Literal["none", "openai"] = "none"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""

    @model_validator(mode="after")
    def _production_is_never_demo_by_default(self) -> "Settings":
        # Demo is the local-dev default. In production it must be chosen explicitly, never inherited.
        if self.app_env.lower() == "production" and "stock_source_mode" not in self.model_fields_set:
            self.stock_source_mode = "live"
        if self.ranking_freshness_weight + self.ranking_quantity_weight + self.ranking_distance_weight <= 0:
            raise ValueError("At least one ranking weight must be greater than zero")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    @property
    def demo_mode(self) -> bool:
        return self.stock_source_mode == "demo"


@lru_cache
def get_settings() -> Settings:
    return Settings(_env_file=".env")
