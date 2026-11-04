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
