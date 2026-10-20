from pydantic import SecretStr

from app.core.config import Settings, get_settings


def test_settings_are_loaded() -> None:
    """配置模块应当提供正确的基础配置。"""

    settings = get_settings()

    assert settings.app_name == "ChangePilot AI"
    assert settings.app_port == 8010
    assert settings.deepseek_model == "deepseek-v4-pro"


def test_api_key_uses_secret_type() -> None:
    """API Key 必须使用不会直接显示明文的类型。"""

    settings = Settings(deepseek_api_key="test-secret")

    assert isinstance(settings.deepseek_api_key, SecretStr)
    assert str(settings.deepseek_api_key) == "**********"
    assert settings.deepseek_api_key.get_secret_value() == "test-secret"
