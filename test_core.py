"""Assert-based self-check for v2 core API validation (Ponytail rule)."""
from fastapi import HTTPException
from v2.api import _day_dir, _factory_dir


def test_invalid_date_raises_400():
    try:
        _day_dir("F001", "not-a-date")
        assert False, "Should have raised HTTPException 400"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Invalid date format" in exc.detail


def test_malformed_factory_id_raises_400():
    try:
        _factory_dir("bad/id/path")
        assert False, "Should have raised HTTPException 400"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Invalid factory ID" in exc.detail


def test_unknown_factory_id_raises_404():
    try:
        _factory_dir("F999")
        assert False, "Should have raised HTTPException 404"
    except HTTPException as exc:
        assert exc.status_code == 404
        assert "Factory not found" in exc.detail


def test_orders_endpoint_returns_expected_structure():
    from v2.api import factory_orders
    res = factory_orders("F001")
    assert "orders" in res
    assert "active_orders" in res
    assert "completed_orders" in res
    assert "next_orders" in res


if __name__ == "__main__":
    test_invalid_date_raises_400()
    test_malformed_factory_id_raises_400()
    test_unknown_factory_id_raises_404()
    test_orders_endpoint_returns_expected_structure()
    print("All core self-checks passed.")
