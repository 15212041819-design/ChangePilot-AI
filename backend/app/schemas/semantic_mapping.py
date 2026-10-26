"""Semantic Mapping Agent 的结构化决策契约。"""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.contracts import FrozenContractModel

SemanticToolName = Literal[
    "search_identifiers",
    "expand_callers",
    "read_signature",
    "list_module_symbols",
]


class SemanticQuery(FrozenContractModel):
    """Agent 对白名单仓库查询工具的一次请求。"""

    tool: SemanticToolName

    terms: tuple[str, ...] = Field(
        default=(),
        max_length=8,
    )

    symbol: str | None = Field(
        default=None,
        max_length=300,
    )

    module: str | None = Field(
        default=None,
        max_length=300,
    )

    reason: str = Field(
        min_length=1,
        max_length=300,
    )

    @model_validator(mode="after")
    def validate_tool_arguments(
        self,
    ) -> "SemanticQuery":
        """每个工具只能携带自己需要的参数。"""

        if self.tool == "search_identifiers":
            if not self.terms:
                raise ValueError("search_identifiers 必须提供 terms。")

            if self.symbol is not None or self.module is not None:
                raise ValueError("search_identifiers 不允许提供 symbol 或 module。")

            return self

        if self.tool in {
            "expand_callers",
            "read_signature",
        }:
            if self.symbol is None or not self.symbol.strip():
                raise ValueError(f"{self.tool} 必须提供 symbol。")

            if self.terms or self.module is not None:
                raise ValueError(f"{self.tool} 只允许提供 symbol。")

            return self

        if self.module is None or not self.module.strip():
            raise ValueError("list_module_symbols 必须提供 module。")

        if self.terms or self.symbol is not None:
            raise ValueError("list_module_symbols 只允许提供 module。")

        return self


class SemanticMappingDecision(FrozenContractModel):
    """Agent 一轮语义重排或扩池决策。"""

    needs_more_context: bool

    selected_paths: tuple[str, ...] = Field(
        default=(),
        max_length=60,
    )

    queries: tuple[
        SemanticQuery,
        ...,
    ] = Field(
        default=(),
        max_length=4,
    )

    explanation: str = Field(
        min_length=1,
        max_length=1000,
    )

    @model_validator(mode="after")
    def validate_decision_shape(
        self,
    ) -> "SemanticMappingDecision":
        """扩池轮和最终选择轮不能混用。"""

        if len(self.selected_paths) != len(set(self.selected_paths)):
            raise ValueError("selected_paths 不能包含重复路径。")

        if self.needs_more_context:
            if not self.queries:
                raise ValueError("needs_more_context=true 时 queries 不能为空。")

            if self.selected_paths:
                raise ValueError("扩池轮不能同时输出 selected_paths。")

            return self

        if self.queries:
            raise ValueError("最终选择轮不能继续请求工具。")

        return self


class SemanticToolTrace(FrozenContractModel):
    """一次受控工具执行的审计记录。"""

    round: int = Field(
        ge=1,
        le=3,
    )

    tool: SemanticToolName

    args_digest: str

    accepted_paths: tuple[str, ...] = ()

    rejected: tuple[str, ...] = ()
