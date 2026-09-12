import uuid
import qrcode
import os
import socket
import hashlib
import hmac
import json
import secrets
import io
import base64
from typing import Optional
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.datastructures import MutableHeaders

from notifications import build_reference_message, dispatch_notification


app = FastAPI()

SECRET_KEY = os.getenv("RXVAULT_SECRET_KEY", "rxvault_doctor_auth_secret_key_2026")
QR_TOKEN_TTL_HOURS = int(os.getenv("QR_TOKEN_TTL_HOURS", "72"))
KIOSK_OTP_MINUTES = int(os.getenv("KIOSK_OTP_MINUTES", "5"))

HOSPITALS_MAP = {
    1: {"name": "Apollo Hospital", "location": "Hyderabad"},
    2: {"name": "KIMS Hospital", "location": "Hyderabad"},
    3: {"name": "Yashoda Hospital", "location": "Hyderabad"},
    4: {"name": "CARE Hospital", "location": "Hyderabad"}
}

ROLE_DOCTOR = "DOCTOR"
ROLE_STAFF = "HOSPITAL_STAFF"
ROLE_HOSPITAL_ADMIN = "HOSPITAL_ADMIN"
ROLE_SYSTEM_ADMIN = "SYSTEM_ADMIN"
ROLE_PATIENT = "PATIENT"

PWA_HEAD = """
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0284c7">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="RxVault">
<link rel="apple-touch-icon" href="/static/icons/icon-192.png">
"""

PWA_SCRIPTS = """
<script src="/static/js/offline-store.js" defer></script>
<script src="/static/js/sync-status.js" defer></script>
<script src="/static/js/pwa.js" defer></script>
"""


def hash_password(password: str) -> str:
    return hmac.new(SECRET_KEY.encode(), password.encode(), hashlib.sha256).hexdigest()


def verify_password(password: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(password), hashed)


def hmac_value(value: str) -> str:
    return hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def generate_qr_token() -> str:
    return secrets.token_urlsafe(32)


def generate_hospital_token() -> str:
    return "HT-" + secrets.token_hex(4).upper()


def generate_prescription_ref() -> str:
    return "RX-" + secrets.token_hex(3).upper()


def get_host_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def public_base_url(request: Request) -> str:
    env = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RXVAULT_PUBLIC_URL") or "").strip().rstrip("/")
    if env:
        return env
    try:
        if "get_setting" in globals():
            db_val = (get_setting("public_base_url") or "").strip().rstrip("/")
            if db_val:
                return db_val
    except Exception:
        pass

    if request is None:
        return "https://prescription-2sui.onrender.com"

    proto = (
        request.headers.get("x-forwarded-proto")
        or request.headers.get("x-forwarded-protocol")
        or request.headers.get("x-url-scheme")
        or request.url.scheme
        or "http"
    ).split(",")[0].strip()

    cf_visitor = request.headers.get("cf-visitor")
    if cf_visitor and "https" in cf_visitor:
        proto = "https"

    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",")[0].strip()

    if not host:
        return str(request.base_url).rstrip("/")

    host_name = host.split(":")[0].strip()
    port = host.split(":")[1].strip() if ":" in host else ""
    if host_name in ("localhost", "127.0.0.1"):
        lan_ip = get_host_ip()
        if lan_ip and lan_ip != "127.0.0.1":
            port_suffix = f":{port}" if port else ""
            return f"{proto}://{lan_ip}{port_suffix}".rstrip("/")

    return f"{proto}://{host}".rstrip("/")


def request_is_https(request: Request) -> bool:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip().lower()
    return proto == "https"


def scan_path_for_token(token: str) -> str:
    return f"/doctor/scan/t/{token}"


def safe_next_url(next_path: str, default: str = "/doctor") -> str:
    value = (next_path or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value or "://" in value:
        return default
    if not value.startswith("/doctor"):
        return default
    return value


def set_doctor_session_cookie(response, doctor_id, request: Request):
    response.set_cookie(
        key="doctor_session",
        value=str(doctor_id),
        max_age=86400 * 7,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
        path="/",
    )


def digits_only(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def whatsapp_phone(mobile: str) -> str:
    digits = digits_only(mobile)
    if len(digits) == 10:
        return "91" + digits
    if digits.startswith("0") and len(digits) == 11:
        return "91" + digits[1:]
    return digits


def build_prescription_share(patient, request: Request) -> dict:
    medicine_str = getattr(patient, "medicine", "") if not isinstance(patient, dict) else patient.get("medicine", "")
    items = parse_medicine_json(medicine_str)
    base_url = public_base_url(request)

    patient_name = getattr(patient, "name", "") if not isinstance(patient, dict) else patient.get("name", "")
    hospital_name = getattr(patient, "hospital_name", "") if not isinstance(patient, dict) else patient.get("hospital_name", "")
    doctor_name = getattr(patient, "doctor_name", "") if not isinstance(patient, dict) else patient.get("doctor_name", "")
    appointment_id = getattr(patient, "appointment_id", "") if not isinstance(patient, dict) else patient.get("appointment_id", "")
    prescription_ref = getattr(patient, "prescription_ref", "") if not isinstance(patient, dict) else patient.get("prescription_ref", "")
    date_str = getattr(patient, "date", "") if not isinstance(patient, dict) else patient.get("date", "")
    duration = getattr(patient, "max_duration_days", 5) if not isinstance(patient, dict) else patient.get("max_duration_days", 5)
    mobile = getattr(patient, "mobile", "") if not isinstance(patient, dict) else patient.get("mobile", "")

    med_count = len(items) if items else (1 if getattr(patient, "medicine", None) else 0)
    med_text = f"{med_count} Medicine{'s' if med_count != 1 else ''}" if med_count > 0 else "Regimen Prescribed"

    quotes = [
        "“Your health is an investment, not an expense. Consistent care brings lasting recovery.”",
        "“Healing is a journey of trust and timely care. Your wellness is our top priority.”",
        "“Health is the greatest gift. Follow your schedule, stay hydrated, and take care today.”"
    ]
    quote_text = quotes[abs(hash(str(appointment_id))) % len(quotes)]

    clean_mobile = digits_only(mobile)
    portal_rx_url = f"{base_url}/my-prescription"

    # Masked mobile, e.g. ******4708
    if len(clean_mobile) >= 4:
        masked_mobile = "*" * (len(clean_mobile) - 4) + clean_mobile[-4:]
    else:
        masked_mobile = clean_mobile

    # Format date nicely e.g. 12 Sep 2026 if possible, otherwise fallback
    formatted_date = date_str
    if date_str:
        try:
            formatted_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d %b %Y")
        except Exception:
            formatted_date = date_str

    h_name = (hospital_name or "RxVault Accredited Hospital").upper()

    lines = [
        f"🏥 {h_name}",
        "💙 Official Digital Prescription Notice",
        f"Dear {patient_name} 👋",
        "Your attending doctor,",
        f"👨‍⚕️ {doctor_name},",
        "has prepared and digitally signed your prescription.",
        "",
        "🔐 YOUR RxVAULT IS READY",
        f"🆔 Appointment ID: {appointment_id}",
        f"📱 Registered Mobile: {masked_mobile}",
        f"🔖 Security Ref: {prescription_ref or 'RX-VAULT'}",
        f"📅 Consultation Date: {formatted_date or datetime.now().strftime('%d %b %Y')}",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "🔒 To view your full prescription (medicines & instructions), open the link below and enter your Appointment ID and Registered Mobile Number:",
        "",
        f"🔗 {portal_rx_url}",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "💙 RxVault — Your prescription. Your privacy. Your peace of mind.",
        "⚠️ In case of an emergency, contact your healthcare provider immediately."
    ]

    text = "\n".join(lines)
    phone = whatsapp_phone(mobile)
    sms_number = clean_mobile
    sms_text = text

    return {
        "text": text,
        "sms_href": f"sms:{sms_number}?&body={quote(sms_text)}",
        "whatsapp_href": f"https://wa.me/{phone}?text={quote(text)}",
        "mobile": mobile,
        "portal_url": portal_rx_url,
    }


def parse_medicine_json(medicine_str):
    if not medicine_str:
        return []
    try:
        items = json.loads(medicine_str)
        if isinstance(items, list):
            for i, item in enumerate(items):
                item["index"] = i + 1
            return items
    except Exception:
        pass
    meds = [m.strip() for m in medicine_str.split(" | ") if m.strip()]
    items = []
    for i, m in enumerate(meds):
        items.append({
            "index": i + 1,
            "name": m,
            "dosage": "1 Unit",
            "frequency": "As directed",
            "instructions": "Take after meals",
            "days": 5
        })
    return items


def prescription_expiry_dt(appointment):
    if not appointment:
        return None
    if getattr(appointment, "prescription_expiry_date", None):
        try:
            return datetime.fromisoformat(appointment.prescription_expiry_date)
        except Exception:
            pass
    if not appointment.prescription_date or not appointment.max_duration_days:
        return None
    try:
        saved_dt = datetime.fromisoformat(appointment.prescription_date)
        return saved_dt + timedelta(days=int(appointment.max_duration_days))
    except Exception:
        return None


def is_prescription_expired(appointment):
    expiry_dt = prescription_expiry_dt(appointment)
    if not expiry_dt:
        return False
    return datetime.now() > expiry_dt


def get_patient_history(mobile_number, current_appointment_id, db):
    if not mobile_number:
        return []
    past_apps = db.query(Appointment).filter(
        Appointment.mobile == mobile_number,
        Appointment.appointment_id != current_appointment_id,
        Appointment.prescription_status == "Saved"
    ).order_by(Appointment.id.desc()).all()

    history = []
    for app_item in past_apps:
        items = parse_medicine_json(app_item.medicine)
        history.append({
            "appointment_id": app_item.appointment_id,
            "date": app_item.date,
            "hospital_name": app_item.hospital_name,
            "doctor_name": app_item.doctor_name,
            "department": app_item.department,
            "problem": app_item.problem,
            "instructions": app_item.instructions,
            "medicine_list": items
        })
    return history


# ---------------- DATABASE ----------------

DATABASE_URL = "sqlite:///./rxvault.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    appointment_id = Column(String, unique=True, index=True)
    name = Column(String)
    gender = Column(String)
    mobile = Column(String)
    problem = Column(Text)
    hospital_name = Column(String)
    doctor_name = Column(String)
    department = Column(String)
    date = Column(String)
    slot = Column(String)
    amount = Column(Integer)
    medicine = Column(Text, nullable=True)
    dosage = Column(String, nullable=True)
    instructions = Column(Text, nullable=True)
    prescription_status = Column(String, default="Pending")
    prescription_date = Column(String, nullable=True)
    max_duration_days = Column(Integer, default=5)
    hospital_id = Column(Integer, nullable=True)
    doctor_id = Column(Integer, nullable=True)
    qr_token = Column(String, unique=True, index=True, nullable=True)
    qr_token_created_at = Column(String, nullable=True)
    qr_token_expires_at = Column(String, nullable=True)
    qr_token_status = Column(String, default="ACTIVE")
    hospital_token = Column(String, index=True, nullable=True)
    prescription_ref = Column(String, unique=True, index=True, nullable=True)
    prescription_expiry_date = Column(String, nullable=True)
    prescription_created_at = Column(String, nullable=True)
    consultation_notes = Column(Text, nullable=True)
    print_count = Column(Integer, default=0)


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    medical_number = Column(String, unique=True, index=True)
    name = Column(String)
    age = Column(Integer)
    experience = Column(String)
    email = Column(String, unique=True, index=True)
    specialty = Column(String)
    password_hash = Column(String)
    hospital_id = Column(Integer, default=1)
    hospital_name = Column(String, default="Apollo Hospital")
    is_active = Column(Boolean, default=True)


class StaffUser(Base):
    __tablename__ = "staff_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    full_name = Column(String)
    role = Column(String)
    hospital_id = Column(Integer, nullable=True)
    hospital_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)


class SyncLedger(Base):
    __tablename__ = "sync_ledger"

    id = Column(Integer, primary_key=True, index=True)
    client_sync_id = Column(String, unique=True, index=True)
    appointment_id = Column(String, index=True)
    doctor_id = Column(Integer)
    payload_hash = Column(String)
    status = Column(String, default="SYNCED")
    created_offline_at = Column(String, nullable=True)
    synced_at = Column(String)
    error_status = Column(String, nullable=True)


class PrintLog(Base):
    __tablename__ = "print_logs"

    id = Column(Integer, primary_key=True, index=True)
    appointment_id = Column(String, index=True)
    staff_id = Column(Integer, nullable=True)
    identifier_used = Column(String)
    printed_at = Column(String)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    appointment_id = Column(String, index=True)
    channel = Column(String)
    destination = Column(String)
    message = Column(Text)
    status = Column(String)
    provider = Column(String)
    created_at = Column(String)


class KioskOtp(Base):
    __tablename__ = "kiosk_otps"

    id = Column(Integer, primary_key=True, index=True)
    appointment_id = Column(String, index=True)
    otp_hash = Column(String)
    expires_at = Column(String)
    attempts = Column(Integer, default=0)
    created_at = Column(String)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)
    value = Column(String)


