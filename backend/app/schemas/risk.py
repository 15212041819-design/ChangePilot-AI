"""确定性 Risk Engine 使用的 schema 模型事实契约。"""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.contracts import FrozenContractModel

SchemaModelKind = Literal[
    "dataclass",
    "pydantic",
    "typeddict",
    "attrs",
]


class SchemaField(FrozenContractModel):
    """数据模型中一个可序列化字段的索引事实。"""

    name: str = Field(
        min_length=1,
    )

    line: int = Field(
        ge=1,
    )

    type_annotation: str | None = None

    required: bool = True


class SchemaModel(FrozenContractModel):
    """Risk Engine 能够确定性识别的数据模型。"""

    qualified_name: str = Field(
        min_length=1,
    )

    path: str = Field(
        min_length=1,
    )

    kind: SchemaModelKind

    line: int = Field(
        ge=1,
    )

    fields: tuple[SchemaField, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_fields(
        self,
    ) -> "SchemaModel":
        """同一个 schema 内不允许出现重名字段。"""

        field_names = [field.name for field in self.fields]

        if len(field_names) != len(set(field_names)):
            raise ValueError("schema model 包含重复字段。")

        return self
