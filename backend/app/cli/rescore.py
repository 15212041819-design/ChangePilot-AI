"""不调用 LLM，重新计算既有评测 JSON 的指标。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.tools.evaluation_rescore import (
    rescore_evaluation_report,
    write_rescored_report,
)


def build_argument_parser() -> argparse.ArgumentParser:
    """创建重新计分命令的参数解析器。"""

    parser = argparse.ArgumentParser(
        prog="changepilot-rescore",
        description=("复用历史 observation 重新计算指标，不调用 LLM。"),
    )
    parser.add_argument(
        "report",
        type=Path,
        help="需要重新计分的原始 JSON 报告。",
    )
    parser.add_argument(
        "--repository",
        required=True,
        help=("生成原报告时评测的 Git 仓库或 monorepo 子目录。"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=("新版 JSON 路径；省略时在原文件名后添加 -rescored。"),
    )
    parser.add_argument(
        "--experiment-name",
        help=("新版实验名称；省略时在原实验名后添加 -rescored。"),
    )
    parser.add_argument(
        "--pool-k",
        type=int,
        help="覆盖原报告的 pool cutoff。",
    )
    parser.add_argument(
        "--file-k",
        type=int,
        help="覆盖原报告的 file cutoff。",
    )
    return parser


def _default_output(report_path: Path) -> Path:
    """生成不会覆盖输入报告的默认输出路径。"""

    return report_path.with_name(f"{report_path.stem}-rescored.json")


def _validate_arguments(
    args: argparse.Namespace,
) -> None:
    """在读取仓库之前拒绝非法参数。"""

    if args.pool_k is not None and args.pool_k <= 0:
        raise ValueError("--pool-k 必须大于 0。")

    if args.file_k is not None and args.file_k <= 0:
        raise ValueError("--file-k 必须大于 0。")

    if args.experiment_name is not None and not args.experiment_name.strip():
        raise ValueError("--experiment-name 不能为空。")


def run_rescore_cli(
    argv: Sequence[str] | None = None,
) -> tuple[dict[str, Any], Path]:
    """解析参数、重新计算指标并保存新版报告。"""

    args = build_argument_parser().parse_args(argv)
    _validate_arguments(args)

    report_path = args.report.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else _default_output(report_path)
    )

    payload = rescore_evaluation_report(
        report_path,
        args.repository,
        experiment_name=args.experiment_name,
        pool_k=args.pool_k,
        file_k=args.file_k,
    )
    written = write_rescored_report(
        payload,
        output_path,
        source_path=report_path,
    )
    return payload, written


def _print_summary(
    payload: dict[str, Any],
    output_path: Path,
) -> None:
    """打印带分母信息的重新计分摘要。"""

    metrics = payload["metrics"]
    hallucination = (
        f"{metrics['hallucination_rate']:.3f}"
        if metrics["hallucination_evaluable"]
        else "not-evaluable"
    )

    print(f"experiment: {payload['experiment_name']}")
    print(f"samples: {metrics['sample_count']}")
    print(f"existing_file_samples: {metrics['existing_file_sample_count']}")
    print(f"existing_source_files: {metrics['existing_source_file_count']}")
    print(f"new_source_files: {metrics['new_source_file_count']}")
    print(f"pool_recall@{metrics['pool_k']}: {metrics['pool_recall_at_k']:.3f}")
    print(f"file_recall@{metrics['file_k']}: {metrics['file_recall_at_k']:.3f}")
    print(f"hallucination_rate: {hallucination}")
    print(f"reported_paths: {metrics['reported_path_count']}")
    print(f"output: {output_path}")


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """命令行入口；普通输入错误返回退出码 1。"""

    try:
        payload, output_path = run_rescore_cli(argv)
    except Exception as exc:  # noqa: BLE001
        # CLI 顶层需要统一输出错误并返回稳定退出码，
        # 所以这里有意捕获所有普通异常。
        print(
            f"rescore failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    _print_summary(
        payload,
        output_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