Base.metadata.create_all(bind=engine)


def update_db_schema():
    import sqlite3
    try:
        conn = sqlite3.connect("./rxvault.db")
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(doctors);")
        cols = [row[1] for row in cursor.fetchall()]
        if "hospital_name" not in cols:
            cursor.execute("ALTER TABLE doctors ADD COLUMN hospital_name TEXT DEFAULT 'Apollo Hospital';")
        if "hospital_id" not in cols:
            cursor.execute("ALTER TABLE doctors ADD COLUMN hospital_id INTEGER DEFAULT 1;")
        if "is_active" not in cols:
            cursor.execute("ALTER TABLE doctors ADD COLUMN is_active INTEGER DEFAULT 1;")

        cursor.execute("PRAGMA table_info(appointments);")
        app_cols = [row[1] for row in cursor.fetchall()]
        extra_app = {
            "prescription_date": "TEXT",
            "max_duration_days": "INTEGER DEFAULT 5",
            "hospital_id": "INTEGER",
            "doctor_id": "INTEGER",
            "qr_token": "TEXT",
            "qr_token_created_at": "TEXT",
            "qr_token_expires_at": "TEXT",
            "qr_token_status": "TEXT DEFAULT 'ACTIVE'",
            "hospital_token": "TEXT",
            "prescription_ref": "TEXT",
            "prescription_expiry_date": "TEXT",
            "prescription_created_at": "TEXT",
            "consultation_notes": "TEXT",
            "print_count": "INTEGER DEFAULT 0",
        }
        for col, ddl in extra_app.items():
            if col not in app_cols:
                cursor.execute(f"ALTER TABLE appointments ADD COLUMN {col} {ddl};")

        conn.commit()
        conn.close()
    except Exception as e:
        print("Schema update notice:", e)


update_db_schema()


def get_setting(key, default=""):
    db = SessionLocal()
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    value = row.value if row else default
    db.close()
    return value


def set_setting(key, value):
    db = SessionLocal()
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = str(value)
    else:
        db.add(SystemSetting(key=key, value=str(value)))
    db.commit()
    db.close()


def seed_default_doctors():
    db = SessionLocal()
    if db.query(Doctor).count() == 0:
        default_docs = [
            {"medical_number": "MC-10001", "name": "Dr. Rahul Sharma", "age": 38, "experience": "10 Years Experience", "email": "rahul.sharma@rxvault.org", "specialty": "General Medicine", "hospital_id": 1, "hospital_name": "Apollo Hospital"},
            {"medical_number": "MC-10002", "name": "Dr. Anjali Reddy", "age": 35, "experience": "8 Years Experience", "email": "anjali.reddy@rxvault.org", "specialty": "General Medicine", "hospital_id": 2, "hospital_name": "KIMS Hospital"},
            {"medical_number": "MC-10003", "name": "Dr. Vikram Rao", "age": 44, "experience": "15 Years Experience", "email": "vikram.rao@rxvault.org", "specialty": "Cardiology", "hospital_id": 3, "hospital_name": "Yashoda Hospital"},
            {"medical_number": "MC-10004", "name": "Dr. Sneha Kumar", "age": 41, "experience": "12 Years Experience", "email": "sneha.kumar@rxvault.org", "specialty": "Cardiology", "hospital_id": 4, "hospital_name": "CARE Hospital"},
            {"medical_number": "MC-10005", "name": "Dr. Priya Sharma", "age": 37, "experience": "9 Years Experience", "email": "priya.sharma@rxvault.org", "specialty": "Dermatology", "hospital_id": 1, "hospital_name": "Apollo Hospital"},
            {"medical_number": "MC-10006", "name": "Dr. Kiran Reddy", "age": 34, "experience": "7 Years Experience", "email": "kiran.reddy@rxvault.org", "specialty": "Dermatology", "hospital_id": 2, "hospital_name": "KIMS Hospital"},
            {"medical_number": "MC-10007", "name": "Dr. Arjun Kumar", "age": 43, "experience": "14 Years Experience", "email": "arjun.kumar@rxvault.org", "specialty": "Orthopedics", "hospital_id": 3, "hospital_name": "Yashoda Hospital"},
            {"medical_number": "MC-10008", "name": "Dr. Sandeep Rao", "age": 40, "experience": "11 Years Experience", "email": "sandeep.rao@rxvault.org", "specialty": "Orthopedics", "hospital_id": 4, "hospital_name": "CARE Hospital"},
        ]
        for d in default_docs:
            doc = Doctor(
                medical_number=d["medical_number"],
                name=d["name"],
                age=d["age"],
                experience=d["experience"],
                email=d["email"],
                specialty=d["specialty"],
                hospital_id=d["hospital_id"],
                hospital_name=d["hospital_name"],
                password_hash=hash_password("doctor123"),
                is_active=True
            )
            db.add(doc)
        db.commit()
    db.close()


def seed_staff_and_settings():
    db = SessionLocal()
    defaults = [
        ("STAFF-APOLLO", "staff123", "Apollo Reception", ROLE_STAFF, 1, "Apollo Hospital"),
        ("ADMIN-APOLLO", "admin123", "Apollo Hospital Admin", ROLE_HOSPITAL_ADMIN, 1, "Apollo Hospital"),
        ("SYSADMIN", "sysadmin123", "RxVault System Admin", ROLE_SYSTEM_ADMIN, None, None),
    ]
    for username, password, full_name, role, hospital_id, hospital_name in defaults:
        existing = db.query(StaffUser).filter(StaffUser.username == username).first()
        if not existing:
            db.add(StaffUser(
                username=username,
                password_hash=hash_password(password),
                full_name=full_name,
                role=role,
                hospital_id=hospital_id,
                hospital_name=hospital_name,
                is_active=True
            ))
    if not db.query(SystemSetting).filter(SystemSetting.key == "qr_token_ttl_hours").first():
        db.add(SystemSetting(key="qr_token_ttl_hours", value=str(QR_TOKEN_TTL_HOURS)))
    if not db.query(SystemSetting).filter(SystemSetting.key == "notification_provider").first():
        db.add(SystemSetting(key="notification_provider", value=os.getenv("NOTIFICATION_PROVIDER", "console")))
    db.commit()
    db.close()


