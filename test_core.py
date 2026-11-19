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


def test_hybrid_rag_search_and_citations():
    from v2.rag import search_factory_documents
    results = search_factory_documents("F001", "coolant pump vibration", limit=2)
    assert len(results) > 0, "Expected at least 1 result for coolant pump vibration"
    top = results[0]
    assert "document_name" in top, "Missing document_name in result"
    assert "page_number" in top, "Missing page_number in result"
    assert "section" in top, "Missing section in result"
    assert "citation" in top, "Missing citation in result"
    assert top["status"] == "sufficient_context"
    assert top["confidence"] >= 0.25


def test_rag_insufficient_context_handling():
    from v2.rag import search_factory_documents
    results = search_factory_documents("F001", "intergalactic warp drive tachyon emitter", limit=2)
    assert len(results) > 0
    assert results[0]["status"] == "insufficient_context"
    assert "Insufficient" in results[0]["content"]


def test_macho_telemetry_alerts_and_depletion():
    from v2.telemetry_reporter import analyze_inventory_telemetry, get_telemetry_alerts
    analysis = analyze_inventory_telemetry("F001")
    assert "alerts" in analysis
    assert "status" in analysis
    assert "headline" in analysis
    alerts = get_telemetry_alerts("F001")
    assert len(alerts) > 0, "Expected telemetry depletion alerts for F001"
    top = alerts[0]
    assert "macho_alert_message" in top
    assert top["depletion_status"] in ("depleted", "near_depletion")


def test_telemetry_alerts_api_endpoint():
    from v2.api import factory_telemetry_alerts, all_telemetry_alerts
    res = factory_telemetry_alerts("F001")
    assert "alerts" in res
    assert "status" in res
    all_res = all_telemetry_alerts()
    assert "alerts" in all_res
    assert "critical_count" in all_res


if __name__ == "__main__":
    test_invalid_date_raises_400()
    test_malformed_factory_id_raises_400()
    test_unknown_factory_id_raises_404()
    test_orders_endpoint_returns_expected_structure()
    test_hybrid_rag_search_and_citations()
    test_rag_insufficient_context_handling()
    test_macho_telemetry_alerts_and_depletion()
    test_telemetry_alerts_api_endpoint()
    print("All core self-checks passed.")
