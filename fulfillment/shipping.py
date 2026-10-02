"""模拟物流适配器。

不发起任何真实网络请求：按方向生成可识别的运单号，并支持把包裹推进到
在途 / 送达 / 损坏状态。真实环境可替换为实现同一接口的适配器。
"""
from __future__ import annotations

from collections import Counter

_PREFIX = {
    "kit_outbound": "CK",      # 材料包去程
    "work_inbound": "HS",      # 作品回寄（handicap send-back）
    "finish_outbound": "FC",   # 成品返寄（finished craft）
}


class MockShippingAdapter:
    def __init__(self) -> None:
        self._seq = Counter()

    def create_waybill(self, direction: str, start: int = 0) -> str:
        """生成运单号；start 为已持久化的同前缀单号数量，避免重启后撞号。"""
        prefix = _PREFIX[direction]
        n = max(self._seq[prefix], start) + 1
        self._seq[prefix] = n
        return f"{prefix}-{n:06d}"

    def describe(self, direction: str) -> str:
        return {
            "kit_outbound": "材料包去程",
            "work_inbound": "作品回寄",
            "finish_outbound": "成品返寄",
        }[direction]