seed_default_doctors()
seed_staff_and_settings()

os.makedirs("static/qr_codes", exist_ok=True)
os.makedirs("static/icons", exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def get_current_doctor(request: Request):
    doctor_id = request.cookies.get("doctor_session")
    if not doctor_id:
        return None
    try:
        db = SessionLocal()
        doc = db.query(Doctor).filter(Doctor.id == int(doctor_id)).first()
        db.close()
        if doc and getattr(doc, "is_active", True) in (1, True, None):
            return doc
        return None
    except Exception:
        return None


def get_current_staff(request: Request):
    staff_id = request.cookies.get("staff_session")
    if not staff_id:
        return None
    try:
        db = SessionLocal()
        staff = db.query(StaffUser).filter(StaffUser.id == int(staff_id), StaffUser.is_active == True).first()  # noqa: E712
        db.close()
        return staff
    except Exception:
        return None


def page_ctx(request: Request, **kwargs):
    ctx = {
        "current_doctor": get_current_doctor(request),
        "current_staff": get_current_staff(request),
    }
    ctx.update(kwargs)
    return ctx


def doctor_can_access_appointment(doctor, appointment) -> bool:
    if not doctor or not appointment:
        return False
    if appointment.doctor_id and appointment.doctor_id == doctor.id:
        return True
    if appointment.doctor_name and appointment.doctor_name == doctor.name:
        return True
    if appointment.hospital_id and appointment.hospital_id == doctor.hospital_id:
        return True
    if appointment.hospital_name and appointment.hospital_name == doctor.hospital_name:
        return True
    return False


def staff_can_access_appointment(staff, appointment) -> bool:
    if not staff or not appointment:
        return False
    if staff.role == ROLE_SYSTEM_ADMIN:
        return True
    if staff.hospital_id and appointment.hospital_id == staff.hospital_id:
        return True
    if staff.hospital_name and appointment.hospital_name == staff.hospital_name:
        return True
    return False


def qr_token_is_valid(appointment) -> tuple:
    if not appointment or not appointment.qr_token:
        return False, "This QR pass is not recognized."
    status = (appointment.qr_token_status or "ACTIVE").upper()
    if status == "INVALIDATED":
        return False, "This QR pass was invalidated after consultation."
    if status == "EXPIRED":
        return False, "This QR pass has expired."
    if appointment.qr_token_expires_at:
        try:
            if datetime.now() > datetime.fromisoformat(appointment.qr_token_expires_at):
                return False, "This QR pass has expired. Please verify the appointment at the help desk."
        except Exception:
            pass
    return True, ""


def assign_qr_token(appointment, ttl_hours=None):
    hours = ttl_hours or int(get_setting("qr_token_ttl_hours", QR_TOKEN_TTL_HOURS) or QR_TOKEN_TTL_HOURS)
    now = datetime.now()
    appointment.qr_token = generate_qr_token()
    appointment.qr_token_created_at = now.isoformat()
    appointment.qr_token_expires_at = (now + timedelta(hours=hours)).isoformat()
    appointment.qr_token_status = "ACTIVE"
    return appointment.qr_token


def generate_qr_data_uri(scan_url: str) -> str:
    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=3,
        )
        qr.add_data(scan_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        return ""


def save_qr_image(scan_url, appointment_id):
    data_uri = generate_qr_data_uri(scan_url)
    try:
        os.makedirs("static/qr_codes", exist_ok=True)
        qr = qrcode.make(scan_url)
        qr_filename = f"{appointment_id}.png"
        qr_path = os.path.join("static", "qr_codes", qr_filename)
        qr.save(qr_path)
    except Exception:
        pass
    return data_uri if data_uri else f"/static/qr_codes/{appointment_id}.png"


def patient_summary(patient, include_history=False, db=None, hide_expired_meds=False):
    if not patient:
        return None
    expired = is_prescription_expired(patient)
    items = parse_medicine_json(patient.medicine)
    if hide_expired_meds and expired:
        items = []
    data = {
        "appointment_id": patient.appointment_id,
        "name": patient.name,
        "gender": patient.gender,
        "mobile": patient.mobile,
        "problem": patient.problem,
        "hospital_name": patient.hospital_name,
        "hospital_id": patient.hospital_id,
        "doctor_name": patient.doctor_name,
        "doctor_id": patient.doctor_id,
        "department": patient.department,
        "date": patient.date,
        "slot": patient.slot,
        "prescription_status": patient.prescription_status,
        "prescription_ref": patient.prescription_ref,
        "hospital_token": patient.hospital_token,
        "medicine_list": items,
        "instructions": patient.instructions,
        "consultation_notes": patient.consultation_notes,
        "is_expired": expired,
        "max_duration_days": patient.max_duration_days or 5,
        "prescription_date": patient.prescription_date,
        "prescription_expiry_date": patient.prescription_expiry_date,
        "prescription_created_at": patient.prescription_created_at,
        "qr_token": patient.qr_token,
        "qr_token_expires_at": patient.qr_token_expires_at,
        "qr_token_status": patient.qr_token_status,
    }
    if include_history and db is not None:
        data["history"] = get_patient_history(patient.mobile, patient.appointment_id, db)
    return data


def apply_prescription(patient, medicine_items, instructions, notes=None):
    max_days = 5
    for item in medicine_items:
        try:
            days = int(item.get("days") or 5)
        except Exception:
            days = 5
        item["days"] = max(1, min(days, 90))
        if item["days"] > max_days:
            max_days = item["days"]
    now = datetime.now()
    expiry = now + timedelta(days=max_days)
    patient.medicine = json.dumps(medicine_items)
    patient.dosage = ", ".join([f"{item['name']} ({item['days']}d)" for item in medicine_items])
    patient.instructions = instructions
    patient.consultation_notes = notes or patient.consultation_notes
    patient.prescription_status = "Saved"
    patient.prescription_date = now.isoformat()
    patient.prescription_created_at = now.isoformat()
    patient.prescription_expiry_date = expiry.isoformat()
    patient.max_duration_days = max_days
    if not patient.prescription_ref:
        patient.prescription_ref = generate_prescription_ref()
    patient.qr_token_status = "INVALIDATED"
    return patient


def send_prescription_notice(patient):
    if not patient or not patient.mobile:
        return None
    message = build_reference_message(
        patient.appointment_id,
        patient.prescription_ref,
        patient.hospital_name or "Hospital",
    )
    result = dispatch_notification(
        destination=patient.mobile,
        message=message,
        appointment_id=patient.appointment_id,
        prescription_ref=patient.prescription_ref,
    )
    db = SessionLocal()
    db.add(NotificationLog(
        appointment_id=patient.appointment_id,
        channel="sms_reference",
        destination=patient.mobile,
        message=message,
        status=result.get("status"),
        provider=result.get("provider"),
        created_at=datetime.now().isoformat(),
    ))
    db.commit()
    db.close()
    return result


def json_safe(data):
    return json.dumps(data, default=str)


# ---------------- HOME PAGE ----------------

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context=page_ctx(request)
    )


@app.get("/offline")
def offline_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="offline.html",
        context=page_ctx(request)
    )


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/manifest.json")
def manifest():
    return FileResponse("static/manifest.json", media_type="application/manifest+json")


@app.get("/api/health")
def api_health():
    return {"ok": True, "time": datetime.now().isoformat()}


@app.get("/api/me")
def api_me(request: Request):
    doctor = get_current_doctor(request)
    staff = get_current_staff(request)
    if doctor:
        material = hmac_value(f"doctor:{doctor.id}:{doctor.medical_number}")
        return {
            "authenticated": True,
            "role": ROLE_DOCTOR,
            "id": doctor.id,
            "name": doctor.name,
            "hospital_id": doctor.hospital_id,
            "offline_key_material": material,
        }
    if staff:
        material = hmac_value(f"staff:{staff.id}:{staff.username}")
        return {
            "authenticated": True,
            "role": staff.role,
            "id": staff.id,
            "name": staff.full_name,
            "hospital_id": staff.hospital_id,
            "offline_key_material": material,
        }
    return {"authenticated": False, "role": ROLE_PATIENT}


# ---------------- ALL HOSPITALS ----------------

@app.get("/hospitals")
def hospitals(request: Request):
    db = SessionLocal()
    hospital_list = []
    for h_id, h_data in HOSPITALS_MAP.items():
        doc_count = db.query(Doctor).filter(Doctor.hospital_id == h_id).count()
        hospital_list.append({
            "id": h_id,
            "name": h_data["name"],
            "location": h_data["location"],
            "doctors": max(doc_count, 1)
        })
    db.close()

    return templates.TemplateResponse(
        request=request,
        name="hospitals.html",
        context=page_ctx(request, hospitals=hospital_list)
    )


@app.get("/hospital/{hospital_id}")
def hospital_details(hospital_id: int, request: Request):
    hospital = HOSPITALS_MAP.get(hospital_id)
    departments = ["General Medicine", "Cardiology", "Dermatology", "Orthopedics"]

    return templates.TemplateResponse(
        request=request,
        name="hospital.html",
        context=page_ctx(
            request,
            hospital=hospital,
            hospital_id=hospital_id,
            departments=departments,
        )
    )


