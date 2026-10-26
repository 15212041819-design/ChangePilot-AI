"""生成全链路 RequirementSpec 的需求 Agent 和确定性组装层。"""

from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.schemas.contracts import (
    ACRegistryState,
    Assumption,
    EntityHint,
    RequirementSpec,
    ScopeHints,
)
from app.schemas.requirement_spec_draft import RequirementSpecDraft
from app.services.ac_registry import (
    allocate_acceptance_criteria,
)
from app.services.llm import get_chat_model
from app.tools.contract_digest import (
    compute_registry_digest,
    compute_spec_digest,
)

EMPTY_SHA256 = "sha256:" + "0" * 64

SYSTEM_PROMPT = """
你是 ChangePilot AI 的 Requirement Agent。

你的职责是把用户的软件变更需求转换为无 ID、无 digest 的语义草稿。

必须遵守：

1. acceptance_criteria 只能包含用户文本直接支持的可验收行为。
2. 不得生成 AC ID、假设 ID、spec ID 或 digest。
3. 不得虚构文件路径、行号或已存在代码符号。
4. entity_hints 只表示检索意图，symbol_candidates 不代表符号真实存在。
5. 用户没有明确的实现条件必须进入 assumptions，不得进入验收标准。
6. constraints 只保留用户明确提出的限制。
7. verification 只能是 test、manual 或 static。
8. priority 只能是 P0、P1 或 P2。
9. change_type 只能是 feature、fix、refactor、perf、config、remove 或 unknown。

请严格按以下格式返回，不要输出额外说明：

{format_instructions}
""".strip()


@dataclass(frozen=True)
class RequirementSpecAgentResult:
    """Agent 一次运行后的完整可追踪结果。"""

    spec: RequirementSpec

    registry: ACRegistryState

    draft: RequirementSpecDraft


def _normalized_vocabulary(
    repository_vocabulary: set[str],
) -> set[str]:
    """对仓库词表做简单且可复现的大小写归一化。"""

    return {item.strip().casefold() for item in repository_vocabulary if item.strip()}


def _build_entity_hints(
    draft: RequirementSpecDraft,
    *,
    repository_vocabulary: set[str],
) -> tuple[EntityHint, ...]:
    """用真实仓库词表标记候选词是否获得事实支持。"""

    vocabulary = _normalized_vocabulary(repository_vocabulary)

    hints: list[EntityHint] = []

    for index, item in enumerate(
        draft.entity_hints,
        start=1,
    ):
        lookup_terms = (
            item.concept,
            *item.lexical_variants,
            *item.symbol_candidates,
        )

        verified_terms = {
            term.strip().casefold()
            for term in lookup_terms
            if term.strip().casefold() in vocabulary
        }

        unverified_symbols = tuple(
            sorted(
                symbol
                for symbol in item.symbol_candidates
                if symbol.strip().casefold() not in vocabulary
            )
        )

        hints.append(
            EntityHint(
                concept_id=f"EH-{index:03d}",
                concept=item.concept,
                lexical_variants=tuple(sorted(set(item.lexical_variants))),
                symbol_candidates=tuple(sorted(set(item.symbol_candidates))),
                role=item.role,
                in_repo_vocab=bool(verified_terms),
                unverified=unverified_symbols,
            )
        )

    return tuple(hints)


def _build_assumptions(
    draft: RequirementSpecDraft,
) -> tuple[Assumption, ...]:
    """按草稿顺序由代码分配稳定的假设 ID。"""

    return tuple(
        Assumption(
            id=f"AS-{index:03d}",
            text=item.text,
            status="unconfirmed",
            impact=(),
        )
        for index, item in enumerate(
            draft.assumptions,
            start=1,
        )
    )


def build_requirement_spec(
    draft: RequirementSpecDraft,
    *,
    source_text: str,
    repository_vocabulary: set[str],
    registry: ACRegistryState | None = None,
    spec_id: str = "RS-0001",
    revision: int = 1,
    scope_hints: ScopeHints | None = None,
) -> RequirementSpecAgentResult:
    """用 ACRegistry 和仓库词表将 LLM 草稿冻结为 RequirementSpec。"""

    normalized_source = " ".join(source_text.split())

    if not normalized_source:
        raise ValueError("变更需求不能为空。")

    current_registry = registry or ACRegistryState()

    updated_registry = allocate_acceptance_criteria(
        current_registry,
        list(draft.acceptance_criteria),
        revision=revision,
    )

    candidate = RequirementSpec(
        spec_id=spec_id,
        spec_revision=revision,
        summary=draft.summary,
        change_type=draft.change_type,
        acceptance_criteria=tuple(
            updated_registry.criteria[criterion_id].criterion
            for criterion_id in sorted(updated_registry.criteria)
        ),
        entity_hints=_build_entity_hints(
            draft,
            repository_vocabulary=(repository_vocabulary),
        ),
        symbol_hints=(),
        constraints=tuple(sorted(set(draft.constraints))),
        assumptions=_build_assumptions(draft),
        scope_hints=(scope_hints or ScopeHints()),
        source_text=normalized_source,
        spec_digest=EMPTY_SHA256,
    )

    registry_digest = compute_registry_digest(updated_registry)

    spec = candidate.model_copy(
        update={
            "spec_digest": (
                compute_spec_digest(
                    candidate,
                    registry_digest=(registry_digest),
                )
            )
        }
    )

    return RequirementSpecAgentResult(
        spec=spec,
        registry=updated_registry,
        draft=draft,
    )


def analyze_requirement_spec(
    request_text: str,
    *,
    repository_vocabulary: set[str],
    registry: ACRegistryState | None = None,
    spec_id: str = "RS-0001",
    revision: int = 1,
    scope_hints: ScopeHints | None = None,
    model: BaseChatModel | None = None,
) -> RequirementSpecAgentResult:
    """调用 Requirement Agent，再使用确定性代码生成最终契约。"""

    normalized_request = " ".join(request_text.split())

    if not normalized_request:
        raise ValueError("变更需求不能为空。")

    parser = PydanticOutputParser(
        pydantic_object=(RequirementSpecDraft),
    )

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                SYSTEM_PROMPT,
            ),
            (
                "human",
                ("请分析以下软件变更需求：\n\n{request_text}"),
            ),
        ]
    )

    selected_model = model or get_chat_model()

    chain = prompt | selected_model | parser

    draft = chain.invoke(
        {
            "request_text": (normalized_request),
            "format_instructions": (parser.get_format_instructions()),
        }
    )

    return build_requirement_spec(
        draft,
        source_text=normalized_request,
        repository_vocabulary=(repository_vocabulary),
        registry=registry,
        spec_id=spec_id,
        revision=revision,
        scope_hints=scope_hints,
    )
