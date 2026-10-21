"""订单业务服务的自动化测试。"""

from order_app.audit import InMemoryAuditLogger
from order_app.models import OrderStatus
from order_app.repository import InMemoryOrderRepository
from order_app.service import OrderService


def build_order_service() -> tuple[
    OrderService,
    InMemoryOrderRepository,
    InMemoryAuditLogger,
]:
    """创建测试使用的订单服务及其依赖组件。"""

    repository = InMemoryOrderRepository()
    audit_logger = InMemoryAuditLogger()
    service = OrderService(
        repository=repository,
        audit_logger=audit_logger,
    )

    return service, repository, audit_logger


def test_create_order() -> None:
    """创建订单后，订单应该处于待支付状态。"""

    service, repository, audit_logger = build_order_service()

    order = service.create_order(
        order_id="order-001",
        user_id="user-001",
    )

    assert order.status is OrderStatus.PENDING
    assert repository.get("order-001") is order
    assert audit_logger.events[-1].action == "order_created"


def test_pay_pending_order() -> None:
    """待支付订单完成支付后，状态和日志应该被更新。"""

    service, repository, audit_logger = build_order_service()

    service.create_order(
        order_id="order-001",
        user_id="user-001",
    )
    paid_order = service.pay_order("order-001")

    assert paid_order.status is OrderStatus.PAID
    assert repository.get("order-001").status is OrderStatus.PAID
    assert audit_logger.events[-1].action == "order_paid"