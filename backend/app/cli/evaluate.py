"""运行无时间泄漏的历史提交评测并输出 JSON 报告。

使用示例：

    python -m app.cli.evaluate C:\\code\\order_service --samples 15

确定性检索基线：

    python -m app.cli.evaluate C:\\code\\order_service \
        --context-mode deterministic \
        --samples 15

语义映射实验：

    python -m app.cli.evaluate C:\\code\\order_service \
        --context-mode semantic \
        --samples 15

命令会从指定 ref 向前筛选历史提交，在每条样本的 ``commit^`` 快照上
运行可选择的上下文检索与确定性 Impact 引擎，并把原始记录与聚合指标
写入 JSON。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import (
    BaseChatModel,
)

from app.tools.evaluation_dataset import (
    build_evaluation_dataset,
)
from app.tools.evaluation_runner import (
    EvaluationExperimentResult,
    EvaluationRunInvalidError,
    run_evaluation_experiment,
)
from app.tools.evidence_evaluation_adapter import (
    EvidenceImpactAdapterConfig,
    create_evidence_impact_adapter,
)

DEFAULT_EXPERIMENT_NAME = "new-retrieval-deterministic-impact"

DEFAULT_OUTPUT_PATH = Path("evals/results/new-retrieval-deterministic-impact.json")


def _default_experiment_name(
    context_mode: str,
) -> str:
    """根据检索模式生成不会混淆基线和语义实验的默认名称。"""

    if context_mode == "semantic":
        return "semantic-retrieval-deterministic-impact"

    return DEFAULT_EXPERIMENT_NAME


def build_argument_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""

    parser = argparse.ArgumentParser(
        prog="changepilot-evaluate",
        description=("从 Git 历史构建无泄漏样本，并运行 ChangePilot AI 证据驱动评测。"),
    )

    parser.add_argument(
        "repository",
        help="待评测 Git 仓库或 monorepo 子目录。",
    )

    parser.add_argument(
        "--ref",
        default="HEAD",
        help=("从哪个 Git ref 开始向前筛选提交，默认 HEAD。"),
    )

    parser.add_argument(
        "--samples",
        type=int,
        default=15,
        help="最多接纳多少条样本，默认 15。",
    )

    parser.add_argument(
        "--scan-limit",
        type=int,
        default=200,
        help=("最多检查多少条历史提交，默认 200。"),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=("JSON 报告路径，默认 evals/results/new-retrieval-deterministic-impact.json。"),
    )

    parser.add_argument(
        "--experiment-name",
        default=None,
        help=("写入报告的实验名称；省略时根据 context mode 自动生成。"),
    )

    parser.add_argument(
        "--context-mode",
        choices=(
            "deterministic",
            "semantic",
        ),
        default="deterministic",
        help=(
            "上下文构建模式："
            "deterministic 为确定性基线，"
            "semantic 为受控语义映射，"
            "默认 deterministic。"
        ),
    )

    parser.add_argument(
        "--pool-limit",
        type=int,
        default=50,
        help=("CandidatePool 文件上限，范围 1 到 60，默认 50。"),
    )

    parser.add_argument(
        "--max-repository-files",
        type=int,
        default=200,
        help=("单个快照最多扫描的仓库文件数，默认 200。"),
    )

    parser.add_argument(
        "--max-file-size-bytes",
        type=int,
        default=50_000,
        help=("单个文件最大读取字节数，默认 50000。"),
    )

    return parser


def _validate_cli_values(
    args: argparse.Namespace,
) -> None:
    """在调用 Git 或 LLM 前拒绝非法命令行配置。"""

    if args.samples <= 0:
        raise ValueError("--samples 必须大于 0。")

    if args.scan_limit <= 0:
        raise ValueError("--scan-limit 必须大于 0。")

    if args.experiment_name is not None and not args.experiment_name.strip():
        raise ValueError("--experiment-name 不能为空。")

    if args.output.suffix.casefold() != ".json":
        raise ValueError("--output 必须使用 .json 扩展名。")


def result_to_json_payload(
    result: EvaluationExperimentResult,
) -> dict[str, Any]:
    """把实验结果转换为带版本号的 JSON 可序列化对象。"""

    return {
        "schema_version": "1.0",
        "experiment_name": result.experiment_name,
        "dataset": asdict(result.dataset),
        "observations": [asdict(observation) for observation in result.observations],
        "cochange_leak_reports": [asdict(report) for report in (result.cochange_leak_reports)],
        "metrics": asdict(result.metrics),
    }


def write_evaluation_result(
    result: EvaluationExperimentResult,
    output_path: str | Path,
) -> Path:
    """使用 UTF-8 和稳定键顺序写入评测报告。"""

    resolved_output = Path(output_path).expanduser().resolve()

    if resolved_output.suffix.casefold() != ".json":
        raise ValueError("评测报告必须使用 .json 扩展名。")

    if resolved_output.exists() and resolved_output.is_dir():
        raise IsADirectoryError(f"评测报告路径是目录：{resolved_output}")

    resolved_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    payload = result_to_json_payload(
        result,
    )

    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    resolved_output.write_text(
        rendered + "\n",
        encoding="utf-8",
        newline="\n",
    )

    return resolved_output


def run_evaluation_cli(
    argv: Sequence[str] | None = None,
    *,
    model: BaseChatModel | None = None,
) -> tuple[
    EvaluationExperimentResult,
    Path,
]:
    """解析参数、执行真实实验并保存报告；测试可注入假模型。"""

    parser = build_argument_parser()
    args = parser.parse_args(argv)

    _validate_cli_values(args)

    repository_path = Path(args.repository).expanduser().resolve()

    dataset = build_evaluation_dataset(
        repository_path,
        requested_ref=args.ref,
        sample_limit=args.samples,
        scan_limit=args.scan_limit,
    )

    if not dataset.samples:
        raise ValueError(
            "没有筛选到可评测提交；请检查提交信息长度、改动文件数或提高 --scan-limit。"
        )

    adapter = create_evidence_impact_adapter(
        model=model,
        config=EvidenceImpactAdapterConfig(
            max_repository_files=(args.max_repository_files),
            max_file_size_bytes=(args.max_file_size_bytes),
            pool_limit=args.pool_limit,
            context_mode=args.context_mode,
        ),
    )

    experiment_name = args.experiment_name or _default_experiment_name(
        args.context_mode,
    )

    result = run_evaluation_experiment(
        repository_path,
        dataset,
        adapter,
        experiment_name=experiment_name,
    )

    output_path = write_evaluation_result(
        result,
        args.output,
    )

    return result, output_path


def _print_summary(
    result: EvaluationExperimentResult,
    output_path: Path,
) -> None:
    """向终端打印适合人工快速核对的精简摘要。"""

    metrics = result.metrics

    print(f"experiment: {result.experiment_name}")
    print(f"samples: {metrics.sample_count}")
    print(f"pool_recall@{metrics.pool_k}: {metrics.pool_recall_at_k:.3f}")
    print(f"file_recall@{metrics.file_k}: {metrics.file_recall_at_k:.3f}")
    print(f"hallucination_rate: {metrics.hallucination_rate:.3f}")
    print(f"failure_rate: {metrics.failure_rate:.3f}")
    print(f"coverage_rate: {metrics.coverage_rate:.3f}")
    print(f"output: {output_path}")


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """命令行进程入口，返回可用于 CI 的退出码。"""

    try:
        result, output_path = run_evaluation_cli(argv)

    except EvaluationRunInvalidError as exc:
        print(
            f"evaluation invalid: {exc}",
            file=sys.stderr,
        )
        return 2

    except Exception as exc:  # noqa: BLE001
        # CLI 必须把普通运行错误转换成稳定退出码，
        # 否则 CI 只能得到不一致的 Python traceback。
        print(
            f"evaluation failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    _print_summary(
        result,
        output_path,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
