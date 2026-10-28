"""Evidence-grounded 正式分析 API 的稳定公开契约。

这个模块只描述 HTTP 边界，不包含仓库扫描、LLM 调用或数据库逻辑。
从 API 1.0 开始明确区分两种状态：

1. ``workflow_status`` 表示请求执行到了哪个阶段；
2. ``status`` 表示统一报告是否允许直接实施。

后续接入 PostgreSQL 时，可以扩展查询接口的工作流状态，但不能改变
本文件已经冻结的字段含义。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.analysis_report import AnalysisReport
from app.schemas.review import ReviewStatus

EVIDENCE_ANALYSIS_API_VERSION = "1.0"

# 当前 POST 接口是同步执行：只有完整报告构建成功后才返回 200。
# PostgreSQL 阶段新增的查询接口可以拥有 queued/running 等状态，
# 但不得把这些运行状态混入报告准入状态。
EvidenceAnalysisWorkflowStatus = Literal["completed"]

# 保留旧类型名称，避免现有内部导入立即失效。
EvidenceAnalysisStatus = ReviewStatus


class EvidenceAnalysisRequest(BaseModel):
    """启动证据驱动代码分析所需的公开输入。"""

    request_text: str = Field(
        min_length=1,
        max_length=10_000,
        description="用户提交的原始软件变更需求",
        examples=["为订单系统增加取消订单功能"],
    )

    repository_name: str = Field(
        min_length=1,
        max_length=200,
        description="工作区中需要分析的仓库目录名称",
        examples=["order_service"],
    )

    commit: str = Field(
        default="HEAD",
        min_length=1,
        max_length=200,
        description="要分析的 Git commit、分支或 tag",
        examples=["HEAD"],
    )


class EvidenceAnalysisReviewRequest(BaseModel):
    """人工审核正式分析时提交的稳定公开输入。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "revise", "reject"] = Field(
        description="批准现有假设、要求修订需求，或终止本次分析",
    )

    feedback: str = Field(
        default="",
        max_length=10_000,
        description="审核意见；revise 和 reject 时必填",
    )

    actor_id: str | None = Field(
        default=None,
        max_length=200,
        description="可选的审核人员标识",
    )

    @model_validator(mode="after")
    def normalize_and_validate_feedback(
        self,
    ) -> "EvidenceAnalysisReviewRequest":
        """清理审核文本，并阻止无说明的修订或拒绝。"""

        feedback = " ".join(self.feedback.split())
        actor_id = " ".join(self.actor_id.split()) if self.actor_id else None

        if self.action in {"revise", "reject"} and not feedback:
            raise ValueError(f"{self.action} 操作必须提供审核反馈。")

        self.feedback = feedback
        self.actor_id = actor_id or None

        return self


class EvidenceAnalysisResponse(BaseModel):
    """正式分析接口返回给前端的稳定成功响应。"""

    api_version: Literal["1.0"] = Field(
        default=EVIDENCE_ANALYSIS_API_VERSION,
        description="公开 API 契约版本",
    )

    thread_id: str = Field(
        min_length=32,
        max_length=32,
        pattern=r"^[0-9a-f]{32}$",
        description="本次分析会话的唯一标识",
    )

    workflow_status: EvidenceAnalysisWorkflowStatus = Field(
        default="completed",
        description="分析工作流执行状态；不得与报告准入状态混用",
    )

    status: ReviewStatus = Field(
        description="报告准入状态：ready、gated 或 blocked",
    )

    report: AnalysisReport = Field(
        description="经过证据校验、结果审核后的统一分析报告",
    )

    @model_validator(mode="after")
    def validate_status_matches_report(
        self,
    ) -> "EvidenceAnalysisResponse":
        """防止 HTTP 外层准入状态与报告内部状态不一致。"""

        if self.status != self.report.overview.status:
            raise ValueError("response.status 与 report.overview.status 不一致。")

        return self
