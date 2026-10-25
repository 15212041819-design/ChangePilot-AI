"""测试策略智能体使用的结构化数据模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TestLevel = Literal[
    "unit",
    "integration",
    "end_to_end",
]

TestCategory = Literal[
    "happy_path",
    "boundary",
    "error",
    "permission",
    "concurrency",
    "regression",
]

TestPriority = Literal[
    "high",
    "medium",
    "low",
]

TestFileAction = Literal[
    "modify",
    "create",
]


class TestScenario(BaseModel):
    """一个可以由开发人员实现的具体测试场景。"""

    model_config = ConfigDict(extra="forbid")

    # 场景名称会被测试文件计划引用，因此必须简短且唯一。
    name: str = Field(
        min_length=1,
        description="测试场景唯一名称",
    )

    # 区分单元测试、集成测试和端到端测试。
    level: TestLevel = Field(
        description="测试层级",
    )

    # 描述该场景关注的测试类型。
    category: TestCategory = Field(
        description="测试场景类型",
    )

    # 高优先级场景应该优先进入本次变更的测试范围。
    priority: TestPriority = Field(
        description="测试优先级",
    )

    # 被测试的模块、类、函数、接口或业务流程。
    target: str = Field(
        min_length=1,
        description="测试目标",
    )

    # 说明为什么需要这个测试场景。
    description: str = Field(
        min_length=1,
        description="测试场景说明",
    )

    # 必须引用需求分析中真实存在的验收条件。
    related_acceptance_criteria: list[str] = Field(
        min_length=1,
        description="关联的需求验收条件",
    )

    # 执行测试前需要满足的状态。
    preconditions: list[str] = Field(
        default_factory=list,
        description="测试前置条件",
    )

    # 开发人员实现测试时需要执行的主要步骤。
    steps: list[str] = Field(
        min_length=1,
        description="测试执行步骤",
    )

    # 测试通过时应该观察到的结果。
    expected_result: str = Field(
        min_length=1,
        description="预期结果",
    )


class TestFilePlan(BaseModel):
    """一个现有或待新建测试文件的修改计划。"""

    model_config = ConfigDict(extra="forbid")

    # 必须是仓库根目录下的安全相对路径。
    path: str = Field(
        min_length=1,
        description="测试文件相对路径",
    )

    # modify 表示文件必须已经存在。
    # create 表示文件当前不能存在。
    action: TestFileAction = Field(
        description="修改现有文件或创建新文件",
    )

    # 解释选择该测试文件的原因。
    reason: str = Field(
        min_length=1,
        description="测试文件规划原因",
    )

    # 引用 TestScenario.name，
    # 表示这个文件负责实现哪些测试场景。
    scenario_names: list[str] = Field(
        min_length=1,
        description="该文件承载的测试场景名称",
    )


class TestStrategy(BaseModel):
    """测试策略智能体生成的完整测试计划。"""

    model_config = ConfigDict(extra="forbid")

    # 对整体测试范围和测试重点的概括。
    summary: str = Field(
        min_length=1,
        description="测试策略概括",
    )

    # 需要实现的结构化测试场景。
    scenarios: list[TestScenario] = Field(
        min_length=1,
        description="测试场景",
    )

    # 现有测试文件修改和新测试文件创建计划。
    test_files: list[TestFilePlan] = Field(
        min_length=1,
        description="测试文件计划",
    )

    # 当前需求或代码信息仍无法覆盖的测试空白。
    coverage_gaps: list[str] = Field(
        default_factory=list,
        description="尚未覆盖或无法确认的测试范围",
    )

    # 执行测试计划时需要关注的风险。
    risks: list[str] = Field(
        default_factory=list,
        description="测试实施风险",
    )
