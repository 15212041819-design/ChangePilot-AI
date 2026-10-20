import pytest
from langchain_openai import ChatOpenAI

from app.core.config import Settings
from app.services.llm import ModelConfigurationError, create_chat_model


def test_missing_api_key_is_rejected() -> None:
    """没有 API Key 时必须给出明确错误。"""

    settings = Settings(deepseek_api_key="")

    with pytest.raises(ModelConfigurationError, match="DEEPSEEK_API_KEY"):
        create_chat_model(settings)


def test_deepseek_client_is_created_without_network_request() -> None:
    """正确配置后应当创建 DeepSeek 客户端，但不发送网络请求。"""

    settings = Settings(
        deepseek_api_key="test-api-key",
        deepseek_model="deepseek-v4-pro",
    )

    model = create_chat_model(settings)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "deepseek-v4-pro"
