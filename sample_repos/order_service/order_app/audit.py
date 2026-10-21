"""订单操作审计日志的内存实现。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """一条不可修改的订单审计事件。"""

    # 操作类型，例如 order_created 或 order_paid。
    action: str

    # 被操作的订单编号。
    order_id: str


class InMemoryAuditLogger:
    """使用内存列表保存审计事件。"""

    def __init__(self) -> None:
        """初始化空的审计事件列表。"""

        self._events: list[AuditEvent] = []

    def record(self, action: str, order_id: str) -> None:
        """记录一次订单操作。"""

        self._events.append(
            AuditEvent(
                action=action,
                order_id=order_id,
            )
        )

    @property
    def events(self) -> list[AuditEvent]:
        """返回审计事件副本，防止外部直接修改内部列表。"""

        return list(self._events)