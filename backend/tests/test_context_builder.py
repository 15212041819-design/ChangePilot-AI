"""确定性候选召回与冻结上下文测试。"""

import pytest

from app.schemas.contracts import (
    AcceptanceCriterion,
    Budget,
    EntityHint,
    RequirementSpec,
    ScopeHints,
)
from app.schemas.repository import (
    RepositoryFile,
    RepositorySnapshot,
)
from app.tools.context_builder import (
    build_repository_context,
    classify_file_kind,
    freeze_candidate_set,
)
from app.tools.git_cochange import (
    CoChangeIndex,
    CoChangePair,
)

ZERO_HASH = "sha256:" + "0" * 64


def _file(
    path: str,
    content: str,
    *,
    language: str = "python",
) -> RepositoryFile:
    """创建仓库快照文件。"""

    return RepositoryFile(
        relative_path=path,
        language=language,
        size_bytes=len(content.encode("utf-8")),
        content=content,
    )


def _repository(
    *,
    reverse: bool = False,
) -> RepositorySnapshot:
    """创建包含业务、调用方、测试和独立审计模块的仓库。"""

    files = [
        _file(
            "order_app/models.py",
            "class Order:\n    pass\n",
        ),
        _file(
            "order_app/service.py",
            (
                "from order_app.models "
                "import Order\n\n"
                "class OrderService:\n"
                "    def cancel_order("
                "self, order_id: str"
                ") -> Order:\n"
                "        return Order()\n"
            ),
        ),
        _file(
            "order_app/api.py",
            (
                "from order_app.service "
                "import OrderService\n\n"
                "def cancel_endpoint() -> None:\n"
                "    OrderService()"
                ".cancel_order('1')\n"
            ),
        ),
        _file(
            "tests/test_service.py",
            (
                "from order_app.service "
                "import OrderService\n\n"
                "def test_cancel_order() -> None:\n"
                "    assert OrderService()\n"
            ),
        ),
        _file(
            "infra/audit.py",
            ("def record_event() -> None:\n    pass\n"),
        ),
        _file(
            "README.md",
            "# Order service\n",
            language="markdown",
        ),
    ]

    if reverse:
        files.reverse()

    return RepositorySnapshot(
        repository_name="order_service",
        files=files,
        scanned_file_count=len(files),
        truncated=False,
    )


def _requirement(
    *,
    max_files: int = 12,
) -> RequirementSpec:
    """创建指向 cancel_order 的需求契约。"""

    return RequirementSpec(
        spec_id="RS-0001",
        spec_revision=1,
        summary="增加取消订单功能。",
        change_type="feature",
        acceptance_criteria=(
            AcceptanceCriterion(
                id="AC-001",
                description=("用户可以取消订单。"),
                verification="test",
                priority="P0",
            ),
        ),
        entity_hints=(
            EntityHint(
                concept_id="EH-001",
                concept="取消订单",
                lexical_variants=("cancel order",),
                symbol_candidates=("cancel_order",),
                role="action_target",
                in_repo_vocab=True,
            ),
        ),
        scope_hints=ScopeHints(
            max_files=max_files,
        ),
        source_text=("为订单系统增加取消订单功能。"),
        spec_digest=ZERO_HASH,
    )


def _cochange_index(
    *,
    target_commit: str = "abc123",
) -> CoChangeIndex:
    """构造 service.py 与 audit.py 稳定共变的历史索引。"""

    return CoChangeIndex(
        repository_relative_path="",
        target_commit=target_commit,
        history_commits=(
            "1" * 40,
            "2" * 40,
        ),
        available_commit_count=2,
        analyzed_commit_count=2,
        skipped_merge_commit_count=0,
        skipped_large_commit_count=0,
        file_commit_counts=(
            (
                "infra/audit.py",
                2,
            ),
            (
                "order_app/service.py",
                2,
            ),
        ),
        pairs=(
            CoChangePair(
                first_path=("infra/audit.py"),
                second_path=("order_app/service.py"),
                shared_commit_count=2,
                first_commit_count=2,
                second_commit_count=2,
                score=1.0,
            ),
        ),
        history_truncated=False,
        pairs_truncated=False,
        history_digest=("sha256:" + "1" * 64),
    )


