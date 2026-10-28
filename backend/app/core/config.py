"""应用运行配置。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    """ChangePilot AI 的运行配置。"""

    app_name: str = "ChangePilot AI"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8010

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"

    # 默认值只服务于本地开发。生产部署必须通过 DATABASE_URL 覆盖，
    # 不允许把真实数据库密码提交到 Git。
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://changepilot:changepilot@127.0.0.1:5432/changepilot"
    )
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """读取并缓存配置，避免每次请求都重新读取 .env。"""

    return Settings()
