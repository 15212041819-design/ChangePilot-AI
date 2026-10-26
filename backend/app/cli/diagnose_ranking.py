"""读取既有评测报告并生成候选排序诊断。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.schemas.ranking_diagnostics import (
    RankingDiagnosticsReport,
)
from app.tools.ranking_diagnostics import (
    build_ranking_diagnostics,
    write_ranking_diagnostics,
)


def build_argument_parser() -> argparse.ArgumentParser:
    """创建排序诊断命令行参数。"""

    parser = argparse.ArgumentParser(
        prog="changepilot-diagnose-ranking",
        description=("诊断正确文件在候选池和最终选择中的位置，不调用 LLM。"),
    )
    parser.add_argument(
        "report",
        type=Path,
        help="已经完成 rescore 的评测 JSON。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="诊断 JSON 输出路径。",
    )
    parser.add_argument(
        "--pool-k",
        type=int,
        help="覆盖报告中的 pool cutoff。",
    )
    parser.add_argument(
        "--file-k",
        type=int,
        help="覆盖报告中的 selection cutoff。",
    )
    parser.add_argument(
        "--worst-limit",
        type=int,
        default=5,
        help="终端最多显示多少条最差样本，默认 5。",
    )
    return parser


def _default_output(source: Path) -> Path:
    """生成独立的默认诊断文件名。"""

    return source.with_name(f"{source.stem}-ranking-diagnostics.json")


def run_diagnostics_cli(
    argv: Sequence[str] | None = None,
) -> tuple[RankingDiagnosticsReport, Path, int]:
    """生成诊断并保存 JSON。"""

    args = build_argument_parser().parse_args(argv)

    for name in (
        "pool_k",
        "file_k",
        "worst_limit",
    ):
        value = getattr(args, name)

        if value is not None and value <= 0:
            raise ValueError(f"--{name.replace('_', '-')} 必须大于 0。")

    source = args.report.expanduser().resolve()
    output = args.output.expanduser().resolve() if args.output else _default_output(source)
    report = build_ranking_diagnostics(
        source,
        pool_k=args.pool_k,
        file_k=args.file_k,
    )
    written = write_ranking_diagnostics(
        report,
        output,
    )
    return report, written, args.worst_limit


def _print_summary(
    report: RankingDiagnosticsReport,
    output: Path,
    worst_limit: int,
) -> None:
    """打印总体瓶颈以及召回最差的样本。"""

    print(f"experiment: {report.experiment_name}")
    print(f"samples: {report.sample_count}")
    print(f"evaluable_samples: {report.evaluable_sample_count}")
    print(f"score_trace_samples: {report.score_trace_sample_count}")
    print(f"existing_truth_files: {report.existing_truth_count}")
    print(f"pool_recall_micro@{report.pool_k}: {report.pool_recall_micro:.3f}")
    print(f"selection_recall_micro@{report.file_k}: {report.selection_recall_micro:.3f}")
    print(f"selection_recall_macro@{report.file_k}: {report.selection_recall_macro:.3f}")
    print(f"missing_from_pool: {report.missing_from_pool_count}")
    print(f"missed_after_ranking: {report.missed_after_ranking_count}")
    print(f"false_positive_selections: {report.false_positive_selected_count}")

    worst = sorted(
        (item for item in report.diagnostics if item.evaluable),
        key=lambda item: (
            item.selection_recall,
            -len(item.missed_after_ranking),
            item.sample_id,
        ),
    )[:worst_limit]

    for item in worst:
        print(
            "worst_sample: "
            f"{item.sample_id} "
            f"recall={item.selection_recall:.3f} "
            f"pool_missing={list(item.missing_from_pool)} "
            f"ranking_missing={list(item.missed_after_ranking)}"
        )

        if not item.score_trace_available:
            print("  score_evidence: unavailable (legacy report)")
            continue

        missed_paths = set(item.missed_after_ranking)

        for truth in item.truth_rankings:
            if truth.path not in missed_paths:
                continue

            signal_values = {signal.name: signal.value for signal in truth.signals}
            print(
                "  missed_truth: "
                f"{truth.path} "
                f"rank={truth.pool_rank} "
                f"score={truth.total_score} "
                f"signals={signal_values}"
            )

        for trace in item.false_positive_score_traces[:3]:
            signal_values = {signal.name: signal.value for signal in trace.signals}
            print(
                "  selected_false_positive: "
                f"{trace.path} "
                f"rank={trace.pool_rank} "
                f"score={trace.total_score} "
                f"signals={signal_values}"
            )

    print(f"output: {output}")


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """命令行入口。"""

    try:
        report, output, worst_limit = run_diagnostics_cli(argv)
    except Exception as exc:  # noqa: BLE001
        # CLI 必须把错误转换成稳定的非零退出码。
        print(
            f"ranking diagnostics failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    _print_summary(
        report,
        output,
        worst_limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
