"""Evidence-grounded 分析 API 的请求和响应契约。"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.analysis_report import AnalysisReport


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
        description="需要分析的工作区仓库名称",
        examples=["order_service"],
    )

    commit: str = Field(
        default="HEAD",
        min_length=1,
        max_length=200,
        description="要分析的 Git commit、分支或 tag",
        examples=["HEAD"],
    )


EvidenceAnalysisStatus = Literal[
    "ready",
    "gated",
    "blocked",
]


class EvidenceAnalysisResponse(BaseModel):
    """证据驱动分析接口的统一响应。"""

    thread_id: str = Field(
        min_length=1,
        description="本次分析请求的唯一标识",
    )

    status: EvidenceAnalysisStatus = Field(
        description="统一报告的实施准入状态",
    )

    report: AnalysisReport = Field(
        description="经过 digest 校验、结果审核后的统一分析报告",
    )

    @model_validator(mode="after")
    def validate_status_matches_report(
        self,
    ) -> "EvidenceAnalysisResponse":
        """防止 HTTP 外层状态与报告内部状态不一致。"""

        if self.status != self.report.overview.status:
            raise ValueError("response.status 与 report.overview.status 不一致。")

        return self
