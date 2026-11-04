"""
notify.py - email and SMS sending.

Nothing here is required to run the demo. With no settings the server prints the message to
its console instead ("simulated"), so you can always read the verification code there.

Real email (Gmail): turn on 2-step verification, create an "App password", then set
    SMTP_USER=you@gmail.com   SMTP_PASS=<16-character app password>
Real SMS (Africa's Talking): set AT_USERNAME and AT_API_KEY (username "sandbox" only reaches
the AT simulator, not a real phone), optionally AT_SENDER_ID.
"""
import os
import smtplib
from email.message import EmailMessage

import requests


def send_email(to, subject, body):
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not user or not password:
        print(f"\n[SIMULATED EMAIL to {to}] {subject}\n{body}\n")
        return "simulated"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"NextHome Energy <{user}>", to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                          int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
        return "sent"
    except Exception as e:                      # never crash a request over an email
        print(f"[EMAIL FAILED to {to}] {e}")
        return "failed"


def send_sms(phone, text):
    username, key = os.environ.get("AT_USERNAME"), os.environ.get("AT_API_KEY")
    if not username or not key or not phone:
        print("\n" + "=" * 60)
        print(f"[SIMULATED SMS to {phone or 'no phone on file'}]\n{text}")
        print("=" * 60 + "\n")
        return "simulated"
    host = "api.sandbox.africastalking.com" if username == "sandbox" else "api.africastalking.com"
    data = {"username": username, "to": phone, "message": text}
    if os.environ.get("AT_SENDER_ID"):
        data["from"] = os.environ["AT_SENDER_ID"]
    try:
        r = requests.post(f"https://{host}/version1/messaging", data=data, timeout=10,
                          headers={"apiKey": key, "Accept": "application/json"})
        return "sent" if r.ok else "failed"
    except requests.exceptions.RequestException as e:
        print(f"[SMS FAILED to {phone}] {e}")
        return "failed"