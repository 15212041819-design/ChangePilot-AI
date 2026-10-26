"""Evidence-grounded 分析 API 与生产执行器之间的边界。

这一层故意不在 HTTP 路由中扫描仓库、构建索引或运行 Agent。
后续的生产组装只需实现 EvidenceAnalysisExecutor，API 契约无需再改。
"""

from functools import lru_cache
from typing import Protocol

from app.schemas.analysis_report import AnalysisReport
from app.schemas.evidence_analysis_api import EvidenceAnalysisRequest


class EvidenceAnalysisUnavailableError(RuntimeError):
    """服务端尚未装配可用的证据分析运行时。"""


class EvidenceAnalysisExecutor(Protocol):
    """路由所依赖的最小执行器协议。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        """执行完整分析并返回已审核的统一报告。"""


class UnconfiguredEvidenceAnalysisExecutor:
    """生产资源尚未接入时使用的明确失败实现。"""

    def analyze(
        self,
        request: EvidenceAnalysisRequest,
    ) -> AnalysisReport:
        """禁止在未配置检索、索引和分支运行器时伪造报告。"""

        raise EvidenceAnalysisUnavailableError("Evidence-grounded 分析运行时尚未配置。")


@lru_cache
def get_evidence_analysis_executor() -> EvidenceAnalysisExecutor:
    """返回 API 使用的执行器，便于 FastAPI 测试和生产替换。"""

    return UnconfiguredEvidenceAnalysisExecutor()