def test_context_retrieval_uses_symbols_dependencies_and_test_mapping() -> None:
    """符号命中应拉入业务文件、调用方、依赖和测试。"""

    result = build_repository_context(
        _requirement(),
        _repository(),
        commit="abc123",
    )

    entries = {entry.path: entry for entry in result.candidate_pool.entries}

    assert "order_app/service.py" in entries
    assert "order_app/api.py" in entries
    assert "order_app/models.py" in entries
    assert "tests/test_service.py" in entries
    assert "README.md" not in entries
    assert "infra/audit.py" not in entries

    service_signals = {signal.name for signal in entries["order_app/service.py"].signals}

    test_signals = {signal.name for signal in entries["tests/test_service.py"].signals}

    assert "symbol" in service_signals
    assert "testmap" in test_signals
    assert result.candidate_set.commit == "abc123"
    assert result.candidate_set.index_digest == result.index_digest


def test_context_digests_are_stable_when_snapshot_order_changes() -> None:
    """仓库文件输入顺序不能改变内容摘要。"""

    first = build_repository_context(
        _requirement(),
        _repository(),
        commit="abc123",
    )

    second = build_repository_context(
        _requirement(),
        _repository(reverse=True),
        commit="abc123",
    )

    assert first.index_digest == second.index_digest
    assert first.candidate_pool.pool_digest == second.candidate_pool.pool_digest
    assert first.candidate_set.set_digest == second.candidate_set.set_digest


def test_freeze_respects_file_budget_and_marks_truncation() -> None:
    """冻结阶段必须使用预算并声明候选未全部进入。"""

    result = build_repository_context(
        _requirement(max_files=2),
        _repository(),
        commit="abc123",
    )

    frozen = result.candidate_set

    assert len(frozen.files) == 2
    assert frozen.truncated is True

    assert all(file_ref.content_sha256.startswith("sha256:") for file_ref in frozen.files)

    assert frozen.set_digest != ZERO_HASH


def test_freeze_rejects_path_outside_candidate_pool() -> None:
    """Semantic Mapping 不能把候选池外路径直接塞入冻结集。"""

    result = build_repository_context(
        _requirement(),
        _repository(),
        commit="abc123",
    )

    with pytest.raises(
        ValueError,
        match="候选池外路径",
    ):
        freeze_candidate_set(
            result.candidate_pool,
            _repository(),
            budget=Budget(),
            selected_paths=("missing.py",),
            selected_by="semantic_map",
        )


def test_content_change_invalidates_index_and_candidate_set_digest() -> None:
    """文件内容变化必须使旧索引和冻结集摘要失效。"""

    original = _repository()
    changed_files = list(original.files)
    service = changed_files[1]

    changed_files[1] = service.model_copy(
        update={"content": (service.content + "\n" + "# changed repository evidence\n")}
    )

    changed = original.model_copy(
        update={
            "files": changed_files,
        }
    )

    first = build_repository_context(
        _requirement(),
        original,
        commit="abc123",
    )

    second = build_repository_context(
        _requirement(),
        changed,
        commit="abc123",
    )

    assert first.index_digest != second.index_digest
    assert first.candidate_set.set_digest != second.candidate_set.set_digest


def test_file_kind_classification_is_deterministic() -> None:
    """文件类型必须由路径确定，不由 LLM 判断。"""

    assert classify_file_kind("tests/test_service.py") == "test"
    assert classify_file_kind("order_app/service.py") == "source"
    assert classify_file_kind("pyproject.toml") == "config"
    assert classify_file_kind("README.md") == "doc"


def test_context_retrieval_adds_low_weight_cochange_neighbor() -> None:
    """稳定共变可以拉入与直接命中文件无依赖边的真实文件。"""

    result = build_repository_context(
        _requirement(),
        _repository(),
        commit="abc123",
        cochange_index=(_cochange_index()),
    )

    entries = {entry.path: entry for entry in result.candidate_pool.entries}

    audit = entries["infra/audit.py"]

    audit_signals = {signal.name: signal.value for signal in audit.signals}

    assert audit_signals == {
        "cochange": 1.25,
    }

    assert audit.pre_score < entries["order_app/service.py"].pre_score


def test_context_retrieval_rejects_mismatched_cochange_commit() -> None:
    """共变索引与代码快照不属于同一 commit 时必须拒绝。"""

    with pytest.raises(
        ValueError,
        match="commit 不一致",
    ):
        build_repository_context(
            _requirement(),
            _repository(),
            commit="abc123",
            cochange_index=(_cochange_index(target_commit="def456")),
        )
