import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.schemas.requirement import RequirementAnalysis
from app.services.requirement_agent import (
    analyze_requirement,
    revise_requirement,
)


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
                ],
                "assumptions": [
                    "账号锁定期间是否允许管理员手动解锁尚未确认"
                ],
                "clarification_questions": [
                    "是否需要支持管理员手动解除账号锁定？"
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
    assert result.assumptions == ["账号锁定期间是否允许管理员手动解锁尚未确认"]
    assert result.clarification_questions == ["是否需要支持管理员手动解除账号锁定？"]


def test_requirement_agent_rejects_empty_request() -> None:
    fake_model = FakeListChatModel(responses=["{}"])

    with pytest.raises(ValueError, match="变更需求不能为空"):
        analyze_requirement("   ", model=fake_model)


def test_requirement_agent_revises_analysis_from_feedback() -> None:
    """需求智能体应该把人工反馈合并到修订结果中。"""

    previous_analysis = RequirementAnalysis(
        summary="连续登录失败三次后锁定账号",
        change_type="feature",
        affected_modules=["authentication", "user"],
        acceptance_criteria=[
            "连续三次登录失败后账号被锁定",
        ],
        risks=[
            "正常用户可能因误操作被锁定",
        ],
        assumptions=[
            "是否允许管理员手动解锁尚未确认",
        ],
        clarification_questions=[
            "是否需要支持管理员手动解除账号锁定？",
        ],
    )

    fake_model = FakeListChatModel(
        responses=[
            """
            {
                "summary": "连续登录失败三次后锁定账号，并允许管理员手动解锁",
                "change_type": "feature",
                "affected_modules": [
                    "authentication",
                    "user",
                    "admin"
                ],
                "acceptance_criteria": [
                    "连续三次登录失败后账号被锁定",
                    "管理员可以手动解除账号锁定"
                ],
                "risks": [
                    "正常用户可能因误操作被锁定",
                    "管理员解锁权限配置错误可能造成越权"
                ],
                "assumptions": [],
                "clarification_questions": []
            }
            """
        ]
    )

    result = revise_requirement(
        request_text="用户连续三次登录失败后锁定账号",
        previous_analysis=previous_analysis,
        review_feedback="需要支持管理员手动解除账号锁定。",
        model=fake_model,
    )

    # 人工确认的内容应该进入新的验收标准。
    assert "管理员可以手动解除账号锁定" in (result.acceptance_criteria)

    # 已经回答的问题不应继续作为未确认假设。
    assert result.assumptions == []
    assert result.clarification_questions == []


def test_requirement_agent_rejects_blank_revision_feedback() -> None:
    """拒绝需求分析时必须提交有效的修改反馈。"""

    previous_analysis = RequirementAnalysis(
        summary="增加账号锁定功能",
        change_type="feature",
        affected_modules=["authentication"],
        acceptance_criteria=["登录失败后锁定账号"],
        risks=[],
        assumptions=["锁定时长尚未确认"],
        clarification_questions=["账号需要锁定多长时间？"],
    )

    fake_model = FakeListChatModel(responses=["{}"])

    with pytest.raises(
        ValueError,
        match="必须提供修改反馈",
    ):
        revise_requirement(
            request_text="增加账号锁定功能",
            previous_analysis=previous_analysis,
            review_feedback="   ",
            model=fake_model,
        )
