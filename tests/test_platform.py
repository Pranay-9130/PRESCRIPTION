from fastapi.testclient import TestClient

from main import (
    app,
    SessionLocal,
    Appointment,
    SyncLedger,
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
    assert "next=" in response.headers["location"]
    assert "/doctor/scan/t/not-a-real-token" in response.headers["location"]


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
    db.query(SyncLedger).filter(SyncLedger.appointment_id == "RX-TESTSYNC").delete()
    db.query(Appointment).filter(Appointment.appointment_id == "RX-TESTSYNC").delete()
    db.commit()
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
        hospital_token="HT-TESTSYNC",
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


def test_doctor_password_unlocks_scanned_qr():
    login = client.post(
        "/doctor/login",
        data={
            "medical_number": "MC-10001",
            "password": "doctor123",
            "next": "/doctor/scan/t/not-a-real-token",
        },
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == "/doctor/scan/t/not-a-real-token"
    opened = client.get("/doctor/scan/t/not-a-real-token")
    assert opened.status_code == 200
    assert "not issued" in opened.text.lower() or "invalid" in opened.text.lower()


def test_qr_endpoints_and_base64_data_uri():
    booking = client.post(
        "/payment-success",
        data={
            "name": "QR Test Patient",
            "gender": "Male",
            "mobile": "9876543210",
            "problem": "Headache",
            "hospital_id": 1,
            "department": "General Medicine",
            "doctor_id": 1,
            "date": "2026-09-12",
            "slot": "11:00 AM",
            "amount": 500,
        },
    )
    assert booking.status_code == 200
    assert "data:image/png;base64," in booking.text
    assert "Copy Doctor Pass Link" in booking.text

    db = SessionLocal()
    appt = db.query(Appointment).filter(Appointment.mobile == "9876543210").order_by(Appointment.id.desc()).first()
    appt_id = appt.appointment_id
    token = appt.qr_token
    db.close()

    qr_img = client.get(f"/qr/{appt_id}.png")
    assert qr_img.status_code == 200
    assert qr_img.headers["content-type"] == "image/png"

    token_img = client.get(f"/qr/token/{token}.png")
    assert token_img.status_code == 200
    assert token_img.headers["content-type"] == "image/png"


def test_scanned_qr_unlock_and_consultation_flow():
    # 1. Booking
    booking = client.post(
        "/payment-success",
        data={
            "name": "Ananya Sharma",
            "gender": "Female",
            "mobile": "9123456780",
            "problem": "Seasonal allergies",
            "hospital_id": 1,
            "department": "General Medicine",
            "doctor_id": 1,
            "date": "2026-09-12",
            "slot": "02:00 PM",
            "amount": 500,
        },
    )
    assert booking.status_code == 200

    db = SessionLocal()
    appt = db.query(Appointment).filter(Appointment.mobile == "9123456780").order_by(Appointment.id.desc()).first()
    token = appt.qr_token
    appt_id = appt.appointment_id
    db.close()

    # 2. Fresh anonymous client (simulating doctor scanning QR with their phone camera)
    mobile_client = TestClient(app)
    scan_resp = mobile_client.get(f"/doctor/scan/t/{token}")
    assert scan_resp.status_code == 200
    assert "Scanned QR Pass" in scan_resp.text
    assert "Ananya Sharma" in scan_resp.text
    assert "Dr. Rahul Sharma" in scan_resp.text

    # 3. Wrong password submitted
    wrong_pwd = mobile_client.post(
        "/doctor/unlock-scan",
        data={"token": token, "password": "wrongpassword", "appointment_id": appt_id},
    )
    assert wrong_pwd.status_code == 200
    assert "Incorrect doctor password" in wrong_pwd.text

    # 4. Correct doctor password submitted
    unlock_resp = mobile_client.post(
        "/doctor/unlock-scan",
        data={"token": token, "password": "doctor123", "appointment_id": appt_id},
        follow_redirects=False,
    )
    assert unlock_resp.status_code == 303
    assert f"/doctor/consultation/{appt_id}" in unlock_resp.headers["location"]
    assert "doctor_session" in unlock_resp.headers.get("set-cookie", "")

    # 5. Doctor opens the mobile consultation page
    consult_page = mobile_client.get(f"/doctor/consultation/{appt_id}")
    assert consult_page.status_code == 200
    assert "Ananya Sharma" in consult_page.text
    assert "Prescribe Medicines" in consult_page.text
    assert "Save & Send Prescription" in consult_page.text

    # 6. Doctor adds medicines and submits prescription
    save_rx = mobile_client.post(
        "/doctor/prescription",
        data={
            "appointment_id": appt_id,
            "medicine_name": ["Cetirizine 10mg", "Paracetamol 500mg"],
            "dosage": ["1 Tablet", "1 Tablet"],
            "frequency": ["0-0-1 (Night)", "SOS (When needed)"],
            "usage_instructions": ["After dinner", "After meals"],
            "duration_days": ["5", "3"],
            "instructions": "Drink plenty of warm water and avoid cold drinks.",
            "notes": "Patient reports throat irritation.",
        },
    )
    assert save_rx.status_code == 200
    assert "Prescription Issued & Saved" in save_rx.text
    assert "Send on WhatsApp" in save_rx.text
    assert "Send via SMS" in save_rx.text
    assert "wa.me/919123456780" in save_rx.text
    assert "sms:9123456780" in save_rx.text
    assert "Cetirizine 10mg" in save_rx.text

    # 7. Patient can view on /my-prescription
    patient_view = client.post(
        "/my-prescription",
        data={"appointment_id": appt_id, "mobile": "9123456780"},
    )
    assert patient_view.status_code == 200
    assert "Cetirizine 10mg" in patient_view.text
    assert "Ananya Sharma" in patient_view.text
    assert "mobile-rx-cards" in patient_view.text
    assert "patient-rx-card" in patient_view.text
    assert "Share via WhatsApp" in patient_view.text


def test_mobile_friendly_pass_modal_and_share_elements():
    booking = client.post(
        "/payment-success",
        data={
            "name": "Mobile User",
            "gender": "Female",
            "mobile": "9988776655",
            "problem": "Viral fever checkup",
            "hospital_id": 1,
            "department": "General Medicine",
            "doctor_id": 1,
            "date": "2026-09-12",
            "slot": "10:00 AM",
            "amount": 500,
        },
    )
    assert booking.status_code == 200
    assert "qr-modal-overlay" in booking.text
    assert "Full-Screen QR" in booking.text
    assert "Share Pass on WhatsApp" in booking.text
    assert "Copy Doctor Pass Link" in booking.text

