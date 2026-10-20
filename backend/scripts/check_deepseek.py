from app.core.config import get_settings
from app.services.llm import get_chat_model


def main() -> None:
    """发送一次最小请求，检查 DeepSeek API 是否可用。"""

    settings = get_settings()
    model = get_chat_model().bind(max_tokens=64)

    print(f"正在连接模型：{settings.deepseek_model}")
    print(f"API 地址：{settings.deepseek_base_url}")

    response = model.invoke(
        "这是一次 API 连通性测试。请只回复：ChangePilot AI 模型连接成功"
    )

    print(f"模型响应：{response.content}")


if __name__ == "__main__":
    main()