"""API-tester for tilgang og isolasjon mellom brukere."""
import pytest
from fastapi.testclient import TestClient

from backend.auth_db import SessionLocal, User, AccessRequest, init_db
from backend.auth_utils import hash_password
import backend.main as main

PASSWORD = "testpassord123"
TEN_A = "0;100;500\n100;200;900\n"
TEN_B = "0;100;111\n100;200;222\n"


@pytest.fixture(scope="module", autouse=True)
def users():
    init_db()
    with SessionLocal() as db:
        for email in ("a@test.no", "b@test.no"):
            if not db.query(User).filter(User.email == email).first():
                db.add(User(email=email, password_hash=hash_password(PASSWORD),
                            is_active=True, must_change_password=False))
        db.commit()


def _login(email):
    client = TestClient(main.app)
    r = client.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 302
    return client


def test_ten_table_is_per_user():
    """Regresjon for K3: B sin avstandsfil skal ikke overskrive A sin."""
    a, b = _login("a@test.no"), _login("b@test.no")
    assert a.post("/ui/validate-ten", files={"tenFile": ("a.csv", TEN_A)}).json()["ok"]
    assert b.post("/ui/validate-ten", files={"tenFile": ("b.csv", TEN_B)}).json()["ok"]
    assert main.TEN_STORE["a@test.no"]["table"][0] == (0, 100, 500)
    assert main.TEN_STORE["b@test.no"]["table"][0] == (0, 100, 111)


def test_progress_requires_login():
    assert TestClient(main.app).get("/ui/progress").status_code == 401


@pytest.mark.parametrize("email", [
    "x');alert(1);//@evil.no",
    "<script>@evil.no",
    "ikke-en-epost",
])
def test_request_access_rejects_invalid_email(email):
    """Regresjon for K2: ugyldige e-poster havner aldri i admin-listen."""
    main._access_requests.clear()
    r = TestClient(main.app).post("/request-access", data={"email": email, "org": "Test"})
    assert r.status_code == 400
    with SessionLocal() as db:
        assert db.query(AccessRequest).filter(AccessRequest.email == email.lower()).first() is None


