"""代码影响分析智能体使用的结构化数据模型。"""

from typing import Literal

from pydantic import BaseModel, Field


class ImpactedFile(BaseModel):
    """一个受到软件变更影响的现有文件。"""

    # 必须是仓库扫描结果中真实存在的相对路径。
    path: str = Field(
        min_length=1,
        description="受到影响的现有文件相对路径",
        examples=["order_app/models.py"],
    )

    # 说明需求为什么会影响这个文件。
    reason: str = Field(
        min_length=1,
        description="该文件受到影响的原因",
    )

    # 描述应该在这个文件中进行哪些修改。
    suggested_changes: list[str] = Field(
        min_length=1,
        description="建议在该文件中完成的修改",
    )

    # 风险等级只能从三个固定值中选择。
    risk_level: Literal["low", "medium", "high"] = Field(
        description="修改该文件的风险等级",
    )


class CodeImpactAnalysis(BaseModel):
    """代码影响分析智能体的完整输出。"""

    # 对本次代码影响范围的整体说明。
    summary: str = Field(
        min_length=1,
        description="代码影响范围的整体概括",
    )

    # 仓库中已经存在并且需要修改的文件。
    impacted_files: list[ImpactedFile] = Field(
        min_length=1,
        description="需要修改的现有文件",
    )

    # 为实现需求可能需要新建的文件。
    new_files: list[str] = Field(
        default_factory=list,
        description="建议新增的文件相对路径",
    )

    # 需要修改或新增测试的现有测试文件。
    test_files: list[str] = Field(
        default_factory=list,
        description="需要调整的现有测试文件",
    )

    # 需要新增、删除或升级的项目依赖。
    dependency_changes: list[str] = Field(
        default_factory=list,
        description="项目依赖变更",
    )