@app.get("/hospital/{hospital_id}/department/{department}")
def doctors(hospital_id: int, department: str, request: Request):
    hospital_data = HOSPITALS_MAP.get(hospital_id, {"name": "Hospital"})
    hospital_name = hospital_data["name"]

    db = SessionLocal()
    db_doctors = db.query(Doctor).filter(
        Doctor.hospital_id == hospital_id,
        Doctor.specialty == department
    ).all()

    if not db_doctors:
        db_doctors = db.query(Doctor).filter(Doctor.specialty == department).all()

    db.close()

    doctors_list = [
        {"id": d.id, "name": d.name, "experience": d.experience, "medical_number": d.medical_number, "hospital_name": d.hospital_name}
        for d in db_doctors
        if getattr(d, "is_active", True) in (1, True, None)
    ]

    return templates.TemplateResponse(
        request=request,
        name="doctors.html",
        context=page_ctx(
            request,
            hospital_name=hospital_name,
            hospital_id=hospital_id,
            department=department,
            doctors=doctors_list,
        )
    )


@app.get("/appointment/{hospital_id}/{department}/{doctor_id}")
def appointment(hospital_id: int, department: str, doctor_id: int, request: Request):
    db = SessionLocal()
    doc = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    db.close()

    doctor_name = doc.name if doc else f"Doctor #{doctor_id}"
    today = datetime.now().date()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(3)]
    slots = [
        {"time": "09:00 AM", "status": "Available"},
        {"time": "09:30 AM", "status": "Booked"},
        {"time": "10:00 AM", "status": "Available"},
        {"time": "10:30 AM", "status": "Available"},
        {"time": "11:00 AM", "status": "Booked"},
        {"time": "11:30 AM", "status": "Available"},
        {"time": "02:00 PM", "status": "Available"},
        {"time": "02:30 PM", "status": "Booked"}
    ]

    return templates.TemplateResponse(
        request=request,
        name="appointment.html",
        context=page_ctx(
            request,
            hospital_id=hospital_id,
            department=department,
            doctor_id=doctor_id,
            doctor_name=doctor_name,
            dates=dates,
            slots=slots,
        )
    )


@app.get("/register/{hospital_id}/{department}/{doctor_id}")
def register_page(hospital_id: int, department: str, doctor_id: int, date: str, slot: str, request: Request):
    db = SessionLocal()
    doc = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    db.close()

    doctor_name = doc.name if doc else f"Doctor #{doctor_id}"

    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context=page_ctx(
            request,
            hospital_id=hospital_id,
            department=department,
            doctor_id=doctor_id,
            doctor_name=doctor_name,
            date=date,
            slot=slot,
        )
    )


@app.post("/register")
def submit_registration(
    request: Request,
    name: str = Form(...),
    gender: str = Form(...),
    mobile: str = Form(...),
    problem: str = Form(""),
    hospital_id: int = Form(...),
    department: str = Form(...),
    doctor_id: int = Form(...),
    date: str = Form(...),
    slot: str = Form(...)
):
    return templates.TemplateResponse(
        request=request,
        name="confirmation.html",
        context=page_ctx(
            request,
            name=name,
            gender=gender,
            mobile=mobile,
            problem=problem,
            hospital_id=hospital_id,
            department=department,
            doctor_id=doctor_id,
            date=date,
            slot=slot,
        )
    )


@app.post("/payment")
def payment_page(
    request: Request,
    name: str = Form(...),
    gender: str = Form(...),
    mobile: str = Form(...),
    problem: str = Form(""),
    hospital_id: int = Form(...),
    department: str = Form(...),
    doctor_id: int = Form(...),
    date: str = Form(...),
    slot: str = Form(...)
):
    db = SessionLocal()
    doc = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    db.close()
    doctor_name = doc.name if doc else "Unknown Doctor"
    hospital_name = HOSPITALS_MAP.get(hospital_id, {}).get("name", "Unknown Hospital")

    return templates.TemplateResponse(
        request=request,
        name="payment.html",
        context=page_ctx(
            request,
            name=name,
            gender=gender,
            mobile=mobile,
            problem=problem,
            hospital_id=hospital_id,
            hospital_name=hospital_name,
            department=department,
            doctor_id=doctor_id,
            doctor_name=doctor_name,
            date=date,
            slot=slot,
        )
    )


@app.post("/payment-success")
def payment_success(
    request: Request,
    name: str = Form(...),
    gender: str = Form(...),
    mobile: str = Form(...),
    problem: str = Form(""),
    hospital_id: int = Form(...),
    department: str = Form(...),
    doctor_id: int = Form(...),
    date: str = Form(...),
    slot: str = Form(...),
    amount: int = Form(...)
):
    db = SessionLocal()
    doc = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    doctor_name = doc.name if doc else "Unknown Doctor"
    hospital_name = HOSPITALS_MAP.get(hospital_id, {}).get("name", "Unknown Hospital")
    appointment_id = "RX-" + str(uuid.uuid4())[:8].upper()

    new_appointment = Appointment(
        appointment_id=appointment_id,
        name=name,
        gender=gender,
        mobile=mobile,
        problem=problem,
        hospital_name=hospital_name,
        hospital_id=hospital_id,
        doctor_name=doctor_name,
        doctor_id=doctor_id,
        department=department,
        date=date,
        slot=slot,
        amount=amount,
        prescription_status="Pending",
        hospital_token=generate_hospital_token(),
    )
    assign_qr_token(new_appointment)
    db.add(new_appointment)
    db.commit()

    scan_url = f"{public_base_url(request)}{scan_path_for_token(new_appointment.qr_token)}"
    qr_code = save_qr_image(scan_url, appointment_id)
    hospital_token = new_appointment.hospital_token
    db.close()

    return templates.TemplateResponse(
        request=request,
        name="success.html",
        context=page_ctx(
            request,
            appointment_id=appointment_id,
            name=name,
            mobile=mobile,
            hospital_name=hospital_name,
            doctor_name=doctor_name,
            department=department,
            date=date,
            slot=slot,
            amount=amount,
            qr_code=qr_code,
            hospital_token=hospital_token,
            scan_url=scan_url,
        )
    )


# ==========================================================================
# DOCTOR AUTHENTICATION & REGISTRATION ENDPOINTS
# ==========================================================================

@app.get("/doctor/register")
def doctor_register_page(request: Request):
    doctor = get_current_doctor(request)
    if doctor:
        return RedirectResponse(url="/doctor", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="doctor_register.html",
        context=page_ctx(request)
    )


@app.post("/doctor/register")
def doctor_register_submit(
    request: Request,
    name: str = Form(...),
    age: int = Form(...),
    experience: str = Form(...),
    email: str = Form(...),
    specialty: str = Form(...),
    hospital_id: int = Form(...),
    medical_number: str = Form(...),
    password: str = Form(...)
):
    medical_number = medical_number.strip().upper()
    email = email.strip().lower()
    db = SessionLocal()

    existing_med = db.query(Doctor).filter(Doctor.medical_number == medical_number).first()
    if existing_med:
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="doctor_register.html",
            context=page_ctx(request, error=f"Medical License Number '{medical_number}' is already registered.")
        )

    existing_email = db.query(Doctor).filter(Doctor.email == email).first()
    if existing_email:
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="doctor_register.html",
            context=page_ctx(request, error=f"Email address '{email}' is already registered.")
        )

    exp_str = f"{experience} Years Experience" if not str(experience).lower().endswith("experience") else experience
    hospital_name = HOSPITALS_MAP.get(hospital_id, {}).get("name", "Apollo Hospital")
    formatted_name = f"Dr. {name}" if not name.lower().startswith("dr.") else name

    new_doc = Doctor(
        medical_number=medical_number,
        name=formatted_name,
        age=age,
        experience=exp_str,
        email=email,
        specialty=specialty,
        hospital_id=hospital_id,
        hospital_name=hospital_name,
        password_hash=hash_password(password),
        is_active=True
    )

    db.add(new_doc)
    db.commit()
    db.close()

    return templates.TemplateResponse(
        request=request,
        name="doctor_login.html",
        context=page_ctx(
            request,
            success=f"Registration successful for {formatted_name}! Please login with your Medical License Number ({medical_number}) and Password.",
            medical_number=medical_number
        )
    )


@app.get("/doctor/login")
def doctor_login_page(request: Request, next: str = ""):
    next_url = safe_next_url(next)
    doctor = get_current_doctor(request)
    if doctor:
        return RedirectResponse(url=next_url, status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="doctor_login.html",
        context=page_ctx(
            request,
            next_url=next_url,
            scan_unlock="/doctor/scan" in next_url,
        )
    )


@app.post("/doctor/login")
def doctor_login_submit(
    request: Request,
    medical_number: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
):
    next_url = safe_next_url(next)
    medical_number = medical_number.strip().upper()
    db = SessionLocal()

    doc = db.query(Doctor).filter(Doctor.medical_number == medical_number).first()
    if not doc or not verify_password(password, doc.password_hash) or getattr(doc, "is_active", True) not in (1, True, None):
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="doctor_login.html",
            context=page_ctx(
                request,
                error="Invalid Medical License Number or Password.",
                next_url=next_url,
                scan_unlock="/doctor/scan" in next_url,
            )
        )

    doc_id = doc.id
    db.close()

    response = RedirectResponse(url=next_url, status_code=303)
    set_doctor_session_cookie(response, doc_id, request)
    return response