def test_generate_osdm_end_to_end():
    """Hele flyten: avstandsfil → OSDM-mal → generering → nedlasting."""
    import json
    import time
    from conftest import ROOT, TEMPLATE_1076

    client = _login("a@test.no")
    ten = (ROOT / "data" / "input" / "TEN-template.csv").read_bytes()
    assert client.post("/ui/validate-ten", files={"tenFile": ("ten.csv", ten)}).json()["ok"]

    job = client.post("/ui/validate-osdm", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())}).json()
    for _ in range(100):
        p = client.get(f"/ui/validate-osdm/progress/{job['jobId']}").json()
        if p["status"] != "running":
            break
        time.sleep(0.1)
    assert p["status"] == "done"

    r = client.post("/ui/generate-osdm", data={
        "exchangeRate": "0.087", "validFrom": "2026-12-13", "validTo": "2027-12-11",
        "datasetId": "8.0", "environment": "test",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["summary"]["pricesUpdated"] > 0
    assert client.get("/ui/progress").json() == {"status": "done", "percent": 100}

    out = json.loads(client.get(f"/ui/download-osdm/{body['outputFile']}").content)
    delivery = out["fareDelivery"]["delivery"]
    assert delivery["deliveryId"] == "8.0" and delivery["usage"] == "TEST_ONLY"
    assert all(f["id"].startswith("1076_8.0_") for f in out["fareDelivery"]["fareStructure"]["prices"])
    # Legacy-feltene SilverRail trenger skal være med (se CLAUDE.md)
    assert all("legacyAccountingIdentifier" in f for f in out["fareDelivery"]["fareStructure"]["fares"])


def _set_user(email, **fields):
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(email=email, password_hash=hash_password(PASSWORD),
                        is_active=True, must_change_password=False)
            db.add(user)
        for k, v in fields.items():
            setattr(user, k, v)
        db.commit()


def test_deactivated_user_loses_access_immediately():
    """H5: en aktiv sesjon skal slutte å virke når brukeren deaktiveres."""
    _set_user("c@test.no", is_active=True)
    client = _login("c@test.no")
    assert client.get("/account").status_code == 200
    _set_user("c@test.no", is_active=False)
    assert client.get("/account").status_code == 401
    assert client.get("/price-adjust", follow_redirects=False).headers["location"].startswith("/login")
    _set_user("c@test.no", is_active=True)


def test_deleted_user_loses_access_immediately():
    _set_user("d@test.no")
    client = _login("d@test.no")
    assert client.get("/account").status_code == 200
    with SessionLocal() as db:
        db.query(User).filter(User.email == "d@test.no").delete()
        db.commit()
    assert client.get("/account").status_code == 401


def test_forced_password_change_cannot_be_skipped():
    """H5: brukeren må bytte passord før noe annet virker."""
    _set_user("e@test.no", must_change_password=True)
    client = TestClient(main.app)
    r = client.post("/login", data={"email": "e@test.no", "password": PASSWORD}, follow_redirects=False)
    assert r.headers["location"] == "/change-password"

    assert client.get("/", follow_redirects=False).headers["location"] == "/change-password"
    assert client.get("/account", follow_redirects=False).headers["location"] == "/change-password"
    assert client.post("/ui/validate-ten", files={"tenFile": ("a.csv", TEN_A)}).status_code == 403

    r = client.post("/change-password", data={"password": "nyttpassord1", "confirm": "nyttpassord1"})
    assert r.status_code == 200
    assert client.get("/account").status_code == 200


def test_contact_requires_login():
    """H4: kontaktskjemaet skal ikke kunne brukes til spam uten innlogging."""
    r = TestClient(main.app).post("/contact", data={"name": "x", "email": "x@x.no", "message": "hei"})
    assert r.status_code == 401


def test_contact_is_rate_limited(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_contact_email", lambda *a: sent.append(a))
    main._contact_sent.clear()
    client = _login("a@test.no")
    data = {"name": "A", "email": "a@test.no", "message": "hei"}
    for _ in range(main._CONTACT_MAX):
        assert client.post("/contact", data=data).json()["ok"]
    assert client.post("/contact", data=data).status_code == 429
    assert len(sent) == main._CONTACT_MAX


def test_emails_escape_user_input(monkeypatch):
    """H4: brukerinput skal ikke kunne legge inn HTML/lenker i e-postene."""
    from backend import email_utils
    captured = {}
    monkeypatch.setattr(email_utils, "_send", lambda to, subject, html: captured.update(subject=subject, html=html))

    email_utils.send_access_request_email('<a href="https://evil">Klikk</a>', "x@x.no", "<b>Org</b>")
    assert "<a href=\"https://evil\">" not in captured["html"]
    assert "&lt;a href=&quot;https://evil&quot;&gt;" in captured["html"]
    assert "<b>Org</b>" not in captured["html"]

    email_utils.send_contact_email("Navn\nBcc: x@y.no", "x@x.no", "<script>alert(1)</script>")
    assert "\n" not in captured["subject"]
    assert "<script>" not in captured["html"]


@pytest.mark.parametrize("name", [
    "validate_distances", "osdm_to_csv", "price_adjust",
    "fare_discount_apply", "fix_osdm", "editor_load",
])
def test_heavy_endpoints_are_sync(name):
    """H1: tunge endepunkter må være `def` så de kjøres i trådpool, ikke på hendelsesløkken."""
    import inspect
    assert not inspect.iscoroutinefunction(getattr(main, name))


def _price_adjust(client, pct, delivery_id="7.0"):
    import json
    from conftest import TEMPLATE_1076
    r = client.post("/price-adjust", files={"osdm_file": ("1076.json", TEMPLATE_1076.read_bytes())}, data={
        "pct": str(pct), "delivery_id": delivery_id, "valid_from": "2026-12-13", "valid_to": "2027-12-11",
    })
    assert r.status_code == 200, r.text
    return json.loads(r.content)


def _fare_price_map(data):
    fs = data["fareDelivery"]["fareStructure"]
    prices = {p["id"]: p["price"][0]["amount"] for p in fs["prices"]}
    return {f["id"]: prices[f["priceRef"]] for f in fs["fares"]}


def test_price_adjust_zero_percent_changes_nothing():
    import json
    from conftest import TEMPLATE_1076
    client = _login("a@test.no")
    original = _fare_price_map(json.loads(TEMPLATE_1076.read_bytes()))
    assert _fare_price_map(_price_adjust(client, 0)) == original


def test_price_adjust_new_delivery_id_keeps_references_valid():
    """Regresjon for ID-mismatch-feilen fra juni 2026."""
    client = _login("a@test.no")
    out = _price_adjust(client, 5, delivery_id="7.1")
    fs = out["fareDelivery"]["fareStructure"]
    price_ids = {p["id"] for p in fs["prices"]}
    assert all(f["priceRef"] in price_ids for f in fs["fares"])
    assert all(pid.startswith("1076_7.1_") for pid in price_ids)
    assert out["fareDelivery"]["delivery"]["deliveryId"] == "7.1"


def test_fix_osdm_on_clean_template_removes_nothing():
    from conftest import TEMPLATE_1076
    client = _login("a@test.no")
    r = client.post("/ui/fix-osdm", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())})
    assert r.status_code == 200, r.text
    assert all(v == 0 for v in r.json()["stats"].values())


def _apply_discount(client, carrier_codes=()):
    import json
    from conftest import TEMPLATE_1076
    data = {
        "stationPairsJson": json.dumps([{"fromCpId": "1076_7.0_E__56", "toCpId": "1076_7.0_E__90"}]),
        "discountName": "Testrabatt", "discountPct": "20",
        "passengerRefs": ["1076_7.0_P__7"], "serviceClassIds": ["BASIC"],
    }
    if carrier_codes:
        data["carrierCodes"] = list(carrier_codes)
    r = client.post("/fare-discount/apply", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())}, data=data)
    assert r.status_code == 200, r.text
    original = json.loads(TEMPLATE_1076.read_bytes())["fareDelivery"]["fareStructure"]
    out = json.loads(r.content)["fareDelivery"]["fareStructure"]
    new_fares = out["fares"][len(original["fares"]):]
    assert new_fares
    return original, out, new_fares


