# 🏥 RxVault — Digital Healthcare Consultation & Prescription Management Platform

A secure, mobile-friendly, offline-capable, multi-channel digital healthcare platform built with **FastAPI** (Python), **SQLite**, **Jinja2 templates**, and a **PWA-enabled** frontend with **IndexedDB** offline storage, password-protected mobile doctor QR scanning, and instant WhatsApp & SMS prescription delivery.

---

## 🔑 Demo Credentials (Staff, Admins & Doctors)

All accounts are pre-seeded in the database on first startup.

### 🏢 Staff & Admin Credentials
| Portal | Role | Username | Password | Access / Permissions |
|--------|------|----------|----------|----------------------|
| `/staff/login` | **Hospital Reception / Staff** | `STAFF-APOLLO` | `staff123` | Help desk lookup by Appointment ID or Hospital Token, patient verification, print physical Rx copy |
| `/staff/login` | **Hospital Admin** | `ADMIN-APOLLO` | `admin123` | Hospital management (`/admin/hospital`), manage reception staff, activate/deactivate hospital doctors |
| `/staff/login` | **System Super Admin** | `SYSADMIN` | `sysadmin123` | System settings (`/admin/system`), QR token TTL config, notification providers, platform audit |

---

### 👨‍⚕️ Doctor Credentials (Portal: `/doctor/login`)
| Doctor Name | Medical License No. | Password | Hospital | Department |
|-------------|---------------------|----------|----------|------------|
| Dr. Rahul Sharma | `MC-10001` | `doctor123` | Apollo Hospital | General Medicine |
| Dr. Anjali Reddy | `MC-10002` | `doctor123` | KIMS Hospital | General Medicine |
| Dr. Vikram Rao | `MC-10003` | `doctor123` | Yashoda Hospital | Cardiology |
| Dr. Sneha Kumar | `MC-10004` | `doctor123` | CARE Hospital | Cardiology |
| Dr. Priya Sharma | `MC-10005` | `doctor123` | Apollo Hospital | Dermatology |
| Dr. Kiran Reddy | `MC-10006` | `doctor123` | KIMS Hospital | Dermatology |
| Dr. Arjun Kumar | `MC-10007` | `doctor123` | Yashoda Hospital | Orthopedics |
| Dr. Sandeep Rao | `MC-10008` | `doctor123` | CARE Hospital | Orthopedics |

> 🔒 **Doctor QR Pass Unlock:** When scanning an appointment QR pass with a phone camera, the doctor can unlock the consultation screen using either their **Medical License Number** or the attending doctor's password (`doctor123`).

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- pip

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server (binds to 0.0.0.0 so phones on your Wi-Fi can connect)
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- Open your desktop browser at: **http://127.0.0.1:8000/**
- Open on mobile phone (same Wi-Fi): `http://<YOUR_LAN_IP>:8000/` (RxVault auto-detects this for generated QR codes).

---

## 📁 Project Structure

```
RXVAULT/
│
├── main.py                      # FastAPI application — all routes, models, business logic
├── notifications.py             # Configurable notification dispatcher (SMS/webhook/file)
├── requirements.txt             # Python dependencies
├── rxvault.db                   # SQLite database (auto-created on first run)
│
├── static/
│   ├── style.css                # Global responsive CSS design system & touch optimizations
│   ├── manifest.json            # PWA Web App Manifest
│   ├── sw.js                    # Service Worker (offline caching)
│   ├── icons/
│   │   ├── icon-192.png         # PWA icon (192×192)
│   │   └── icon-512.png         # PWA icon (512×512)
│   ├── qr_codes/                # Auto-generated QR code pass images
│   └── js/
│       ├── pwa.js               # Service Worker registration
│       ├── offline-store.js     # IndexedDB store + AES-GCM encryption helpers
│       ├── sync-status.js       # Online/Offline/Syncing status bar UI
│       ├── doctor-offline.js    # Doctor offline mode + QR scanning + sync engine
│       └── patient-offline.js   # Patient offline prescription caching + expiry check
│
├── templates/
│   ├── index.html               # Home page
│   ├── hospitals.html           # Hospital listing
│   ├── hospital.html            # Single hospital & departments
│   ├── doctors.html             # Doctors in a department
│   ├── appointment.html         # Date & slot picker (mobile touch-friendly)
│   ├── register.html            # Patient registration form
│   ├── confirmation.html        # Booking confirmation
│   ├── payment.html             # Payment page
│   ├── success.html             # Booking success + Full-screen QR modal + WhatsApp share
│   ├── doctor_register.html     # Doctor self-registration
│   ├── doctor_login.html        # Doctor login
│   ├── doctor_unlock.html       # Scanned QR pass password unlock screen
│   ├── doctor_consultation.html # Mobile consultation screen + dynamic medicine builder
│   ├── doctor_dashboard.html    # Doctor consultation & prescription workspace
│   ├── prescription_success.html# 1-tap WhatsApp, SMS & App delivery buttons
│   ├── my_prescription.html     # Patient prescription lookup form
│   ├── view_prescription.html   # Patient prescription display (cards on mobile, table on PC)
│   ├── kiosk.html               # Self-service kiosk (OTP-based)
│   ├── staff_login.html         # Staff / Admin login
│   ├── staff_helpdesk.html      # Reception help desk
│   ├── admin_hospital.html      # Hospital admin panel
│   ├── admin_system.html        # System admin panel
│   └── offline.html             # Offline fallback page (served by Service Worker)
│
└── tests/
    └── test_platform.py         # 14 automated pytest test cases
```

