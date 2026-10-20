import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.services.requirement_agent import analyze_requirement


def test_requirement_agent_returns_structured_result() -> None:
    fake_model = FakeListChatModel(
        responses=[
            """
            {
                "summary": "连续登录失败三次后锁定账号",
                "change_type": "feature",
                "affected_modules": ["authentication", "user"],
                "acceptance_criteria": [
                    "连续三次登录失败后账号被锁定",
                    "账号锁定后不能继续登录"
                ],
                "risks": [
                    "正常用户可能因误操作被锁定"
                ]
            }
            """
        ]
    )

    result = analyze_requirement(
        "  用户连续三次   登录失败后锁定账号  ",
        model=fake_model,
    )

    assert result.summary == "连续登录失败三次后锁定账号"
    assert result.change_type == "feature"
    assert result.affected_modules == ["authentication", "user"]
    assert len(result.acceptance_criteria) == 2


def test_requirement_agent_rejects_empty_request() -> None:
    fake_model = FakeListChatModel(responses=["{}"])

    with pytest.raises(ValueError, match="变更需求不能为空"):
        analyze_requirement("   ", model=fake_model)