@app.get("/doctor/logout")
def doctor_logout():
    response = RedirectResponse(url="/doctor/login", status_code=303)
    response.delete_cookie(key="doctor_session")
    return response


def render_doctor_dashboard(request, doctor, patient=None, appointment_id=None, previous_visits=None, error=None):
    return templates.TemplateResponse(
        request=request,
        name="doctor_dashboard.html",
        context=page_ctx(
            request,
            current_doctor=doctor,
            patient=patient,
            appointment_id=appointment_id,
            previous_visits=previous_visits or [],
            error=error,
            patient_json=json_safe(patient) if patient else "null",
            history_json=json_safe(previous_visits or []),
        )
    )


@app.get("/doctor")
def doctor_dashboard(request: Request):
    doctor = get_current_doctor(request)
    if not doctor:
        return RedirectResponse(url="/doctor/login", status_code=303)
    return render_doctor_dashboard(request, doctor)


def load_patient_for_doctor(db, doctor, appointment_id):
    appointment_id = (appointment_id or "").strip().upper()
    patient = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    if not patient:
        return None, None, "No appointment found."
    if not doctor_can_access_appointment(doctor, patient):
        return None, None, "You are not authorized to open this patient's record."
    previous_visits = get_patient_history(patient.mobile, appointment_id, db)
    return patient_summary(patient), previous_visits, None


@app.get("/qr/{appointment_id}.png")
def serve_appointment_qr_image(appointment_id: str):
    appointment_id = appointment_id.strip().upper()
    db = SessionLocal()
    appointment = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    db.close()
    token = appointment.qr_token if appointment and appointment.qr_token else appointment_id
    scan_url = f"/doctor/scan/t/{token}"
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=3)
    qr.add_data(scan_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/qr/token/{token}.png")
def serve_token_qr_image(token: str):
    token = token.strip()
    scan_url = f"/doctor/scan/t/{token}"
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=3)
    qr.add_data(scan_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/doctor/scan/t/{token}")
def scan_appointment_token(token: str, request: Request):
    token = (token or "").strip()
    doctor = get_current_doctor(request)

    db = SessionLocal()
    patient_row = db.query(Appointment).filter(Appointment.qr_token == token).first()
    if not patient_row:
        patient_row = db.query(Appointment).filter(Appointment.appointment_id == token.upper()).first()

    if not patient_row:
        db.close()
        if not doctor:
            next_url = scan_path_for_token(token)
            return RedirectResponse(url=f"/doctor/login?next={quote(next_url, safe='/')}", status_code=303)
        return render_doctor_dashboard(request, doctor, None, token, [], "This QR pass is invalid or was not issued by RxVault.")

    valid, message = qr_token_is_valid(patient_row)
    if not valid:
        db.close()
        if not doctor:
            return templates.TemplateResponse(
                request=request,
                name="doctor_unlock.html",
                context=page_ctx(request, patient=patient_row, token=token, error=message),
            )
        return render_doctor_dashboard(request, doctor, None, patient_row.appointment_id, [], message)

    if not doctor:
        patient_data = {
            "appointment_id": patient_row.appointment_id,
            "name": patient_row.name,
            "gender": patient_row.gender,
            "mobile": patient_row.mobile,
            "problem": patient_row.problem,
            "hospital_name": patient_row.hospital_name,
            "hospital_id": patient_row.hospital_id,
            "doctor_name": patient_row.doctor_name,
            "doctor_id": patient_row.doctor_id,
            "department": patient_row.department,
            "date": patient_row.date,
            "slot": patient_row.slot,
        }
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="doctor_unlock.html",
            context=page_ctx(request, patient=patient_data, token=token),
        )

    if not doctor_can_access_appointment(doctor, patient_row):
        patient_data = {
            "appointment_id": patient_row.appointment_id,
            "name": patient_row.name,
            "gender": patient_row.gender,
            "mobile": patient_row.mobile,
            "problem": patient_row.problem,
            "hospital_name": patient_row.hospital_name,
            "hospital_id": patient_row.hospital_id,
            "doctor_name": patient_row.doctor_name,
            "doctor_id": patient_row.doctor_id,
            "department": patient_row.department,
            "date": patient_row.date,
            "slot": patient_row.slot,
        }
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="doctor_unlock.html",
            context=page_ctx(
                request,
                patient=patient_data,
                token=token,
                error=f"Signed in as Dr. {doctor.name}, but this appointment is assigned to {patient_row.doctor_name}. Enter the assigned doctor's password to switch.",
            ),
        )

    appointment_id = patient_row.appointment_id
    db.close()
    return RedirectResponse(url=f"/doctor/consultation/{appointment_id}", status_code=303)


@app.get("/doctor/scan/{appointment_id}")
def scan_appointment_qr(appointment_id: str, request: Request):
    appointment_id = appointment_id.strip()
    db = SessionLocal()
    patient_row = db.query(Appointment).filter(Appointment.qr_token == appointment_id).first()
    if patient_row:
        db.close()
        return RedirectResponse(url=f"/doctor/scan/t/{appointment_id}", status_code=303)

    patient_row = db.query(Appointment).filter(Appointment.appointment_id == appointment_id.upper()).first()
    if patient_row and patient_row.qr_token:
        db.close()
        return RedirectResponse(url=f"/doctor/scan/t/{patient_row.qr_token}", status_code=303)

    doctor = get_current_doctor(request)
    if not patient_row:
        db.close()
        if not doctor:
            next_url = f"/doctor/scan/{appointment_id}"
            return RedirectResponse(url=f"/doctor/login?next={quote(next_url, safe='/')}", status_code=303)
        return render_doctor_dashboard(request, doctor, None, appointment_id, [], f"No appointment found matching Appointment ID: {appointment_id}")

    if not doctor:
        patient_data = {
            "appointment_id": patient_row.appointment_id,
            "name": patient_row.name,
            "gender": patient_row.gender,
            "mobile": patient_row.mobile,
            "problem": patient_row.problem,
            "hospital_name": patient_row.hospital_name,
            "hospital_id": patient_row.hospital_id,
            "doctor_name": patient_row.doctor_name,
            "doctor_id": patient_row.doctor_id,
            "department": patient_row.department,
            "date": patient_row.date,
            "slot": patient_row.slot,
        }
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="doctor_unlock.html",
            context=page_ctx(request, patient=patient_data, token=patient_row.qr_token or appointment_id),
        )

    if not doctor_can_access_appointment(doctor, patient_row):
        db.close()
        return render_doctor_dashboard(request, doctor, None, appointment_id, [], "You are not authorized to view this patient.")

    target_id = patient_row.appointment_id
    db.close()
    return RedirectResponse(url=f"/doctor/consultation/{target_id}", status_code=303)


@app.post("/doctor/unlock-scan")
def unlock_scanned_appointment(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    appointment_id: str = Form(""),
    doctor_id: str = Form(""),
    medical_number: str = Form(""),
):
    token = token.strip()
    db = SessionLocal()
    patient_row = db.query(Appointment).filter(Appointment.qr_token == token).first()
    if not patient_row and appointment_id:
        patient_row = db.query(Appointment).filter(Appointment.appointment_id == appointment_id.strip().upper()).first()

    if not patient_row:
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="doctor_unlock.html",
            context=page_ctx(request, error="Appointment record not found for this QR pass."),
        )

    matched_doc = None
    if medical_number.strip():
        med = medical_number.strip().upper()
        doc = db.query(Doctor).filter(Doctor.medical_number == med).first()
        if doc and verify_password(password, doc.password_hash):
            matched_doc = doc
    else:
        doc = None
        if patient_row.doctor_id:
            doc = db.query(Doctor).filter(Doctor.id == patient_row.doctor_id).first()
        if not doc and patient_row.doctor_name:
            doc = db.query(Doctor).filter(Doctor.name == patient_row.doctor_name).first()
        if doc and verify_password(password, doc.password_hash):
            matched_doc = doc
        else:
            if patient_row.hospital_id:
                hospital_docs = db.query(Doctor).filter(Doctor.hospital_id == patient_row.hospital_id).all()
                for h_doc in hospital_docs:
                    if verify_password(password, h_doc.password_hash):
                        matched_doc = h_doc
                        break

    if not matched_doc:
        patient_data = {
            "appointment_id": patient_row.appointment_id,
            "name": patient_row.name,
            "gender": patient_row.gender,
            "mobile": patient_row.mobile,
            "problem": patient_row.problem,
            "hospital_name": patient_row.hospital_name,
            "doctor_name": patient_row.doctor_name,
            "doctor_id": patient_row.doctor_id,
            "department": patient_row.department,
            "date": patient_row.date,
            "slot": patient_row.slot,
        }
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="doctor_unlock.html",
            context=page_ctx(
                request,
                patient=patient_data,
                token=token,
                error="Incorrect doctor password. Access denied.",
            ),
        )

    doc_id = matched_doc.id
    target_appointment_id = patient_row.appointment_id
    db.close()

    response = RedirectResponse(url=f"/doctor/consultation/{target_appointment_id}", status_code=303)
    set_doctor_session_cookie(response, doc_id, request)
    return response