---

## 🔄 Complete Application Flow

### 👤 PATIENT FLOW

```
1. Home Page (/)
        ↓
2. Browse Hospitals (/hospitals)
        ↓
3. Select Department (/hospital/{id})
        ↓
4. Select Doctor (/hospital/{id}/department/{dept})
        ↓
5. Choose Date & Slot (/appointment/{hospital_id}/{dept}/{doctor_id})
        ↓
6. Register Details (/register/{...})
        ↓
7. Confirm Booking (/payment)
        ↓
8. Payment Success (/payment-success)
   → Appointment created in DB
   → Secure QR Token generated (secrets.token_urlsafe(32))
   → QR Code image saved to /static/qr_codes/
   → Hospital Desk Token generated (HT-XXXXXXXX)
        ↓
9. Success Page (/success)
   → Shows: Appointment ID, QR Code, Hospital Token, Booking Details
```

---

### 🩺 DOCTOR FLOW

```
1. Doctor Registration (/doctor/register)
   Fields: Name, Age, Experience, Email, Specialty,
           Hospital, Medical License Number, Password

        ↓

2. Doctor Login (/doctor/login)
   → Authenticated via cookie (doctor_session)

        ↓

3. Doctor Dashboard (/doctor)
   ┌──────────────────────────────────┐
   │  Option A: Type Appointment ID   │ → POST /doctor/search
   │  Option B: Scan QR Code          │ → GET  /doctor/scan/t/{token}
   └──────────────────────────────────┘
        ↓
4. Patient Record Loaded
   → Patient details displayed
   → Automatic consultation history retrieved (same mobile number)

        ↓

5. Write Prescription
   → Add multiple medicines (Name, Strength, Dosage,
     Frequency, Instructions, Duration in Days)
   → Add Consultation Notes
   → Add General Advice
   → Submit → POST /doctor/prescription

        ↓

6. Prescription Saved
   → Expiry date calculated (max days across all medicines)
   → Prescription Reference generated (RX-XXXXXX)
   → QR Token INVALIDATED (single-use consultation pass)
   → SMS reference notification dispatched
   → Confirmation page shown
```

---

### 📋 PATIENT PRESCRIPTION ACCESS FLOW

```
1. Patient visits /my-prescription
        ↓
2. Enter: Appointment ID + Mobile Number
        ↓
3. System verifies and retrieves prescription
        ↓
4. If ACTIVE:
   → Full medicine list shown (table format)
   → Prescription Date & Expiry Date displayed
   → Prescription saved to IndexedDB (offline cache)
   → "Last Synced: [timestamp]" shown

   If EXPIRED (Current Date > Expiry Date):
   → Medicine details HIDDEN
   → Shows: ⚠️ Time of Usage Completed
     "Prescribed duration has expired. Please visit your
      doctor again for a fresh consultation."
```

---

### 📵 OFFLINE FLOW — DOCTOR

