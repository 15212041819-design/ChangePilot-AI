"""代码影响分析智能体的自动化测试。"""

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
)

from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.schemas.requirement import RequirementAnalysis
from app.services.code_impact_agent import analyze_code_impact


def build_requirement() -> RequirementAnalysis:
    """创建测试使用的结构化需求。"""

    return RequirementAnalysis(
        summary="为订单系统增加取消订单功能",
        change_type="feature",
        affected_modules=[
            "订单模型",
            "订单服务",
            "审计日志",
        ],
        acceptance_criteria=[
            "只有待支付订单可以取消",
            "取消订单后必须记录审计日志",
        ],
        risks=[
            "支付和取消同时发生可能导致状态冲突",
        ],
    )


def build_repository() -> RepositorySnapshot:
    """创建测试使用的简化仓库快照。"""

    return RepositorySnapshot(
        repository_name="order_service",
        files=[
            RepositoryFile(
                relative_path="order_app/models.py",
                language="python",
                size_bytes=100,
                content=('class OrderStatus:\n    PENDING = "pending"\n    PAID = "paid"\n'),
            ),
            RepositoryFile(
                relative_path="order_app/service.py",
                language="python",
                size_bytes=100,
                content=("class OrderService:\n    def pay_order(self, order_id): ...\n"),
            ),
            RepositoryFile(
                relative_path="tests/test_service.py",
                language="python",
                size_bytes=100,
                content="def test_pay_pending_order(): ...\n",
            ),
        ],
        scanned_file_count=3,
        truncated=False,
    )


def test_code_impact_agent_returns_structured_result() -> None:
    """智能体应该返回结构化且基于真实文件的分析结果。"""

    fake_model = FakeListChatModel(
        responses=[
            """
            {
                "summary": "需要扩展订单状态和订单业务服务",
                "impacted_files": [
                    {
                        "path": "order_app/models.py",
                        "reason": "订单缺少已取消状态",
                        "suggested_changes": [
                            "增加 CANCELLED 订单状态"
                        ],
                        "risk_level": "medium"
                    },
                    {
                        "path": "order_app/service.py",
                        "reason": "订单服务缺少取消操作",
                        "suggested_changes": [
                            "增加取消待支付订单的方法",
                            "取消成功后记录审计日志"
                        ],
                        "risk_level": "high"
                    },
                    {
                        "path": "tests/test_service.py",
                        "reason": "需要验证取消订单业务规则",
                        "suggested_changes": [
                            "增加取消待支付订单测试",
                            "增加已支付订单不能取消的测试"
                        ],
                        "risk_level": "low"
                    }
                ],
                "new_files": [],
                "test_files": [
                    "tests/test_service.py"
                ],
                "dependency_changes": []
            }
            """
        ]
    )

    result = analyze_code_impact(
        requirement=build_requirement(),
        repository=build_repository(),
        model=fake_model,
    )

    assert len(result.impacted_files) == 3
    assert result.impacted_files[0].path == "order_app/models.py"
    assert result.impacted_files[1].risk_level == "high"
    assert result.test_files == ["tests/test_service.py"]
    assert result.dependency_changes == []


def test_code_impact_agent_rejects_empty_repository() -> None:
    """智能体应该拒绝没有任何源代码的仓库。"""

    empty_repository = RepositorySnapshot(
        repository_name="empty",
        files=[],
        scanned_file_count=0,
        truncated=False,
    )

    fake_model = FakeListChatModel(responses=["{}"])

    with pytest.raises(
        ValueError,
        match="代码仓库中没有可分析的文件",
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=empty_repository,
            model=fake_model,
        )


def test_code_impact_agent_rejects_unknown_file() -> None:
    """智能体不应该把仓库中不存在的文件当作现有文件。"""

    fake_model = FakeListChatModel(
        responses=[
            """
            {
                "summary": "错误地引用了不存在的文件",
                "impacted_files": [
                    {
                        "path": "order_app/cancel.py",
                        "reason": "模型错误地认为该文件已经存在",
                        "suggested_changes": [
                            "修改取消订单逻辑"
                        ],
                        "risk_level": "medium"
                    }
                ],
                "new_files": [],
                "test_files": [],
                "dependency_changes": []
            }
            """
        ]
    )

    with pytest.raises(
        ValueError,
        match="仓库中不存在的文件",
    ):
        analyze_code_impact(
            requirement=build_requirement(),
            repository=build_repository(),
            model=fake_model,
        )