@app.get("/doctor/consultation/{appointment_id}")
def doctor_consultation_page(appointment_id: str, request: Request):
    doctor = get_current_doctor(request)
    appointment_id = appointment_id.strip().upper()
    if not doctor:
        return RedirectResponse(url=f"/doctor/login?next=/doctor/consultation/{appointment_id}", status_code=303)

    db = SessionLocal()
    patient_row = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    if not patient_row:
        db.close()
        return render_doctor_dashboard(request, doctor, None, appointment_id, [], f"No appointment found for ID: {appointment_id}")

    if not doctor_can_access_appointment(doctor, patient_row):
        db.close()
        return render_doctor_dashboard(request, doctor, None, appointment_id, [], "You are not authorized to consult for this patient.")

    patient_data = patient_summary(patient_row)
    previous_visits = get_patient_history(patient_row.mobile, appointment_id, db)
    db.close()

    return templates.TemplateResponse(
        request=request,
        name="doctor_consultation.html",
        context=page_ctx(
            request,
            current_doctor=doctor,
            patient=patient_data,
            appointment_id=appointment_id,
            previous_visits=previous_visits,
        ),
    )


@app.post("/doctor/search")
def search_appointment(request: Request, appointment_id: str = Form(...)):
    doctor = get_current_doctor(request)
    if not doctor:
        return RedirectResponse(url="/doctor/login", status_code=303)

    db = SessionLocal()
    patient_data, previous_visits, error = load_patient_for_doctor(db, doctor, appointment_id)
    db.close()
    return render_doctor_dashboard(request, doctor, patient_data, appointment_id.strip().upper(), previous_visits, error)


def extract_medicines_from_form(form_data):
    names = form_data.getlist("medicine_name") or form_data.getlist("medicine")
    dosages = form_data.getlist("dosage")
    frequencies = form_data.getlist("frequency")
    usages = form_data.getlist("usage_instructions") or form_data.getlist("instructions_item")
    days_list = form_data.getlist("duration_days") or form_data.getlist("days")
    medicine_items = []
    for i in range(len(names)):
        m_name = names[i].strip() if i < len(names) else ""
        if not m_name:
            continue
        m_dosage = dosages[i].strip() if i < len(dosages) and dosages[i].strip() else "1 Unit"
        m_freq = frequencies[i].strip() if i < len(frequencies) and frequencies[i].strip() else "1-0-1 (Twice Daily)"
        m_usage = usages[i].strip() if i < len(usages) and usages[i].strip() else "Take after meals"
        m_days = int(days_list[i].strip()) if i < len(days_list) and str(days_list[i]).strip().isdigit() else 5
        medicine_items.append({
            "name": m_name,
            "dosage": m_dosage,
            "frequency": m_freq,
            "instructions": m_usage,
            "days": m_days
        })
    return medicine_items


@app.post("/doctor/prescription")
async def save_prescription(request: Request):
    doctor = get_current_doctor(request)
    if not doctor:
        return RedirectResponse(url="/doctor/login", status_code=303)

    form_data = await request.form()
    appointment_id = form_data.get("appointment_id", "").strip().upper()
    general_instructions = form_data.get("instructions", "")
    notes = form_data.get("notes", "")
    medicine_items = extract_medicines_from_form(form_data)

    db = SessionLocal()
    patient = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    patient_data = None
    notice = None
    share_text = ""
    if patient and doctor_can_access_appointment(doctor, patient) and medicine_items:
        apply_prescription(patient, medicine_items, general_instructions, notes)
        db.commit()
        items = parse_medicine_json(patient.medicine)
        share = build_prescription_share(patient, request)
        patient_data = {
            "name": patient.name,
            "hospital_name": patient.hospital_name,
            "doctor_name": patient.doctor_name,
            "medicine": patient.medicine,
            "dosage": patient.dosage,
            "instructions": patient.instructions,
            "medicine_list": items,
            "max_duration_days": patient.max_duration_days,
            "prescription_ref": patient.prescription_ref,
            "mobile": patient.mobile,
            "share_sms": share["sms_href"],
            "share_whatsapp": share["whatsapp_href"],
        }
        share_text = share["text"]
        notice = send_prescription_notice(patient)
    elif patient and not medicine_items:
        db.close()
        return RedirectResponse(url=f"/doctor/consultation/{appointment_id}?error=Please+prescribe+at+least+one+medicine", status_code=303)
    elif not patient:
        db.close()
        return render_doctor_dashboard(request, doctor, None, appointment_id, [], "Appointment not found")

    db.close()

    return templates.TemplateResponse(
        request=request,
        name="prescription_success.html",
        context=page_ctx(
            request,
            current_doctor=doctor,
            patient=patient_data,
            appointment_id=appointment_id,
            notification=notice,
            share_text=share_text,
        )
    )


@app.get("/api/doctor/offline-pack")
def doctor_offline_pack(request: Request):
    doctor = get_current_doctor(request)
    if not doctor:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    db = SessionLocal()
    rows = db.query(Appointment).filter(
        (Appointment.doctor_id == doctor.id) | (Appointment.doctor_name == doctor.name)
    ).order_by(Appointment.id.desc()).limit(40).all()
    appointments = []
    for row in rows:
        if not doctor_can_access_appointment(doctor, row):
            continue
        data = patient_summary(row, include_history=True, db=db)
        appointments.append(data)
    db.close()
    return {
        "doctor_id": doctor.id,
        "synced_at": datetime.now().isoformat(),
        "appointments": appointments,
    }


@app.post("/api/doctor/sync-prescription")
async def sync_prescription(request: Request):
    doctor = get_current_doctor(request)
    if not doctor:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    payload = await request.json()
    client_sync_id = (payload.get("client_sync_id") or "").strip()
    appointment_id = (payload.get("appointment_id") or "").strip().upper()
    medicines = payload.get("medicines") or []
    instructions = payload.get("instructions") or ""
    notes = payload.get("notes") or ""
    created_offline_at = payload.get("created_offline_at")

    if not client_sync_id or not appointment_id or not medicines:
        return JSONResponse({"error": "Invalid sync payload"}, status_code=400)

    db = SessionLocal()
    existing = db.query(SyncLedger).filter(SyncLedger.client_sync_id == client_sync_id).first()
    if existing:
        result = {
            "status": "DUPLICATE",
            "appointment_id": existing.appointment_id,
            "message": "Already synchronized",
        }
        db.close()
        return result

    patient = db.query(Appointment).filter(Appointment.appointment_id == appointment_id).first()
    if not patient:
        db.close()
        return JSONResponse({"error": "Appointment not found"}, status_code=404)
    if not doctor_can_access_appointment(doctor, patient):
        db.close()
        return JSONResponse({"error": "Not authorized for this appointment"}, status_code=403)

    if patient.prescription_status == "Saved" and patient.medicine:
        ledger = SyncLedger(
            client_sync_id=client_sync_id,
            appointment_id=appointment_id,
            doctor_id=doctor.id,
            payload_hash=hmac_value(json.dumps(payload, sort_keys=True, default=str)),
            status="CONFLICT",
            created_offline_at=created_offline_at,
            synced_at=datetime.now().isoformat(),
            error_status="Server already has a prescription for this appointment",
        )
        existing_ref = patient.prescription_ref
        db.add(ledger)
        db.commit()
        db.close()
        return {
            "status": "CONFLICT",
            "appointment_id": appointment_id,
            "prescription_ref": existing_ref,
            "message": "A prescription already exists on the server. Local duplicate was not applied.",
        }

    apply_prescription(patient, medicines, instructions, notes)
    ledger = SyncLedger(
        client_sync_id=client_sync_id,
        appointment_id=appointment_id,
        doctor_id=doctor.id,
        payload_hash=hmac_value(json.dumps(payload, sort_keys=True, default=str)),
        status="SYNCED",
        created_offline_at=created_offline_at,
        synced_at=datetime.now().isoformat(),
    )
    db.add(ledger)
    db.commit()
    ref = patient.prescription_ref
    server_id = patient.id
    notice = send_prescription_notice(patient)
    result = {
        "status": "SYNCED",
        "appointment_id": appointment_id,
        "prescription_ref": ref,
        "server_record_id": server_id,
        "notification": notice,
    }
    db.close()
    return result


# ---------------- PATIENT PRESCRIPTION PAGE ----------------

def patient_prescription_pack(patient):
    expired = is_prescription_expired(patient)
    items = [] if expired else parse_medicine_json(patient.medicine)
    expiry = prescription_expiry_dt(patient)
    return {
        "appointment_id": patient.appointment_id,
        "name": patient.name,
        "mobile": patient.mobile,
        "hospital_name": patient.hospital_name,
        "doctor_name": patient.doctor_name,
        "date": patient.date,
        "medicine_list": items,
        "instructions": None if expired else patient.instructions,
        "is_expired": expired,
        "max_duration_days": patient.max_duration_days if patient.max_duration_days else 5,
        "prescription_date": patient.prescription_date,
        "prescription_created_at": patient.prescription_created_at,
        "expiry_date": expiry.isoformat() if expiry else None,
        "prescription_ref": patient.prescription_ref,
        "prescription_status": "EXPIRED" if expired else "ACTIVE",
    }


def find_patient_by_appointment_and_mobile(db, appointment_id: str, mobile: str):
    if not appointment_id or not mobile:
        return None
    clean_id = appointment_id.strip().upper()
    clean_mob = digits_only(mobile)
    if not clean_id or not clean_mob:
        return None

    possible_ids = [clean_id]
    if clean_id.startswith("RX-"):
        possible_ids.append(clean_id[3:])
    else:
        possible_ids.append(f"RX-{clean_id}")

    candidates = db.query(Appointment).filter(Appointment.appointment_id.in_(possible_ids)).all()
    for p in candidates:
        db_mob = digits_only(p.mobile)
        if db_mob == clean_mob or p.mobile == mobile.strip():
            return p
        if len(clean_mob) >= 10 and len(db_mob) >= 10 and clean_mob[-10:] == db_mob[-10:]:
            return p
    return None