```
ONLINE (background sync):
  → GET /api/doctor/offline-pack
  → Up to 40 appointments cached in IndexedDB (AES-GCM encrypted)
  → Patient history cached per mobile number

OFFLINE:
  → Status bar shows: 🔴 Offline Mode
  → Doctor can:
      • Click cached appointment chips to open patient records
      • Search by Appointment ID (loads from IndexedDB)
      • Scan QR code (local token validation from IndexedDB cache)
      • Write full prescription
      • Save → stored in IndexedDB "pending" outbox
      • Record marked: Pending Sync

RECONNECTED:
  → Status bar shows: 🟡 Synchronizing...
  → POST /api/doctor/sync-prescription (for each pending record)
      - Duplicate check via client_sync_id (idempotent)
      - CONFLICT if server already has a prescription
      - SYNCED on success
  → Status bar shows: 🟢 Synced
```

---

### 📵 OFFLINE FLOW — PATIENT

```
ONLINE:
  → Patient retrieves prescription → stored in IndexedDB

OFFLINE:
  → Status bar shows: 🔴 Offline Mode
  → Patient searches → lookup form is hidden
  → Cached prescription displayed with offline banner
  → Expiry check runs using cached expiry_date:
      If expired → medicines locked, safety warning shown
  → "Back to Lookup" button restores the search form
```

---

### 🏥 HOSPITAL STAFF / RECEPTION FLOW

```
1. Staff Login (/staff/login)
   Credentials: Username + Password

        ↓

2. Help Desk (/staff/helpdesk)
   Lookup by ANY of:
   ├─ Appointment ID + Mobile Number (or last 4 digits)
   └─ Hospital Desk Token (HT-XXXXXXXX) — for patients without phones

        ↓

3. Prescription Retrieved
   → If expired: safety lock shown
   → If active: full medicine table shown
   → Print Summary button (window.print())
   → Record Physical Copy Issued (POST /staff/print-log)
```

---

### 🖥️ SELF-SERVICE KIOSK FLOW

```
1. Patient visits /kiosk
        ↓
2. Enter: Appointment ID + Mobile Number
        ↓
3. System generates OTP (6-digit) → dispatched via notification channel
   (Demo mode: OTP shown on screen)
        ↓
4. Patient enters OTP
   → 5-minute expiry
   → Max 5 attempts before lockout
        ↓
5. Prescription displayed
   → Print button available
```

---

### 🔑 ADMIN FLOWS

```
Hospital Admin (/admin/hospital)
  → Add/Remove reception staff accounts
  → Activate/Deactivate doctors
  → View authorized staff

System Admin (/admin/system)
  → Configure QR Token TTL (hours)
  → Configure Notification Provider
  → View platform statistics
```

---

## 🗄️ Database Schema

**SQLite file:** `rxvault.db` (auto-created)

### appointments
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| appointment_id | STRING | Unique ID (RX-XXXXXXXX) |
| name | STRING | Patient name |
| gender | STRING | Patient gender |
| mobile | STRING | Registered mobile |
| problem | TEXT | Chief complaint |
| hospital_name | STRING | Hospital |
| doctor_name | STRING | Attending doctor |
| department | STRING | Department |
| date | STRING | Appointment date |
| slot | STRING | Time slot |
| amount | INTEGER | Consultation fee |
| medicine | TEXT | JSON array of medicines |
| dosage | STRING | Summary dosage string |
| instructions | TEXT | General advice |
| prescription_status | STRING | Pending / Saved |
| prescription_date | STRING | ISO timestamp |
| prescription_created_at | STRING | ISO timestamp |
| prescription_expiry_date | STRING | ISO timestamp |
| max_duration_days | INTEGER | Longest medicine duration |
| consultation_notes | TEXT | Doctor's clinical notes |
| prescription_ref | STRING | RX-XXXXXX reference |
| hospital_token | STRING | HT-XXXXXXXX desk token |
| qr_token | STRING | Secure URL-safe random token |
| qr_token_created_at | STRING | ISO timestamp |
| qr_token_expires_at | STRING | ISO timestamp |
| qr_token_status | STRING | ACTIVE / INVALIDATED / EXPIRED |
| print_count | INTEGER | Number of printed copies |
| hospital_id | INTEGER | FK to hospital |
| doctor_id | INTEGER | FK to doctor |

### doctors
| Column | Description |
|--------|-------------|
| medical_number | License ID (unique login key) |
| name, age, experience | Profile info |
| email | Unique email |
| specialty | Medical specialty |
| hospital_id / hospital_name | Affiliation |
| password_hash | HMAC-SHA256 hash |
| is_active | Account status |

### staff_users
| Column | Description |
|--------|-------------|
| username | Login username |
| password_hash | HMAC-SHA256 hash |
| role | HOSPITAL_STAFF / HOSPITAL_ADMIN / SYSTEM_ADMIN |
| hospital_id | Affiliation |

