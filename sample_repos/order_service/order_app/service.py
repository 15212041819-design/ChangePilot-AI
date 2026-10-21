"""订单系统的核心业务服务。"""

from order_app.audit import InMemoryAuditLogger
from order_app.models import Order, OrderStatus
from order_app.repository import InMemoryOrderRepository


class OrderService:
    """处理订单创建和支付等业务操作。"""

    def __init__(
        self,
        repository: InMemoryOrderRepository,
        audit_logger: InMemoryAuditLogger,
    ) -> None:
        """注入订单仓库和审计日志组件。"""

        self._repository = repository
        self._audit_logger = audit_logger

    def create_order(
        self,
        order_id: str,
        user_id: str,
    ) -> Order:
        """创建一个待支付订单。"""

        order = Order(
            order_id=order_id,
            user_id=user_id,
        )

        self._repository.add(order)

        # 创建订单属于重要业务操作，需要记录审计日志。
        self._audit_logger.record(
            action="order_created",
            order_id=order.order_id,
        )

        return order

    def pay_order(self, order_id: str) -> Order:
        """支付一个处于待支付状态的订单。"""

        order = self._repository.get(order_id)

        # 已支付或处于其他状态的订单不能重复支付。
        if order.status is not OrderStatus.PENDING:
            raise ValueError("只有待支付订单可以完成支付。")

        order.status = OrderStatus.PAID
        self._repository.save(order)

        # 支付完成后记录审计日志。
        self._audit_logger.record(
            action="order_paid",
            order_id=order.order_id,
        )

        return order