"""需求人工审核数据模型的自动化测试。"""

import pytest
from pydantic import ValidationError

from app.schemas.human_review import RequirementReviewDecision


def test_requirement_review_allows_approval_without_feedback() -> None:
    """批准需求分析时可以不填写补充反馈。"""

    decision = RequirementReviewDecision(
        action="approve",
    )

    assert decision.action == "approve"
    assert decision.feedback is None


def test_requirement_review_normalizes_rejection_feedback() -> None:
    """拒绝时应该保存清理过空格的有效反馈。"""

    decision = RequirementReviewDecision(
        action="reject",
        feedback="  请补充账号锁定时长。  ",
    )

    assert decision.action == "reject"
    assert decision.feedback == "请补充账号锁定时长。"


def test_requirement_review_rejects_blank_rejection_feedback() -> None:
    """拒绝需求分析时不能提交空反馈。"""

    with pytest.raises(
        ValidationError,
        match="拒绝需求分析时必须提供修改反馈",
    ):
        RequirementReviewDecision(
            action="reject",
            feedback="   ",
        )