@app.get("/my-prescription")
def my_prescription_page(
    request: Request,
    id: Optional[str] = None,
    appointment_id: Optional[str] = None,
    m: Optional[str] = None,
    mobile: Optional[str] = None,
):
    target_id = (id or appointment_id or "").strip().upper()
    target_mobile = (m or mobile or "").strip()

    if target_id and target_mobile:
        db = SessionLocal()
        patient = find_patient_by_appointment_and_mobile(db, target_id, target_mobile)

        if patient:
            if patient.prescription_status == "Saved":
                pack = patient_prescription_pack(patient)
                expired = pack["is_expired"]
                patient_data = {
                    "name": patient.name,
                    "hospital_name": patient.hospital_name,
                    "doctor_name": patient.doctor_name,
                    "date": patient.date,
                    "medicine": None if expired else patient.medicine,
                    "dosage": None if expired else patient.dosage,
                    "instructions": None if expired else patient.instructions,
                    "medicine_list": pack["medicine_list"],
                    "is_expired": expired,
                    "max_duration_days": pack["max_duration_days"],
                    "prescription_date": patient.prescription_date,
                    "prescription_expiry_date": pack["expiry_date"],
                }
                real_id = patient.appointment_id
                db.close()
                return templates.TemplateResponse(
                    request=request,
                    name="view_prescription.html",
                    context=page_ctx(
                        request,
                        patient=patient_data,
                        appointment_id=real_id,
                        prescription_pack=json_safe(pack),
                    )
                )
            db.close()
            return templates.TemplateResponse(
                request=request,
                name="my_prescription.html",
                context=page_ctx(
                    request,
                    appointment_id=target_id,
                    mobile=target_mobile,
                    message="Prescription has not been added by the doctor yet."
                )
            )
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="my_prescription.html",
            context=page_ctx(
                request,
                appointment_id=target_id,
                mobile=target_mobile,
                message="Invalid Appointment ID or Mobile Number."
            )
        )

    return templates.TemplateResponse(
        request=request,
        name="my_prescription.html",
        context=page_ctx(
            request,
            appointment_id=target_id if target_id else "",
            mobile=target_mobile if target_mobile else "",
        )
    )


@app.post("/my-prescription")
def view_prescription(request: Request, appointment_id: str = Form(...), mobile: str = Form(...)):
    appointment_id = appointment_id.strip().upper()
    db = SessionLocal()

    patient = find_patient_by_appointment_and_mobile(db, appointment_id, mobile)

    if patient:
        if patient.prescription_status == "Saved":
            pack = patient_prescription_pack(patient)
            expired = pack["is_expired"]
            patient_data = {
                "name": patient.name,
                "hospital_name": patient.hospital_name,
                "doctor_name": patient.doctor_name,
                "date": patient.date,
                "medicine": None if expired else patient.medicine,
                "dosage": None if expired else patient.dosage,
                "instructions": None if expired else patient.instructions,
                "medicine_list": pack["medicine_list"],
                "is_expired": expired,
                "max_duration_days": pack["max_duration_days"],
                "prescription_date": patient.prescription_date,
                "prescription_expiry_date": pack["expiry_date"],
            }
            real_id = patient.appointment_id
            db.close()
            return templates.TemplateResponse(
                request=request,
                name="view_prescription.html",
                context=page_ctx(
                    request,
                    patient=patient_data,
                    appointment_id=real_id,
                    prescription_pack=json_safe(pack),
                )
            )
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="my_prescription.html",
            context=page_ctx(
                request,
                appointment_id=appointment_id,
                mobile=mobile,
                message="Prescription has not been added by the doctor yet."
            )
        )

    db.close()
    return templates.TemplateResponse(
        request=request,
        name="my_prescription.html",
        context=page_ctx(
            request,
            appointment_id=appointment_id,
            mobile=mobile,
            message="Invalid Appointment ID or Mobile Number."
        )
    )


@app.post("/api/patient/prescription-pack")
async def api_patient_pack(request: Request):
    payload = await request.json()
    appointment_id = (payload.get("appointment_id") or "").strip().upper()
    mobile = (payload.get("mobile") or "").strip()
    db = SessionLocal()
    patient = find_patient_by_appointment_and_mobile(db, appointment_id, mobile)
    if not patient or patient.prescription_status != "Saved":
        db.close()
        return JSONResponse({"error": "Not found"}, status_code=404)
    pack = patient_prescription_pack(patient)
    db.close()
    return pack


# ---------------- STAFF / KIOSK / ADMIN ----------------

@app.get("/staff/login")
def staff_login_page(request: Request):
    staff = get_current_staff(request)
    if staff:
        if staff.role == ROLE_SYSTEM_ADMIN:
            return RedirectResponse("/admin/system", status_code=303)
        if staff.role == ROLE_HOSPITAL_ADMIN:
            return RedirectResponse("/admin/hospital", status_code=303)
        return RedirectResponse("/staff/helpdesk", status_code=303)
    return templates.TemplateResponse(request=request, name="staff_login.html", context=page_ctx(request))


@app.post("/staff/login")
def staff_login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip().upper()
    db = SessionLocal()
    staff = db.query(StaffUser).filter(StaffUser.username == username).first()
    if not staff or not staff.is_active or not verify_password(password, staff.password_hash):
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="staff_login.html",
            context=page_ctx(request, error="Invalid staff username or password."),
        )
    staff_id = staff.id
    role = staff.role
    db.close()
    target = "/staff/helpdesk"
    if role == ROLE_SYSTEM_ADMIN:
        target = "/admin/system"
    elif role == ROLE_HOSPITAL_ADMIN:
        target = "/admin/hospital"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie(key="staff_session", value=str(staff_id), max_age=86400 * 7, httponly=True, samesite="lax")
    return response


@app.get("/staff/logout")
def staff_logout():
    response = RedirectResponse(url="/staff/login", status_code=303)
    response.delete_cookie(key="staff_session")
    return response


def require_staff(request: Request, roles=None):
    staff = get_current_staff(request)
    if not staff:
        return None
    if roles and staff.role not in roles:
        return None
    return staff


@app.get("/staff/helpdesk")
def staff_helpdesk(request: Request):
    staff = require_staff(request, [ROLE_STAFF, ROLE_HOSPITAL_ADMIN, ROLE_SYSTEM_ADMIN])
    if not staff:
        return RedirectResponse("/staff/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="staff_helpdesk.html",
        context=page_ctx(request, current_staff=staff)
    )


def mobile_matches(stored, provided):
    provided = (provided or "").strip()
    stored = stored or ""
    if not provided:
        return False
    if len(provided) <= 4:
        return stored.endswith(provided)
    return stored == provided


def find_appointment_for_staff(db, staff, appointment_id, hospital_token, mobile):
    row = None
    identifier = ""
    if appointment_id:
        row = db.query(Appointment).filter(Appointment.appointment_id == appointment_id.strip().upper()).first()
        identifier = "appointment_id"
    if not row and hospital_token:
        row = db.query(Appointment).filter(Appointment.hospital_token == hospital_token.strip().upper()).first()
        identifier = "hospital_token"
    if not row:
        return None, None, "No matching appointment was found."
    if not staff_can_access_appointment(staff, row):
        return None, None, "This record belongs to another hospital."
    token_ok = bool(hospital_token) and row.hospital_token == hospital_token.strip().upper()
    mobile_ok = mobile_matches(row.mobile, mobile) if mobile else False
    if not token_ok and not mobile_ok:
        return None, None, "Verify the patient with mobile number (or last 4 digits) or a hospital desk token. Appointment ID alone is not enough."
    return row, identifier, None


@app.post("/staff/helpdesk")
def staff_helpdesk_lookup(
    request: Request,
    appointment_id: str = Form(""),
    hospital_token: str = Form(""),
    mobile: str = Form(""),
):
    staff = require_staff(request, [ROLE_STAFF, ROLE_HOSPITAL_ADMIN, ROLE_SYSTEM_ADMIN])
    if not staff:
        return RedirectResponse("/staff/login", status_code=303)
    db = SessionLocal()
    row, identifier, error = find_appointment_for_staff(db, staff, appointment_id, hospital_token, mobile)
    prescription = None
    if row:
        expired = is_prescription_expired(row)
        prescription = {
            "appointment_id": row.appointment_id,
            "name": row.name,
            "hospital_name": row.hospital_name,
            "doctor_name": row.doctor_name,
            "date": row.date,
            "prescription_ref": row.prescription_ref,
            "hospital_token": row.hospital_token,
            "is_expired": expired,
            "max_duration_days": row.max_duration_days or 5,
            "medicine_list": [] if expired else parse_medicine_json(row.medicine),
            "instructions": None if expired else row.instructions,
            "prescription_status": row.prescription_status,
            "print_count": row.print_count or 0,
        }
        if row.prescription_status != "Saved":
            error = "Prescription has not been issued yet."
    db.close()
    return templates.TemplateResponse(
        request=request,
        name="staff_helpdesk.html",
        context=page_ctx(
            request,
            current_staff=staff,
            prescription=prescription,
            error=error,
            identifier_used=identifier,
        )
    )