def test_discount_fares_keep_legacy_fields_and_carrier():
    """H2: rabattfarer må ha legacy-feltene SilverRail trenger, og beholde originalens TCO/carrier."""
    client = _login("a@test.no")
    original, _, new_fares = _apply_discount(client)
    template = next(f for f in original["fares"] if f["regionalConstraintRef"] == new_fares[0]["regionalConstraintRef"])
    for f in new_fares:
        assert "legacyAccountingIdentifier" in f and "legacyConversion" in f and "individualContracts" in f
        assert f["involvedTCOs"] == template["involvedTCOs"]
        assert f["carrierConstraintRef"] == template["carrierConstraintRef"]


def test_discount_with_carriers_uses_new_carrier_constraint():
    client = _login("a@test.no")
    _, out, new_fares = _apply_discount(client, carrier_codes=["1186"])
    new_cc = out["carrierConstraints"][-1]
    assert new_cc["includedCarrier"] == ["1186"]
    assert all(f["carrierConstraintRef"] == new_cc["id"] for f in new_fares)


def test_discount_is_based_on_primary_fare_not_fip():
    """M3: med vanlig + FIP-fare for samme kombinasjon skal rabatten regnes fra den høyeste."""
    from backend.osdm_editor import round_up_020
    client = _login("a@test.no")
    original, out, new_fares = _apply_discount(client)
    prices = {p["id"]: p["price"][0]["amount"] / 100 for p in out["prices"]}
    for nf in new_fares:
        candidates = [prices[f["priceRef"]] for f in original["fares"]
                      if f["regionalConstraintRef"] == nf["regionalConstraintRef"]
                      and f["passengerConstraintRef"] == nf["passengerConstraintRef"]
                      and f["serviceClassRef"] == nf["serviceClassRef"]]
        assert len(candidates) >= 1
        assert prices[nf["priceRef"]] == round_up_020(max(candidates) * 0.8)


def test_generate_reports_relations_not_covered_by_distance_file():
    """H3: relasjoner utenfor avstandsfilen skal rapporteres, ikke hoppes stille over."""
    import time
    from conftest import TEMPLATE_1076

    client = _login("b@test.no")
    assert client.post("/ui/validate-ten", files={"tenFile": ("kort.csv", "0;50;100\n")}).json()["ok"]
    job = client.post("/ui/validate-osdm", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())}).json()
    for _ in range(100):
        if client.get(f"/ui/validate-osdm/progress/{job['jobId']}").json()["status"] != "running":
            break
        time.sleep(0.1)

    r = client.post("/ui/generate-osdm", data={
        "exchangeRate": "0.087", "validFrom": "2026-12-13", "validTo": "2027-12-11",
        "datasetId": "8.0", "environment": "test",
    })
    summary = r.json()["summary"]
    assert summary["rcsSkipped"] > 0
    assert 0 < len(summary["rcsSkippedExamples"]) <= 5
    assert "km)" in summary["rcsSkippedExamples"][0]
