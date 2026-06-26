from models.keys.key import Key


def test_grace_expiry_defaults_none():
    k = Key(tg_id=1, client_id="c", email="a@b.c", expiry_time=0, key="k", inbound_id=7)
    assert k.grace_expiry is None


def test_grace_expiry_is_persisted_field():
    assert "grace_expiry" in Key._DB_FIELDS


def test_to_dict_includes_grace_expiry():
    k = Key(tg_id=1, client_id="c", email="a@b.c", expiry_time=0, key="k", inbound_id=7,
            grace_expiry=1234567890000)
    d = k.to_dict()
    assert d["grace_expiry"] == 1234567890000
