"""在隔离 Git 快照上运行可插拔的离线评测实验。

运行器只负责评测纪律，不绑定具体 LLM 或确定性引擎：

1. 每条样本只签出 analysis_commit，不让实验读取 answer_commit 内容；
2. 共变索引只读取 analysis_commit 之前的历史，并执行时间泄漏自检；
3. 单条样本失败会转换为 failed observation，不中断其余样本；
4. 时间泄漏属于评测红线，一旦发现立即使整次评测作废；
5. 最后统一调用指标计算器，缺失和失败样本不能被静默忽略。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from app.schemas.evaluation import (
    EvaluationDataset,
    EvaluationSample,
)
from app.schemas.evaluation_metrics import (
    EvaluationMetricsReport,
    EvaluationObservation,
)
from app.tools.evaluation_metrics import (
    calculate_evaluation_metrics,
)
from app.tools.git_cochange import (
    CoChangeIndex,
    CoChangeLeakReport,
    build_cochange_index,
    check_cochange_leak,
)
from app.tools.git_snapshot import (
    checkout_repository_snapshot,
)


class EvaluationRunInvalidError(RuntimeError):
    """整次评测违反红线，结果不得继续使用。"""


@dataclass(frozen=True)
class EvaluationCaseContext:
    """实验适配器处理一条样本时可以读取的全部资源。"""

    sample: EvaluationSample
    snapshot_path: Path
    snapshot_commit: str
    cochange_index: CoChangeIndex

    def __post_init__(self) -> None:
        """确保适配器看到的快照就是样本规定的分析提交。"""

        if self.snapshot_commit != self.sample.analysis_commit:
            raise ValueError("snapshot_commit 与样本 analysis_commit 不一致。")

        if self.cochange_index.target_commit != self.sample.analysis_commit:
            raise ValueError("CoChangeIndex 与样本 analysis_commit 不一致。")


EvaluationAdapter = Callable[
    [EvaluationCaseContext],
    EvaluationObservation,
]


@dataclass(frozen=True)
class EvaluationExperimentResult:
    """一次完整实验的原始记录、泄漏证明和聚合指标。"""

    experiment_name: str
    dataset: EvaluationDataset
    observations: tuple[EvaluationObservation, ...]
    cochange_leak_reports: tuple[CoChangeLeakReport, ...]
    metrics: EvaluationMetricsReport

    def __post_init__(self) -> None:
        """成功返回的实验必须为每条样本保留一条运行记录。"""

        if len(self.observations) != len(self.dataset.samples):
            raise ValueError("observations 数量必须等于数据集样本数量。")

        if any(not report.passed for report in self.cochange_leak_reports):
            raise ValueError("成功实验结果不能包含未通过的泄漏自检。")


def _failed_observation(
    sample: EvaluationSample,
    *,
    error: str,
    started_at: float,
) -> EvaluationObservation:
    """把单样本异常转换为可计入失败率的标准记录。"""

    elapsed_ms = max(
        0.0,
        (perf_counter() - started_at) * 1000,
    )

    return EvaluationObservation(
        sample_id=sample.sample_id,
        status="failed",
        candidate_pool_paths=(),
        selected_paths=(),
        reported_paths=(),
        invalid_reported_paths=(),
        analyzed_item_count=0,
        assigned_item_count=0,
        latency_ms=round(elapsed_ms, 3),
        token_count=0,
        error=error,
    )


def _validate_adapter_observation(
    sample: EvaluationSample,
    observation: EvaluationObservation,
) -> None:
    """防止适配器把一条样本的结果错误归到另一条样本。"""

    if observation.sample_id != sample.sample_id:
        raise ValueError(
            "实验适配器返回了错误 sample_id："
            f"期望 {sample.sample_id}，"
            f"实际 {observation.sample_id}。"
        )


def run_evaluation_experiment(
    repository_path: str | Path,
    dataset: EvaluationDataset,
    adapter: EvaluationAdapter,
    *,
    experiment_name: str,
    cochange_history_limit: int = 200,
    cochange_max_files_per_commit: int = 50,
    cochange_max_pairs: int = 5_000,
    pool_k: int = 50,
    file_k: int = 12,
) -> EvaluationExperimentResult:
    """在每条样本的隔离快照中执行同一个实验适配器。"""

    if not experiment_name.strip():
        raise ValueError("experiment_name 不能为空。")

    if not dataset.samples:
        raise ValueError("评测数据集不能为空。")

    resolved_repository = Path(repository_path).expanduser().resolve()

    if not resolved_repository.exists():
        raise FileNotFoundError(f"仓库不存在：{resolved_repository}")

    if not resolved_repository.is_dir():
        raise NotADirectoryError(f"仓库路径不是目录：{resolved_repository}")

    observations: list[EvaluationObservation] = []
    leak_reports: list[CoChangeLeakReport] = []

    for sample in dataset.samples:
        started_at = perf_counter()

        try:
            # 共变索引以 analysis_commit 为时间边界。
            # 目标提交和未来提交都不会参与特征计算。
            cochange_index = build_cochange_index(
                resolved_repository,
                sample.analysis_commit,
                history_limit=cochange_history_limit,
                max_files_per_commit=(cochange_max_files_per_commit),
                max_pairs=cochange_max_pairs,
            )

            leak_report = check_cochange_leak(
                resolved_repository,
                cochange_index,
            )

            # R4 红线：
            # 时间泄漏会让所有召回数字失去意义，
            # 因此不能降级成普通单样本失败。
            if not leak_report.passed:
                raise EvaluationRunInvalidError(
                    "cochange_leak_check 未通过，"
                    "整次评测结果作废："
                    f"sample_id={sample.sample_id}，"
                    "unexpected_commits="
                    f"{leak_report.unexpected_commits}"
                )

            leak_reports.append(leak_report)

            with checkout_repository_snapshot(
                resolved_repository,
                sample.analysis_commit,
            ) as snapshot:
                context = EvaluationCaseContext(
                    sample=sample,
                    snapshot_path=(snapshot.repository_path),
                    snapshot_commit=(snapshot.resolved_commit),
                    cochange_index=cochange_index,
                )

                observation = adapter(context)

                _validate_adapter_observation(
                    sample,
                    observation,
                )

                observations.append(observation)

        except EvaluationRunInvalidError:
            raise

        except Exception as exc:  # noqa: BLE001
            # 检查本身还没有执行时，
            # 不得伪造 passed 证明。
            #
            # 该样本只记录 failed observation。
            # 报告中可以看到 leak report 数量
            # 少于样本数量。
            observations.append(
                _failed_observation(
                    sample,
                    error=(f"{type(exc).__name__}: {exc}"),
                    started_at=started_at,
                )
            )

    metrics = calculate_evaluation_metrics(
        dataset,
        tuple(observations),
        experiment_name=experiment_name,
        pool_k=pool_k,
        file_k=file_k,
    )

    return EvaluationExperimentResult(
        experiment_name=experiment_name.strip(),
        dataset=dataset,
        observations=tuple(observations),
        cochange_leak_reports=tuple(leak_reports),
        metrics=metrics,
    )
