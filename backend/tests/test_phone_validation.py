from app.schemas import BloodBank


def bank(phone):
    return BloodBank(bank_id="test-bank", name="Test facility", phone=phone)


def test_invalid_source_phone_is_unavailable():
    assert bank("12345").phone is None
    assert bank("not a number").phone is None
    assert bank("+91 98765 43210").phone == "+91 98765 43210"


def test_phone_placeholder_values_are_not_callable():
    for value in ("-", "N/A", "unknown", "not available", None):
        assert bank(value).phone is None
