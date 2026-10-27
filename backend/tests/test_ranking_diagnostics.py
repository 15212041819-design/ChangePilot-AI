"""候选排序诊断工具测试。"""

import json
from pathlib import Path

import pytest

from app.cli.diagnose_ranking import (
    main,
    run_diagnostics_cli,
)
from app.tools.ranking_diagnostics import (
    RankingDiagnosticsError,
    build_ranking_diagnostics,
    write_ranking_diagnostics,
)


def _report(path: Path) -> None:
    """写入一个包含既有文件与新增文件的最小评测报告。"""

    payload = {
        "schema_version": "1.1",
        "experiment_name": "ranking-test",
        "dataset": {
            "samples": [
                {
                    "sample_id": "sample-1",
                    "request_text": ("change service and model behavior"),
                    "analysis_commit": "a" * 40,
                    "answer_commit": "b" * 40,
                    "existing_source_files": [
                        "app/model.py",
                        "app/service.py",
                    ],
                    "new_source_files": [],
                },
                {
                    "sample_id": "sample-2",
                    "request_text": "add audit module",
                    "analysis_commit": "b" * 40,
                    "answer_commit": "c" * 40,
                    "existing_source_files": [],
                    "new_source_files": [
                        "app/audit.py",
                    ],
                },
            ]
        },
        "observations": [
            {
                "sample_id": "sample-1",
                "candidate_pool_paths": [
                    "app/service.py",
                    "app/unrelated.py",
                    "app/model.py",
                ],
                "selected_paths": [
                    "app/service.py",
                    "app/unrelated.py",
                ],
                "candidate_score_traces": [
                    {
                        "path": "app/service.py",
                        "pool_rank": 1,
                        "total_score": 4.0,
                        "signals": [
                            {
                                "name": "symbol",
                                "value": 4.0,
                            },
                        ],
                    },
                    {
                        "path": "app/unrelated.py",
                        "pool_rank": 2,
                        "total_score": 2.0,
                        "signals": [
                            {
                                "name": "keyword",
                                "value": 2.0,
                            },
                        ],
                    },
                    {
                        "path": "app/model.py",
                        "pool_rank": 3,
                        "total_score": 1.0,
                        "signals": [
                            {
                                "name": "dep_closure",
                                "value": 1.0,
                            },
                        ],
                    },
                ],
                "candidate_rerank_traces": [
                    {
                        "path": "app/service.py",
                        "original_rank": 1,
                        "rerank_rank": 1,
                        "pre_score": 4.0,
                        "lane": "source",
                        "selected": True,
                    },
                    {
                        "path": "app/unrelated.py",
                        "original_rank": 2,
                        "rerank_rank": 2,
                        "pre_score": 2.0,
                        "lane": "source",
                        "selected": True,
                    },
                    {
                        "path": "app/model.py",
                        "original_rank": 3,
                        "rerank_rank": None,
                        "pre_score": 1.0,
                        "lane": "source",
                        "selected": False,
                    },
                ],
            },
            {
                "sample_id": "sample-2",
                "candidate_pool_paths": [
                    "app/service.py",
                ],
                "selected_paths": [],
                "candidate_score_traces": [
                    {
                        "path": "app/service.py",
                        "pool_rank": 1,
                        "total_score": 1.0,
                        "signals": [
                            {
                                "name": "dep_closure",
                                "value": 1.0,
                            },
                        ],
                    }
                ],
                "candidate_rerank_traces": [
                    {
                        "path": "app/service.py",
                        "original_rank": 1,
                        "rerank_rank": None,
                        "pre_score": 1.0,
                        "lane": "source",
                        "selected": False,
                    }
                ],
            },
        ],
        "metrics": {
            "pool_k": 50,
            "file_k": 12,
        },
    }

    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_build_ranking_diagnostics_separates_pool_and_ranking_misses(
    tmp_path: Path,
) -> None:
    """正确文件已进池但未被选择时，应归因于排序阶段。"""

    source = tmp_path / "report.json"
    _report(source)

    report = build_ranking_diagnostics(source)
    first = report.diagnostics[0]

    assert report.evaluable_sample_count == 1
    assert report.existing_truth_count == 2
    assert report.pool_recall_micro == 1.0
    assert report.selection_recall_micro == 0.5
    assert report.selection_recall_macro == 0.5
    assert report.missing_from_pool_count == 0
    assert report.missed_after_ranking_count == 1
    assert report.false_positive_selected_count == 1
    assert report.score_trace_sample_count == 2
    assert report.rerank_trace_sample_count == 2
    assert report.rerank_unchanged_truth_count == 1
    assert report.rerank_rejected_truth_count == 1
    assert report.rerank_promoted_truth_count == 0
    assert report.rerank_demoted_truth_count == 0

    assert first.missed_after_ranking == ("app/model.py",)
    assert first.false_positive_selections == ("app/unrelated.py",)
    assert first.truth_rankings[0].pool_rank == 3
    assert first.truth_rankings[0].selection_rank is None
    assert first.truth_rankings[0].total_score == 1.0
    assert first.truth_rankings[0].signals[0].name == "dep_closure"
    assert first.truth_rankings[0].outcome == "rerank_rejected"
    assert first.truth_rankings[0].rerank_lane == "source"
    assert first.truth_rankings[0].rerank_selected is False
    assert first.truth_rankings[0].rerank_rank is None
    assert first.false_positive_score_traces[0].path == "app/unrelated.py"
    assert first.false_positive_score_traces[0].total_score == 2.0


