"""API-tester for tilgang og isolasjon mellom brukere."""
import pytest
from fastapi.testclient import TestClient

from backend.auth_db import SessionLocal, User, AccessRequest, init_db
from backend.auth_utils import hash_password
import backend.main as main
from backend.core import security, state
from backend.routers import public

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
    assert state.TEN_STORE["a@test.no"]["table"][0] == (0, 100, 500)
    assert state.TEN_STORE["b@test.no"]["table"][0] == (0, 100, 111)


def test_progress_requires_login():
    assert TestClient(main.app).get("/ui/progress").status_code == 401


@pytest.mark.parametrize("email", [
    "x');alert(1);//@evil.no",
    "<script>@evil.no",
    "ikke-en-epost",
])
def test_request_access_rejects_invalid_email(email):
    """Regresjon for K2: ugyldige e-poster havner aldri i admin-listen."""
    security._access_requests.clear()
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
    monkeypatch.setattr(public, "send_contact_email", lambda *a: sent.append(a))
    security._contact_sent.clear()
    client = _login("a@test.no")
    data = {"name": "A", "email": "a@test.no", "message": "hei"}
    for _ in range(security._CONTACT_MAX):
        assert client.post("/contact", data=data).json()["ok"]
    assert client.post("/contact", data=data).status_code == 429
    assert len(sent) == security._CONTACT_MAX


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


@pytest.mark.parametrize("path", [
    "/ui/validate-distances", "/frontend/osdm-to-csv", "/price-adjust",
    "/fare-discount/apply", "/ui/fix-osdm", "/osdm-editor/load",
])
def test_heavy_endpoints_are_sync(path):
    """H1: tunge endepunkter må være `def` så de kjøres i trådpool, ikke på hendelsesløkken."""
    import inspect
    endpoint = next(r.endpoint for r in main.app.routes if getattr(r, "path", None) == path and "POST" in r.methods)
    assert not inspect.iscoroutinefunction(endpoint)


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


def test_forgot_password_is_rate_limited():
    """M4: «glemt passord» skal ikke kunne brukes til å bombe noen med e-post."""
    security._forgot_requests.clear()
    client = TestClient(main.app)
    for _ in range(5):
        assert client.post("/forgot-password", data={"email": "finnes.ikke@test.no"}).status_code == 200
    assert client.post("/forgot-password", data={"email": "finnes.ikke@test.no"}).status_code == 429


def test_editor_metadata_writes_valid_calendar_dates():
    """M7: datoer fra editoren skal få samme format og utcOffset som generering."""
    import json
    from conftest import TEMPLATE_1076
    client = _login("a@test.no")
    r = client.post("/osdm-editor/load", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())})
    assert r.status_code == 200
    assert len(r.json()["validity"]["from_date"]) == 10  # YYYY-MM-DD for <input type="date">

    client.post("/osdm-editor/metadata", json={"valid_from": "2027-01-10", "valid_until": "2027-06-30"})
    out = json.loads(client.get("/osdm-editor/download").content)
    cal = out["fareDelivery"]["fareStructure"]["calendars"][0]
    assert cal["fromDate"] == "2027-01-10T00:00:00+0000"
    assert cal["untilDate"] == "2027-06-30T23:59:59+0000"
    assert cal["utcOffset"] == 60  # vintertid i Oslo


