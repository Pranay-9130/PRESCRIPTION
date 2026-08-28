"""
Configurable multi-channel patient notifications.

Do NOT send medicine names, dosages, diagnoses, or other clinical details.

Supported providers (NOTIFICATION_PROVIDER):
  - console  : print to server logs (default, safe for demos)
  - webhook  : POST JSON to NOTIFICATION_WEBHOOK_URL
               Optional header: Authorization: Bearer NOTIFICATION_API_KEY
  - file     : append JSON lines to NOTIFICATION_LOG_FILE

SMS/email vendors can be wired by pointing the webhook at a gateway
(Twilio, MSG91, hospital SMS hub, etc.) without changing application code.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional


def build_reference_message(appointment_id: str, prescription_ref: str, hospital_name: str) -> str:
    return (
        f"Your consultation has been completed. "
        f"Prescription Reference: {prescription_ref}. "
        f"Appointment: {appointment_id}. "
        f"Hospital: {hospital_name}. "
        f"Please visit the hospital help desk or use a supported patient access method "
        f"to retrieve your prescription. Do not share this reference publicly."
    )


def dispatch_notification(
    *,
    destination: str,
    message: str,
    appointment_id: str,
    prescription_ref: str,
    channel: str = "sms_reference",
) -> dict:
    provider = (os.getenv("NOTIFICATION_PROVIDER") or "console").strip().lower()
    payload = {
        "channel": channel,
        "to": destination,
        "message": message,
        "appointment_id": appointment_id,
        "prescription_ref": prescription_ref,
        "sent_at": datetime.now().isoformat(),
        "sensitive_clinical_data": False,
    }

    result = {"provider": provider, "status": "queued", "detail": ""}

    if provider == "webhook":
        url = os.getenv("NOTIFICATION_WEBHOOK_URL", "").strip()
        if not url:
            result["status"] = "error"
            result["detail"] = "NOTIFICATION_WEBHOOK_URL is not configured"
            return result
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            api_key = os.getenv("NOTIFICATION_API_KEY", "").strip()
            if api_key:
                req.add_header("Authorization", f"Bearer {api_key}")
            with urllib.request.urlopen(req, timeout=8) as resp:
                result["status"] = "sent" if 200 <= resp.status < 300 else "error"
                result["detail"] = f"HTTP {resp.status}"
        except urllib.error.URLError as exc:
            result["status"] = "error"
            result["detail"] = str(exc)
        return result

    if provider == "file":
        path = os.getenv("NOTIFICATION_LOG_FILE", "./notification_outbox.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
            result["status"] = "sent"
            result["detail"] = path
        except OSError as exc:
            result["status"] = "error"
            result["detail"] = str(exc)
        return result

    print("[RxVault notification]", json.dumps(payload, ensure_ascii=False))
    result["status"] = "sent"
    result["detail"] = "console"
    return result
