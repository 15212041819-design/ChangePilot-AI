"""候选池确定性检索的全量文件轨迹契约。

该契约不仅记录最终进入 CandidatePool 的文件，还记录仓库快照中每个文件
为什么没有进入候选池。它用于定位召回失败发生在“没有信号”还是“排名截断”，
不能参与候选打分，也不能改变 CandidatePool 的 digest。
"""

from __future__ import annotations

from math import fsum
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.contracts import FileKind, FrozenContractModel, SignalScore

CandidateRetrievalOutcome = Literal[
    "selected",
    "below_cutoff",
    "no_signal",
    "scope_excluded",
    "document",
]


class CandidateRetrievalTrace(FrozenContractModel):
    """仓库中一个文件在基础候选检索阶段的完整判定结果。"""

    path: str = Field(min_length=1)
    kind: FileKind
    outcome: CandidateRetrievalOutcome

    # full_rank 是截断前的全量排名；
    # 只有真正获得过检索信号的文件才拥有排名。
    full_rank: int | None = Field(
        default=None,
        ge=1,
    )

    # 所有确定性检索信号的稳定求和结果。
    total_score: float = Field(
        default=0.0,
        ge=0.0,
    )

    signals: tuple[SignalScore, ...] = ()

    @model_validator(mode="after")
    def validate_trace_consistency(
        self,
    ) -> CandidateRetrievalTrace:
        """保证轨迹可以复算，且排除原因与评分状态一致。"""

        names = tuple(signal.name for signal in self.signals)

        # 信号必须稳定排序，避免不同平台或不同进程产生不同结果。
        if names != tuple(sorted(names)):
            raise ValueError("检索轨迹中的 signals 必须按名称稳定排序。")

        if len(names) != len(set(names)):
            raise ValueError("检索轨迹中的信号名称不能重复。")

        ranked = self.outcome in {
            "selected",
            "below_cutoff",
        }

        if ranked:
            # selected 和 below_cutoff 都是已经参与过排名的文件。
            if self.full_rank is None or not self.signals:
                raise ValueError("已评分文件必须包含 full_rank 和至少一个信号。")

            expected_total = round(
                fsum(sorted(signal.value for signal in self.signals)),
                3,
            )

            if self.total_score != expected_total:
                raise ValueError("total_score 必须等于 signals 的稳定求和结果。")

        elif self.full_rank is not None or self.signals or self.total_score != 0.0:
            # 没进入评分阶段的文件不能伪造分数或排名。
            raise ValueError("未评分文件不能携带排名、分数或信号。")

        return self