def test_price_adjust_uses_exact_ratios_and_leaves_no_unused_prices():
    """M1: +5 % → voksen skaleres per relasjon, øvrige fra eksakte ratioer, ingen ubrukte priser."""
    import json
    from collections import defaultdict
    from conftest import TEMPLATE_1076
    from backend.osdm_editor import round_up_020

    def per_rc(data):
        fs = data["fareDelivery"]["fareStructure"]
        prices = {p["id"]: p["price"][0]["amount"] / 100 for p in fs["prices"]}
        out = defaultdict(lambda: defaultdict(list))
        for f in fs["fares"]:
            out[f["regionalConstraintRef"].split("_", 2)[2]][f["passengerConstraintRef"].split("_", 2)[2]].append(prices[f["priceRef"]])
        return out, fs

    before, _ = per_rc(json.loads(TEMPLATE_1076.read_bytes()))
    after, fs = per_rc(_price_adjust(_login("a@test.no"), 5, delivery_id="7.1"))

    for rc, pcs in before.items():
        new_adult = round_up_020(max(pcs["G__1"]) * 1.05)
        assert max(after[rc]["G__1"]) == new_adult
        assert max(after[rc]["G__3"]) == round_up_020(new_adult * 0.25)
        assert max(after[rc]["G__5"]) == round_up_020(new_adult * 0.5)
        assert max(after[rc]["G__2"]) == round_up_020(new_adult * 0.9)

    used = {f["priceRef"] for f in fs["fares"]}
    assert {p["id"] for p in fs["prices"]} == used
    assert all("legacyAccountingIdentifier" in f for f in fs["fares"])


def _poll(client, url, done=("done", "error"), tries=200):
    import time
    for _ in range(tries):
        p = client.get(url).json()
        if p["status"] in done:
            return p
        time.sleep(0.05)
    raise AssertionError(f"Jobben ble ikke ferdig: {p}")


def test_excel_export_job():
    """Excel-eksport: opplasting → jobb → fremdrift → nedlasting av gyldig xlsx."""
    import io
    from openpyxl import load_workbook
    from conftest import TEMPLATE_1076
    client = _login("a@test.no")
    job = client.post("/frontend/osdm-to-csv", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())}).json()
    status = _poll(client, f"/frontend/osdm-to-csv-status/{job['jobId']}")
    assert status["status"] == "done" and status["rows"] > 0
    assert status["filename"] == "1076_7.0_prod.xlsx"

    # En annen bruker får ikke se eller laste ned jobben
    other = _login("b@test.no")
    assert other.get(f"/frontend/osdm-to-csv-status/{job['jobId']}").status_code == 403

    r = client.get(f"/frontend/osdm-to-csv-download/{job['jobId']}")
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws.max_row - 8 == status["rows"]  # data starter på rad 9


def test_excel_export_rejects_non_osdm():
    client = _login("a@test.no")
    r = client.post("/frontend/osdm-to-csv", files={"osdmFile": ("x.json", b'{"foo": 1}')})
    assert r.status_code == 400


def test_fare_discount_parse_job():
    from conftest import TEMPLATE_1076
    client = _login("a@test.no")
    job = client.post("/fare-discount/parse", files={"osdmFile": ("1076.json", TEMPLATE_1076.read_bytes())}).json()
    p = _poll(client, f"/fare-discount/parse/progress/{job['jobId']}")
    assert p["status"] == "done"
    result = p["result"]
    assert result["deliveryId"] == "7.0"
    assert result["stations"] and result["carriers"] and result["serviceClasses"]

    bad = client.post("/fare-discount/parse", files={"osdmFile": ("x.json", b"ikke json")}).json()
    p = _poll(client, f"/fare-discount/parse/progress/{bad['jobId']}")
    assert p == {"status": "error", "percent": 0, "result": None, "error": "Filen er ikke gyldig JSON"}


def test_validate_osdm_invalid_json_reports_error():
    """Regresjon: ugyldig JSON ga «reading 26 %» for alltid i stedet for feil."""
    client = _login("a@test.no")
    job = client.post("/ui/validate-osdm", files={"osdmFile": ("x.json", b"ikke json")}).json()
    p = _poll(client, f"/ui/validate-osdm/progress/{job['jobId']}")
    assert p["status"] == "error" and p["error"] == "Filen er ikke gyldig JSON"


def test_login_upgrades_old_password_hash():
    """Hasher med 29 000 runder (passlib) oppgraderes ved vellykket innlogging."""
    from backend.auth_utils import hash_password, needs_rehash
    _set_user("f@test.no", password_hash=hash_password(PASSWORD, rounds=29000))
    _login("f@test.no")
    with SessionLocal() as db:
        assert not needs_rehash(db.query(User).filter(User.email == "f@test.no").first().password_hash)
