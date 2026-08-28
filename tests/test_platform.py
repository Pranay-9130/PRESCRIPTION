from fastapi.testclient import TestClient

from main import (
    app,
    SessionLocal,
    Appointment,
    hash_password,
    is_prescription_expired,
    apply_prescription,
)


client = TestClient(app)


def test_home_and_pwa_assets():
    home = client.get("/")
    assert home.status_code == 200
    assert "RxVault" in home.text
    assert 'rel="manifest"' in home.text
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    manifest = client.get("/manifest.json")
    assert manifest.status_code == 200
    health = client.get("/api/health")
    assert health.json()["ok"] is True


def test_hospitals_and_departments_still_work():
    assert client.get("/hospitals").status_code == 200
    assert client.get("/hospital/1").status_code == 200
    assert client.get("/hospital/1/department/General%20Medicine").status_code == 200


def test_doctor_login_required_for_dashboard():
    response = client.get("/doctor", follow_redirects=False)
    assert response.status_code == 303
    assert "/doctor/login" in response.headers["location"]


def test_qr_scan_requires_auth():
    response = client.get("/doctor/scan/t/not-a-real-token", follow_redirects=False)
    assert response.status_code == 303
    assert "/doctor/login" in response.headers["location"]


def _login_doctor():
    return client.post(
        "/doctor/login",
        data={"medical_number": "MC-10001", "password": "doctor123"},
        follow_redirects=False,
    )


def test_doctor_login_and_offline_pack():
    login = _login_doctor()
    assert login.status_code == 303
    pack = client.get("/api/doctor/offline-pack")
    assert pack.status_code == 200
    assert pack.json()["doctor_id"] == 1


def test_appointment_qr_token_is_not_appointment_id():
    booking = client.post(
        "/payment-success",
        data={
            "name": "Test Patient",
            "gender": "Female",
            "mobile": "9999911111",
            "problem": "Fever",
            "hospital_id": 1,
            "department": "General Medicine",
            "doctor_id": 1,
            "date": "2026-08-28",
            "slot": "10:00 AM",
            "amount": 500,
        },
    )
    assert booking.status_code == 200
    assert "HT-" in booking.text
    db = SessionLocal()
    row = db.query(Appointment).filter(Appointment.mobile == "9999911111").order_by(Appointment.id.desc()).first()
    assert row.qr_token
    assert row.qr_token != row.appointment_id
    assert not row.qr_token.startswith("RX-")
    assert row.hospital_token.startswith("HT-")
    db.close()


def test_prescription_idempotent_sync_and_expiry():
    db = SessionLocal()
    appt = Appointment(
        appointment_id="RX-TESTSYNC",
        name="Sync Patient",
        gender="Male",
        mobile="8888877777",
        problem="Cough",
        hospital_name="Apollo Hospital",
        hospital_id=1,
        doctor_name="Dr. Rahul Sharma",
        doctor_id=1,
        department="General Medicine",
        date="2026-08-28",
        slot="09:00 AM",
        amount=500,
        prescription_status="Pending",
    )
    db.add(appt)
    db.commit()
    db.close()

    _login_doctor()
    payload = {
        "client_sync_id": "offline-uuid-1",
        "appointment_id": "RX-TESTSYNC",
        "medicines": [
            {
                "name": "Paracetamol 500mg",
                "dosage": "1 Tablet",
                "frequency": "1-0-1",
                "instructions": "After food",
                "days": 3,
            }
        ],
        "instructions": "Rest",
        "notes": "Offline note",
        "created_offline_at": "2026-08-28T10:00:00",
    }
    first = client.post("/api/doctor/sync-prescription", json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "SYNCED"
    second = client.post("/api/doctor/sync-prescription", json=payload)
    assert second.json()["status"] == "DUPLICATE"
    conflict = dict(payload)
    conflict["client_sync_id"] = "offline-uuid-2"
    third = client.post("/api/doctor/sync-prescription", json=conflict)
    assert third.json()["status"] == "CONFLICT"

    lookup = client.post("/my-prescription", data={"appointment_id": "RX-TESTSYNC", "mobile": "8888877777"})
    assert lookup.status_code == 200
    assert "Paracetamol" in lookup.text

    db = SessionLocal()
    row = db.query(Appointment).filter(Appointment.appointment_id == "RX-TESTSYNC").first()
    assert row.prescription_expiry_date
    assert row.qr_token_status == "INVALIDATED"
    row.prescription_expiry_date = "2020-01-01T00:00:00"
    db.commit()
    assert is_prescription_expired(row)
    db.close()

    expired_view = client.post("/my-prescription", data={"appointment_id": "RX-TESTSYNC", "mobile": "8888877777"})
    assert "Time of Usage Completed" in expired_view.text
    assert "Paracetamol" not in expired_view.text


def test_staff_cannot_open_prescription_with_id_only():
    login = client.post(
        "/staff/login",
        data={"username": "STAFF-APOLLO", "password": "staff123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    denied = client.post("/staff/helpdesk", data={"appointment_id": "RX-TESTSYNC"})
    assert denied.status_code == 200
    assert "Appointment ID alone is not enough" in denied.text

    db = SessionLocal()
    row = db.query(Appointment).filter(Appointment.appointment_id == "RX-TESTSYNC").first()
    token = row.hospital_token
    db.close()
    allowed = client.post(
        "/staff/helpdesk",
        data={"appointment_id": "RX-TESTSYNC", "hospital_token": token},
    )
    assert allowed.status_code == 200
    assert "Time of Usage Completed" in allowed.text


def test_kiosk_requires_otp_not_guessable_id():
    page = client.get("/kiosk")
    assert page.status_code == 200
    assert "verification" in page.text.lower()


def test_unauthorized_api_sync():
    anon = TestClient(app)
    response = anon.post(
        "/api/doctor/sync-prescription",
        json={"client_sync_id": "x", "appointment_id": "RX-TESTSYNC", "medicines": [{"name": "x", "days": 1}]},
    )
    assert response.status_code == 401
