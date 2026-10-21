"""订单数据仓库的内存实现。"""

from order_app.models import Order


class OrderNotFoundError(LookupError):
    """请求的订单不存在。"""


class InMemoryOrderRepository:
    """使用内存字典保存订单的仓库。"""

    def __init__(self) -> None:
        """初始化空的订单存储空间。"""

        # 字典的键是订单编号，值是订单对象。
        self._orders: dict[str, Order] = {}

    def add(self, order: Order) -> None:
        """新增一条订单记录。"""

        # 相同订单编号不能重复创建。
        if order.order_id in self._orders:
            raise ValueError(
                f"订单已经存在：{order.order_id}"
            )

        self._orders[order.order_id] = order

    def get(self, order_id: str) -> Order:
        """根据订单编号查询订单。"""

        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise OrderNotFoundError(
                f"订单不存在：{order_id}"
            ) from exc

    def save(self, order: Order) -> None:
        """保存已经存在的订单。"""

        # save 只负责更新已有订单，不负责创建新订单。
        if order.order_id not in self._orders:
            raise OrderNotFoundError(
                f"订单不存在：{order.order_id}"
            )

        self._orders[order.order_id] = order