def test_pool_cutoff_can_expose_retrieval_miss(
    tmp_path: Path,
) -> None:
    """真实文件排名低于 cutoff 时，应归入候选池遗漏。"""

    source = tmp_path / "report.json"
    _report(source)

    report = build_ranking_diagnostics(
        source,
        pool_k=2,
    )

    assert report.pool_recall_micro == 0.5
    assert report.missing_from_pool_count == 1
    assert report.missed_after_ranking_count == 0
    assert report.diagnostics[0].missing_from_pool == ("app/model.py",)
    assert report.diagnostics[0].truth_rankings[0].outcome == "below_pool_cutoff"


def test_legacy_report_without_rerank_traces_remains_supported(
    tmp_path: Path,
) -> None:
    """旧报告没有重排轨迹时仍可诊断，但必须显式标记证据不可用。"""

    source = tmp_path / "legacy-rerank.json"
    _report(source)

    payload = json.loads(
        source.read_text(
            encoding="utf-8",
        )
    )

    for observation in payload["observations"]:
        observation.pop("candidate_rerank_traces")

    source.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    report = build_ranking_diagnostics(source)
    first = report.diagnostics[0]

    assert report.rerank_trace_sample_count == 0
    assert first.rerank_trace_available is False
    assert first.truth_rankings[0].outcome == "selection_untraced"
    assert first.truth_rankings[0].rerank_selected is None


def test_old_report_requires_rescore_before_diagnostics(
    tmp_path: Path,
) -> None:
    """缺少 existing/new 分类的旧报告不能产生误导诊断。"""

    source = tmp_path / "old.json"
    _report(source)

    payload = json.loads(
        source.read_text(
            encoding="utf-8",
        )
    )
    payload["dataset"]["samples"][0].pop("existing_source_files")

    source.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with pytest.raises(
        RankingDiagnosticsError,
        match="app.cli.rescore",
    ):
        build_ranking_diagnostics(source)


def test_cli_writes_json_and_rejects_source_overwrite(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI 写入独立报告，且禁止覆盖唯一的原始报告。"""

    source = tmp_path / "report.json"
    output = tmp_path / "diagnostics.json"
    _report(source)

    report, written, limit = run_diagnostics_cli(
        (
            str(source),
            "--output",
            str(output),
            "--worst-limit",
            "3",
        )
    )

    assert written == output.resolve()
    assert limit == 3

    rendered = json.loads(
        output.read_text(
            encoding="utf-8",
        )
    )
    assert rendered["selected_hit_count"] == 1
    assert rendered["schema_version"] == "1.1"
    assert rendered["rerank_rejected_truth_count"] == 1

    with pytest.raises(
        ValueError,
        match="不能覆盖",
    ):
        write_ranking_diagnostics(
            report,
            source,
        )

    assert (
        main(
            (
                str(source),
                "--output",
                str(source),
            )
        )
        == 1
    )
    assert "ranking diagnostics failed" in capsys.readouterr().err
