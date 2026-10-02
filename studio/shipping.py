"""物流适配器：默认内存/数据库模拟实现，可替换为真实承运商 SDK。"""
from __future__ import annotations

from typing import Protocol


class ShippingAdapter(Protocol):
    def create_tracking(self, direction: str, ref_id: str) -> str: ...

    def describe(self, tracking_no: str) -> str: ...


class MockCarrier:
    """模拟承运商：按方向生成不同号段运单号，并给出模拟轨迹描述。"""

    PREFIX = {
        "outbound_kit": "CK",        # 去程材料包
        "inbound_work": "RC",        # 作品回寄
        "outbound_finished": "FC",  # 成品返寄
    }

    def __init__(self) -> None:
        self._seq = 0

    def sync(self, n: int) -> None:
        """以数据库中已有的最大序号续号，避免重启后运单号撞号。"""
        self._seq = max(self._seq, n)

    def create_tracking(self, direction: str, ref_id: str) -> str:
        self._seq += 1
        prefix = self.PREFIX.get(direction, "XX")
        return f"MOCK-{prefix}-{self._seq:05d}"

    def describe(self, tracking_no: str) -> str:
        return f"模拟物流轨迹：{tracking_no} 已揽收 → 运输中 → 待签收（数据由模拟适配器生成）"


# 默认单例，运单号仅与数据库内容有关，重启后靠库中已有号段续号
_default = MockCarrier()


def get_carrier() -> ShippingAdapter:
    return _default
