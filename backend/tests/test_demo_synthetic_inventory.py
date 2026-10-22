from datetime import datetime, timezone

from app.services.demo_data import demo_stock


def test_demo_inventory_can_attach_to_a_real_directory_bank_without_replacing_its_identity():
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    bank_id = "datagov-verified-facility-id"

    records = demo_stock(now, [bank_id], "O+", "platelets", requested_quantity=2)[bank_id]

    assert len(records) == 1
    assert records[0].bank_source_id == bank_id
    assert records[0].source_id == "demo-fixture-v1"
    assert records[0].blood_group == "O+"
    assert records[0].component == "platelets"
    assert any("Synthetic demo" in limitation for limitation in records[0].limitations)
