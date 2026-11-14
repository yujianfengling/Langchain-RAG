"""白板板书存档测试（2026-11-14）

覆盖式保存、按 (user, subject, key) 隔离、超限拒存、无存档返回 None。
"""

import pytest

from services import mindboard_store as store


def _payload(text: str) -> dict:
    return {
        "root": {"id": "n1", "text": text, "side": "right", "children": []},
        "marks": [],
        "deltas": {},
    }


def test_save_and_load_round_trip():
    store.save_board("u1", "s1", "node-a", _payload("逻辑结构分类"))
    data = store.load_board("u1", "s1", "node-a")
    assert data is not None
    assert data["root"]["text"] == "逻辑结构分类"
    assert data["updated_at"]


def test_save_overwrites_and_keeps_keys_isolated():
    store.save_board("u1", "s1", "k", _payload("第一版"))
    first = store.load_board("u1", "s1", "k")["updated_at"]
    store.save_board("u1", "s1", "k", _payload("第二版"))
    second = store.load_board("u1", "s1", "k")
    assert second["root"]["text"] == "第二版"
    assert second["updated_at"] >= first

    # 不同用户/学科/键互不可见
    assert store.load_board("u2", "s1", "k") is None
    assert store.load_board("u1", "s2", "k") is None
    assert store.load_board("u1", "s1", "other") is None


def test_empty_key_falls_back_to_free():
    store.save_board("u1", "s1", "", _payload("自由画板"))
    assert store.load_board("u1", "s1", "free")["root"]["text"] == "自由画板"
    assert store.load_board("u1", "s1", "   ")["root"]["text"] == "自由画板"


def test_load_missing_returns_none():
    assert store.load_board("nobody", "s1", "k") is None


def test_oversized_payload_rejected():
    big = _payload("x")
    big["marks"] = [{"kind": "text", "id": "t1", "color": "#111",
                     "x": 0, "y": 0, "w": 100, "text": "字" * (store.MAX_PAYLOAD_BYTES)}]
    with pytest.raises(ValueError):
        store.save_board("u1", "s1", "k", big)
    # 拒存后原存档不受影响
    store.save_board("u1", "s1", "keep", _payload("完好"))
    assert store.load_board("u1", "s1", "keep")["root"]["text"] == "完好"