### sync_ledger
Tracks offline prescription sync operations (idempotent).

### kiosk_otps
Tracks kiosk OTP codes with expiry and attempt limits.

### print_logs
Audit log for every printed prescription copy.

### notification_logs
Audit log for all notification dispatches.

### system_settings
Key-value store for configurable platform settings.

---

## 🔐 Security Design

| Feature | Implementation |
|---------|---------------|
| Doctor auth | HMAC-SHA256 password hash, httpOnly session cookie |
| Staff auth | HMAC-SHA256 password hash, httpOnly session cookie |
| QR tokens | `secrets.token_urlsafe(32)` — 256-bit random, not guessable |
| QR expiry | Configurable TTL (default 72 hours) |
| QR invalidation | Token status set to INVALIDATED after consultation |
| Offline encryption | AES-GCM via WebCrypto API, key derived from server HMAC material |
| Cache purge | `RxVaultOffline.clearAll()` on all logout events |
| Backend auth | All sensitive endpoints check cookies server-side |
| Kiosk OTP | 6-digit, 5-minute expiry, 5-attempt lockout |
| No data in QR | QR contains only a random token, never medical data |

---

## 📡 API Endpoints Reference

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/` | Public | Home page |
| GET | `/hospitals` | Public | List all hospitals |
| GET | `/hospital/{id}` | Public | Hospital departments |
| GET | `/hospital/{id}/department/{dept}` | Public | Doctors list |
| GET | `/appointment/{hospital_id}/{dept}/{doctor_id}` | Public | Slot picker |
| POST | `/payment-success` | Public | Book appointment & generate QR |
| GET | `/doctor/register` | Public | Doctor registration form |
| POST | `/doctor/register` | Public | Submit registration |
| GET | `/doctor/login` | Public | Doctor login form |
| POST | `/doctor/login` | Public | Authenticate doctor |
| GET | `/doctor/logout` | Doctor | Logout & clear cookie |
| GET | `/doctor` | Doctor | Dashboard |
| POST | `/doctor/search` | Doctor | Search patient by Appointment ID |
| GET | `/doctor/scan/t/{token}` | Doctor | Scan QR pass by token |
| POST | `/doctor/prescription` | Doctor | Save prescription |
| GET | `/api/doctor/offline-pack` | Doctor | Download offline data pack |
| POST | `/api/doctor/sync-prescription` | Doctor | Sync offline prescription |
| GET | `/my-prescription` | Public | Patient lookup form |
| POST | `/my-prescription` | Public | Retrieve prescription |
| POST | `/api/patient/prescription-pack` | Public | JSON prescription for caching |
| GET | `/staff/login` | Public | Staff login form |
| POST | `/staff/login` | Public | Authenticate staff |
| GET | `/staff/helpdesk` | Staff | Reception lookup form |
| POST | `/staff/helpdesk` | Staff | Look up patient prescription |
| POST | `/staff/print-log` | Staff | Record printed copy |
| GET | `/kiosk` | Public | Self-service kiosk |
| POST | `/kiosk/otp` | Public | Request OTP |
| POST | `/kiosk/verify` | Public | Verify OTP & show prescription |
| GET | `/admin/hospital` | Hospital Admin | Manage doctors & staff |
| POST | `/admin/hospital/staff` | Hospital Admin | Add staff account |
| POST | `/admin/hospital/doctor-status` | Hospital Admin | Activate/deactivate doctor |
| GET | `/admin/system` | System Admin | Platform configuration |
| POST | `/admin/system` | System Admin | Save settings |
| GET | `/api/health` | Public | Health check |
| GET | `/api/me` | Any Auth | Current user role info |
| GET | `/sw.js` | Public | Service Worker |
| GET | `/manifest.json` | Public | PWA Manifest |
| GET | `/offline` | Public | Offline fallback page |

---

## 🔔 Notification System

Configured via environment variable `NOTIFICATION_PROVIDER`:

| Provider | Description |
|----------|-------------|
| `console` | Prints to server logs (default, safe for demo) |
| `file` | Appends JSON lines to `NOTIFICATION_LOG_FILE` |
| `webhook` | HTTP POST to `NOTIFICATION_WEBHOOK_URL` |

**Messages contain only:** Appointment ID, Prescription Reference, Hospital Name.
**Messages never contain:** Medicine names, dosages, diagnoses, or medical details.

---

## 🌐 Progressive Web App (PWA)

- **Manifest:** `/manifest.json` — name, icons, theme color, display mode
- **Service Worker:** `/sw.js`
  - Precaches: CSS, JS files, icons, offline page
  - **Static assets:** Cache-first strategy
  - **HTML pages:** Network-first with offline fallback
  - **API calls:** Bypassed (never cached)
- **Installable** on Android, iOS (Add to Home Screen), Desktop Chrome

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PUBLIC_BASE_URL` | Auto-detected LAN IP | Public base URL used in QR codes & WhatsApp links (e.g. `https://rxvault.onrender.com`) |
| `RXVAULT_SECRET_KEY` | `rxvault_doctor_auth_secret_key_2026` | HMAC secret for password hashing & offline key material |
| `QR_TOKEN_TTL_HOURS` | `72` | QR pass validity duration in hours |
| `KIOSK_OTP_MINUTES` | `5` | OTP expiry in minutes |
| `KIOSK_OTP_DEMO` | `1` | Show OTP on screen (set `0` in production) |
| `NOTIFICATION_PROVIDER` | `console` | `console`, `file`, or `webhook` |
| `NOTIFICATION_WEBHOOK_URL` | — | Webhook URL for SMS/email gateway |
| `NOTIFICATION_API_KEY` | — | Bearer token for webhook auth |
| `NOTIFICATION_LOG_FILE` | `./notification_outbox.jsonl` | File path for file provider |

