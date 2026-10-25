"""实施计划智能体使用的结构化数据模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ImplementationAction = Literal[
    "modify",
    "create",
    "verify",
]

ImplementationPriority = Literal[
    "high",
    "medium",
    "low",
]

ImplementationRiskLevel = Literal[
    "low",
    "medium",
    "high",
]


class ImplementationStep(BaseModel):
    """开发人员可以按顺序执行的一个实施步骤。"""

    model_config = ConfigDict(extra="forbid")

    # 步骤标识会被执行顺序和其他步骤的依赖关系引用。
    step_id: str = Field(
        min_length=1,
        description="实施步骤唯一标识",
    )

    # 用一句话概括该步骤要完成的工作。
    title: str = Field(
        min_length=1,
        description="实施步骤标题",
    )

    # modify 和 create 会改变文件，verify 只执行验证。
    action: ImplementationAction = Field(
        description="实施步骤动作",
    )

    # modify/create 必须提供文件路径；verify 必须为 None。
    target_path: str | None = Field(
        default=None,
        description="需要修改或创建的仓库相对路径",
    )

    # 高优先级步骤通常是核心业务或安全相关修改。
    priority: ImplementationPriority = Field(
        description="实施步骤优先级",
    )

    # 说明该步骤的目的及其与整体变更的关系。
    description: str = Field(
        min_length=1,
        description="实施步骤说明",
    )

    # 描述开发人员需要完成的具体代码改动。
    changes: list[str] = Field(
        min_length=1,
        description="具体修改内容",
    )

    # 只能引用当前计划中真实存在且排在前面的 step_id。
    depends_on: list[str] = Field(
        default_factory=list,
        description="当前步骤依赖的前置步骤",
    )

    # 必须引用需求分析中真实存在的验收条件。
    related_acceptance_criteria: list[str] = Field(
        min_length=1,
        description="关联的需求验收条件",
    )

    # 只能引用测试策略中真实存在的测试场景名称。
    related_test_scenarios: list[str] = Field(
        default_factory=list,
        description="关联的测试场景名称",
    )

    # 记录该步骤的局部实施风险。
    risk_level: ImplementationRiskLevel = Field(
        description="实施风险等级",
    )

    # 当前步骤完成后可以执行的局部验证命令。
    # 系统只展示这些命令，不会自动执行。
    verification_commands: list[str] = Field(
        default_factory=list,
        description="步骤级验证命令",
    )

    # 说明该步骤失败时如何恢复到修改前状态。
    rollback_instructions: list[str] = Field(
        min_length=1,
        description="步骤级回滚说明",
    )


class ImplementationPlan(BaseModel):
    """实施计划智能体生成的完整开发任务清单。"""

    model_config = ConfigDict(extra="forbid")

    # 概括实施范围、主要顺序和最终目标。
    summary: str = Field(
        min_length=1,
        description="实施计划概括",
    )

    # 包含文件修改、文件创建和最终验证步骤。
    steps: list[ImplementationStep] = Field(
        min_length=1,
        description="实施步骤",
    )

    # 必须按实际执行先后列出所有 step_id，且不能重复。
    execution_order: list[str] = Field(
        min_length=1,
        description="实施步骤执行顺序",
    )

    # 完成全部修改后执行的项目级验证命令。
    # 系统只展示命令，不会自动执行。
    final_verification_commands: list[str] = Field(
        min_length=1,
        description="最终验证命令",
    )

    # 标记适合保存或检查代码状态的位置。
    rollback_checkpoints: list[str] = Field(
        min_length=1,
        description="整体回滚检查点",
    )

    # 计划执行时仍需关注的整体风险。
    risks: list[str] = Field(
        default_factory=list,
        description="整体实施风险",
    )

    # 证据不足但为了形成计划而保留的显式假设。
    assumptions: list[str] = Field(
        default_factory=list,
        description="实施计划假设",
    )