@app.post("/staff/print-log")
def staff_print_log(request: Request, appointment_id: str = Form(...), identifier_used: str = Form("helpdesk")):
    staff = require_staff(request, [ROLE_STAFF, ROLE_HOSPITAL_ADMIN, ROLE_SYSTEM_ADMIN])
    if not staff:
        return RedirectResponse("/staff/login", status_code=303)
    db = SessionLocal()
    row = db.query(Appointment).filter(Appointment.appointment_id == appointment_id.strip().upper()).first()
    if row and staff_can_access_appointment(staff, row):
        row.print_count = (row.print_count or 0) + 1
        db.add(PrintLog(
            appointment_id=row.appointment_id,
            staff_id=staff.id,
            identifier_used=identifier_used,
            printed_at=datetime.now().isoformat(),
        ))
        db.commit()
    db.close()
    return RedirectResponse(url="/staff/helpdesk", status_code=303)


@app.get("/kiosk")
def kiosk_page(request: Request):
    return templates.TemplateResponse(request=request, name="kiosk.html", context=page_ctx(request))


@app.post("/kiosk/otp")
def kiosk_send_otp(request: Request, appointment_id: str = Form(...), mobile: str = Form(...)):
    appointment_id = appointment_id.strip().upper()
    mobile = mobile.strip()
    db = SessionLocal()
    row = db.query(Appointment).filter(
        Appointment.appointment_id == appointment_id,
        Appointment.mobile == mobile,
    ).first()
    message = "If the details match, a verification code was generated for this kiosk session."
    otp_demo = None
    if row:
        otp = f"{secrets.randbelow(1000000):06d}"
        db.add(KioskOtp(
            appointment_id=appointment_id,
            otp_hash=hmac_value(otp),
            expires_at=(datetime.now() + timedelta(minutes=KIOSK_OTP_MINUTES)).isoformat(),
            attempts=0,
            created_at=datetime.now().isoformat(),
        ))
        db.commit()
        dispatch_notification(
            destination=mobile,
            message=f"RxVault kiosk verification code for appointment {appointment_id}. Do not share medicines over SMS.",
            appointment_id=appointment_id,
            prescription_ref=row.prescription_ref or "PENDING",
            channel="kiosk_otp",
        )
        if (os.getenv("KIOSK_OTP_DEMO") or "1") == "1":
            otp_demo = otp
            message = f"Demo mode: enter verification code {otp}. Configure KIOSK_OTP_DEMO=0 in production."
    db.close()
    return templates.TemplateResponse(
        request=request,
        name="kiosk.html",
        context=page_ctx(
            request,
            step="otp",
            appointment_id=appointment_id,
            mobile=mobile,
            message=message,
            otp_demo=otp_demo,
        )
    )


@app.post("/kiosk/verify")
def kiosk_verify(
    request: Request,
    appointment_id: str = Form(...),
    mobile: str = Form(...),
    otp: str = Form(...),
):
    appointment_id = appointment_id.strip().upper()
    db = SessionLocal()
    row = db.query(Appointment).filter(
        Appointment.appointment_id == appointment_id,
        Appointment.mobile == mobile.strip(),
    ).first()
    otp_row = (
        db.query(KioskOtp)
        .filter(KioskOtp.appointment_id == appointment_id)
        .order_by(KioskOtp.id.desc())
        .first()
    )
    error = None
    prescription = None
    if not row or not otp_row:
        error = "Verification failed. Appointment ID, mobile number, and code are all required."
    elif datetime.now() > datetime.fromisoformat(otp_row.expires_at):
        error = "Verification code expired. Please request a new code."
    elif otp_row.attempts >= 5:
        error = "Too many attempts. Please request a new code."
    elif not hmac.compare_digest(otp_row.otp_hash, hmac_value(otp.strip())):
        otp_row.attempts = (otp_row.attempts or 0) + 1
        db.commit()
        error = "Invalid verification code."
    elif row.prescription_status != "Saved":
        error = "Prescription has not been issued yet."
    else:
        expired = is_prescription_expired(row)
        prescription = {
            "appointment_id": row.appointment_id,
            "name": row.name,
            "hospital_name": row.hospital_name,
            "doctor_name": row.doctor_name,
            "date": row.date,
            "prescription_ref": row.prescription_ref,
            "is_expired": expired,
            "max_duration_days": row.max_duration_days or 5,
            "medicine_list": [] if expired else parse_medicine_json(row.medicine),
            "instructions": None if expired else row.instructions,
        }
    db.close()
    return templates.TemplateResponse(
        request=request,
        name="kiosk.html",
        context=page_ctx(
            request,
            step="done" if prescription else "otp",
            appointment_id=appointment_id,
            mobile=mobile,
            prescription=prescription,
            error=error,
        )
    )


@app.get("/admin/hospital")
def admin_hospital(request: Request):
    staff = require_staff(request, [ROLE_HOSPITAL_ADMIN, ROLE_SYSTEM_ADMIN])
    if not staff:
        return RedirectResponse("/staff/login", status_code=303)
    db = SessionLocal()
    q = db.query(Doctor)
    if staff.role != ROLE_SYSTEM_ADMIN:
        q = q.filter(Doctor.hospital_id == staff.hospital_id)
    doctors = q.all()
    staff_users = db.query(StaffUser).filter(StaffUser.role == ROLE_STAFF)
    if staff.role != ROLE_SYSTEM_ADMIN:
        staff_users = staff_users.filter(StaffUser.hospital_id == staff.hospital_id)
    staff_users = staff_users.all()
    db.close()
    return templates.TemplateResponse(
        request=request,
        name="admin_hospital.html",
        context=page_ctx(request, current_staff=staff, doctors=doctors, staff_users=staff_users)
    )


@app.post("/admin/hospital/staff")
def admin_add_staff(
    request: Request,
    username: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
):
    staff = require_staff(request, [ROLE_HOSPITAL_ADMIN, ROLE_SYSTEM_ADMIN])
    if not staff:
        return RedirectResponse("/staff/login", status_code=303)
    db = SessionLocal()
    uname = username.strip().upper()
    if db.query(StaffUser).filter(StaffUser.username == uname).first():
        db.close()
        return RedirectResponse("/admin/hospital", status_code=303)
    db.add(StaffUser(
        username=uname,
        password_hash=hash_password(password),
        full_name=full_name,
        role=ROLE_STAFF,
        hospital_id=staff.hospital_id,
        hospital_name=staff.hospital_name,
        is_active=True,
    ))
    db.commit()
    db.close()
    return RedirectResponse("/admin/hospital", status_code=303)


@app.post("/admin/hospital/doctor-status")
def admin_doctor_status(request: Request, doctor_id: int = Form(...), is_active: int = Form(...)):
    staff = require_staff(request, [ROLE_HOSPITAL_ADMIN, ROLE_SYSTEM_ADMIN])
    if not staff:
        return RedirectResponse("/staff/login", status_code=303)
    db = SessionLocal()
    doc = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if doc and (staff.role == ROLE_SYSTEM_ADMIN or doc.hospital_id == staff.hospital_id):
        doc.is_active = bool(is_active)
        db.commit()
    db.close()
    return RedirectResponse("/admin/hospital", status_code=303)


@app.get("/admin/system")
def admin_system(request: Request):
    staff = require_staff(request, [ROLE_SYSTEM_ADMIN])
    if not staff:
        return RedirectResponse("/staff/login", status_code=303)
    db = SessionLocal()
    settings = {row.key: row.value for row in db.query(SystemSetting).all()}
    staff_count = db.query(StaffUser).count()
    doctor_count = db.query(Doctor).count()
    appt_count = db.query(Appointment).count()
    db.close()
    hospitals = [{"id": i, **data} for i, data in HOSPITALS_MAP.items()]
    return templates.TemplateResponse(
        request=request,
        name="admin_system.html",
        context=page_ctx(
            request,
            current_staff=staff,
            settings=settings,
            hospitals=hospitals,
            staff_count=staff_count,
            doctor_count=doctor_count,
            appt_count=appt_count,
        )
    )


@app.post("/admin/system")
def admin_system_save(
    request: Request,
    qr_token_ttl_hours: str = Form(...),
    notification_provider: str = Form(...),
):
    staff = require_staff(request, [ROLE_SYSTEM_ADMIN])
    if not staff:
        return RedirectResponse("/staff/login", status_code=303)
    set_setting("qr_token_ttl_hours", qr_token_ttl_hours)
    set_setting("notification_provider", notification_provider)
    return RedirectResponse("/admin/system", status_code=303)


@app.middleware("http")
async def inject_pwa_assets(request: Request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type:
        return response
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    try:
        html = body.decode("utf-8")
    except Exception:
        return response
    if "</head>" in html and 'rel="manifest"' not in html:
        html = html.replace("</head>", PWA_HEAD + "</head>", 1)
    page_scripts = PWA_SCRIPTS
    if request.url.path.startswith("/doctor") and "doctor-offline.js" not in html:
        page_scripts += '<script src="/static/js/doctor-offline.js" defer></script>'
    if "prescription" in request.url.path and "patient-offline.js" not in html:
        page_scripts += '<script src="/static/js/patient-offline.js" defer></script>'
    if "</body>" in html and "offline-store.js" not in html:
        html = html.replace("</body>", page_scripts + "</body>", 1)
    headers = MutableHeaders(response.headers)
    if "content-length" in headers:
        del headers["content-length"]
    return HTMLResponse(content=html, status_code=response.status_code, headers=dict(headers))
