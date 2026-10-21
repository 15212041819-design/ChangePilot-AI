"""订单系统使用的领域模型。"""

from dataclasses import dataclass
from enum import StrEnum


class OrderStatus(StrEnum):
    """订单当前所处的业务状态。"""

    # 订单已经创建，但用户还没有完成支付。
    PENDING = "pending"

    # 订单已经完成支付。
    PAID = "paid"


@dataclass(slots=True)
class Order:
    """订单领域对象。"""

    # 订单在系统中的唯一标识。
    order_id: str

    # 创建该订单的用户标识。
    user_id: str

    # 新创建的订单默认处于待支付状态。
    status: OrderStatus = OrderStatus.PENDING