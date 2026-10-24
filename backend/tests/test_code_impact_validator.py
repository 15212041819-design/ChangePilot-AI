"""代码影响分析真实性校验工具的自动化测试。"""

import pytest

from app.schemas.code_impact import (
    CodeImpactAnalysis,
    ImpactedFile,
)
from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.tools.code_impact_validator import (
    CodeImpactValidationError,
    validate_code_impact_analysis,
)


def build_repository() -> RepositorySnapshot:
    """创建包含业务文件和测试文件的仓库快照。"""

    return RepositorySnapshot(
        repository_name="order_service",
        files=[
            RepositoryFile(
                relative_path="order_app/models.py",
                language="python",
                size_bytes=100,
                content="class Order: ...\n",
            ),
            RepositoryFile(
                relative_path="order_app/service.py",
                language="python",
                size_bytes=100,
                content="class OrderService: ...\n",
            ),
            RepositoryFile(
                relative_path="tests/test_service.py",
                language="python",
                size_bytes=100,
                content="def test_service(): ...\n",
            ),
        ],
        scanned_file_count=3,
        truncated=False,
    )


def build_analysis(
    *,
    impacted_path: str = "order_app/service.py",
    new_files: list[str] | None = None,
    test_files: list[str] | None = None,
) -> CodeImpactAnalysis:
    """创建可以按需修改路径的代码影响分析结果。"""

    return CodeImpactAnalysis(
        summary="需要修改订单服务",
        impacted_files=[
            ImpactedFile(
                path=impacted_path,
                reason="订单服务缺少取消订单功能",
                suggested_changes=["增加 cancel_order 方法"],
                risk_level="medium",
            )
        ],
        new_files=new_files or [],
        test_files=test_files or [],
        dependency_changes=[],
    )


def test_validator_accepts_real_repository_paths() -> None:
    """真实存在且安全的文件路径应该通过校验。"""

    analysis = build_analysis(
        test_files=["tests/test_service.py"],
        new_files=["order_app/cancel_policy.py"],
    )

    result = validate_code_impact_analysis(
        analysis,
        build_repository(),
    )

    assert result.impacted_files[0].path == "order_app/service.py"
    assert result.test_files == ["tests/test_service.py"]
    assert result.new_files == ["order_app/cancel_policy.py"]


def test_validator_normalizes_path_separators() -> None:
    """Windows 分隔符和多余的当前目录标记应该被规范化。"""

    analysis = build_analysis(
        impacted_path=r"order_app\service.py",
        test_files=["./tests/test_service.py"],
    )

    result = validate_code_impact_analysis(
        analysis,
        build_repository(),
    )

    assert result.impacted_files[0].path == "order_app/service.py"
    assert result.test_files == ["tests/test_service.py"]


def test_validator_rejects_unknown_impacted_file() -> None:
    """不存在的文件不能被列为受影响的现有文件。"""

    analysis = build_analysis(
        impacted_path="order_app/missing.py",
    )

    with pytest.raises(
        CodeImpactValidationError,
        match="仓库中不存在的文件",
    ):
        validate_code_impact_analysis(
            analysis,
            build_repository(),
        )


def test_validator_rejects_unknown_test_file() -> None:
    """不存在的测试文件不能被列为现有测试文件。"""

    analysis = build_analysis(
        test_files=["tests/test_missing.py"],
    )

    with pytest.raises(
        CodeImpactValidationError,
        match="仓库中不存在的测试文件",
    ):
        validate_code_impact_analysis(
            analysis,
            build_repository(),
        )


def test_validator_rejects_existing_file_as_new_file() -> None:
    """已经存在的文件不能被错误地列为新文件。"""

    analysis = build_analysis(
        new_files=["order_app/models.py"],
    )

    with pytest.raises(
        CodeImpactValidationError,
        match="仓库中已经存在的文件",
    ):
        validate_code_impact_analysis(
            analysis,
            build_repository(),
        )


def test_validator_rejects_parent_directory_traversal() -> None:
    """文件路径不能使用 .. 逃离代码仓库。"""

    analysis = build_analysis(
        new_files=["../outside.py"],
    )

    with pytest.raises(
        CodeImpactValidationError,
        match="目录穿越路径",
    ):
        validate_code_impact_analysis(
            analysis,
            build_repository(),
        )


def test_validator_rejects_absolute_path() -> None:
    """模型不能返回本地计算机上的绝对路径。"""

    analysis = build_analysis(
        impacted_path=r"C:\Users\example\service.py",
    )

    with pytest.raises(
        CodeImpactValidationError,
        match="不能包含绝对路径",
    ):
        validate_code_impact_analysis(
            analysis,
            build_repository(),
        )


def test_validator_rejects_duplicate_impacted_files() -> None:
    """同一个受影响文件不能重复出现在结果中。"""

    analysis = CodeImpactAnalysis(
        summary="重复列出了订单服务文件",
        impacted_files=[
            ImpactedFile(
                path="order_app/service.py",
                reason="第一次引用",
                suggested_changes=["增加取消订单方法"],
                risk_level="medium",
            ),
            ImpactedFile(
                path="./order_app/service.py",
                reason="第二次引用",
                suggested_changes=["增加状态校验"],
                risk_level="medium",
            ),
        ],
        new_files=[],
        test_files=[],
        dependency_changes=[],
    )

    with pytest.raises(
        CodeImpactValidationError,
        match="包含重复文件",
    ):
        validate_code_impact_analysis(
            analysis,
            build_repository(),
        )
