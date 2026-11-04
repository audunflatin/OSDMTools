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
