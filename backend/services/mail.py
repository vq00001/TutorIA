import os
import smtplib
from email.message import EmailMessage


def send_token(address, purpose, raw):
    origin = os.environ["APP_ORIGIN"].rstrip("/")
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = address
    message["Subject"] = (
        "TutorIA — Verificación" if purpose == "verify_email" else "TutorIA — Recuperación"
    )
    message.set_content(f"{origin}/cuenta/{purpose}?token={raw}\nEste enlace es de un solo uso.")
    with smtplib.SMTP(
        os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "1025")), timeout=5
    ) as smtp:
        if os.environ.get("SMTP_STARTTLS", "true").lower() == "true":
            smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)