---

## 🧪 Automated Testing

RxVault includes comprehensive test coverage for all features:

```bash
# Run all 14 automated tests
python -m pytest tests/ -v
```

### Verified Test Scenarios:
1. `test_home_and_pwa_assets`: Verifies landing page, manifest, and service worker.
2. `test_hospitals_and_departments_still_work`: Verifies hospital/department directory.
3. `test_doctor_login_required_for_dashboard`: Verifies authentication barriers.
4. `test_qr_scan_requires_auth`: Verifies QR tokens require login/password.
5. `test_doctor_login_and_offline_pack`: Tests offline consultation sync API.
6. `test_appointment_qr_token_is_not_appointment_id`: Verifies cryptographic token separation.
7. `test_prescription_idempotent_sync_and_expiry`: Verifies offline sync idempotency & auto-expiry.
8. `test_staff_cannot_open_prescription_with_id_only`: Verifies patient privacy controls.
9. `test_kiosk_requires_otp_not_guessable_id`: Tests kiosk OTP protection.
10. `test_unauthorized_api_sync`: Verifies API authentication.
11. `test_doctor_password_unlocks_scanned_qr`: Verifies QR password unlock workflow.
12. `test_qr_endpoints_and_base64_data_uri`: Verifies embedded Base64 QR generation and streaming.
13. `test_scanned_qr_unlock_and_consultation_flow`: Full end-to-end booking -> QR scan -> password unlock -> regimen write -> WhatsApp/SMS dispatch.
14. `test_mobile_friendly_pass_modal_and_share_elements`: Tests mobile UI components, full screen QR modal & responsive cards.

---

## 🧪 Testing Offline Mode (Step-by-Step)

### Doctor Offline Test
1. Login as a doctor at `/doctor/login`
2. Dashboard loads and syncs appointments to IndexedDB automatically
3. Open **Chrome DevTools → Network tab → check "Offline"**
4. Status bar shows `🔴 Offline Mode`
5. Search for a cached appointment by ID — it loads from IndexedDB
6. Scan the QR code — local validation runs without internet
7. Write a prescription and save — status shows `🟠 Offline – 1 record waiting`
8. Uncheck "Offline" in DevTools
9. Status changes to `🟡 Synchronizing...` then `🟢 Synced`
10. Prescription is saved to the server database with no duplicates

### Patient Offline Test
1. Go to `/my-prescription` and look up a prescription online
2. Prescription is auto-cached in IndexedDB
3. Enable Offline mode in DevTools
4. Search again — search form hides, cached prescription displays
5. Click **"Back to Lookup"** to search again
6. To test expiry: modify the cached `expiry_date` in IndexedDB (DevTools → Application → IndexedDB) to a past date, reload — prescription locks automatically

---

## 📞 Support & Contact

**Developer:** P. Pranay Kumar Reddy
**Phone:** 7207795562
**Email:** pranaykumarr800@gmail.com

---

*© 2026 RxVault Digital Healthcare System. All rights reserved.*
