from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.core.config import Settings, get_settings


class ModelConfigurationError(RuntimeError):
    """模型配置缺失或不正确。"""


def create_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """根据项目配置创建 DeepSeek 模型客户端。"""

    resolved_settings = settings or get_settings()
    api_key = resolved_settings.deepseek_api_key.get_secret_value().strip()

    if not api_key:
        raise ModelConfigurationError("DEEPSEEK_API_KEY 未配置，请在 backend/.env 中填写。")

    return ChatOpenAI(
        model=resolved_settings.deepseek_model,
        api_key=api_key,
        base_url=resolved_settings.deepseek_base_url,
        temperature=0,
        timeout=120,
        max_retries=2,
    )


@lru_cache
def get_chat_model() -> ChatOpenAI:
    """创建并缓存默认模型客户端。"""

    return create_chat_model()
