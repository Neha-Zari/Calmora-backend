
import os
import base64
from email.mime.text import MIMEText
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request


# Gmail permission
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "gmail_credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")


def get_gmail_service():
    creds = None

    # FORCE CORRECT FILE PATHS
    base_dir = Path(__file__).resolve().parent
    token_file = base_dir / "gmail_token.json"
    credentials_file = base_dir / "gmail_credentials.json"

    print("TOKEN FILE:", token_file)
    print("TOKEN EXISTS:", token_file.exists())

    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_file),
                SCOPES
            )

            print("CREDS VALID:", creds.valid)
            print("CREDS EXPIRED:", creds.expired)
            print("HAS REFRESH TOKEN:", bool(creds.refresh_token))

        except Exception as e:
            print("TOKEN LOADING ERROR:", repr(e))
            return None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())

            token_file.write_text(
                creds.to_json(),
                encoding="utf-8"
            )

        except Exception as e:
            print("TOKEN REFRESH ERROR:", repr(e))
            return None

    if not creds or not creds.valid:
        print("GMAIL CREDENTIALS ARE NOT VALID")
        return None

    try:
        service = build("gmail", "v1", credentials=creds)
        print("GMAIL SERVICE CREATED")
        return service

    except Exception as e:
        print("GMAIL SERVICE ERROR:", repr(e))
        return None
def send_email(to_email, subject, body):
    print("EMAIL FUNCTION CALLED")
    print("Sending email to:", to_email)

    try:
        service = get_gmail_service()

        if service is None:
            print("GMAIL SERVICE IS NONE")
            return False

        print("GMAIL SERVICE READY")

        message = MIMEText(body)
        message["to"] = to_email
        message["subject"] = subject

        raw_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode()

        message_body = {
            "raw": raw_message
        }

        sent_message = service.users().messages().send(
            userId="me",
            body=message_body
        ).execute()

        print("EMAIL SENT SUCCESSFULLY:", sent_message.get("id"))

        return True

    except Exception as e:
        print("EMAIL SENDING FAILED:", repr(e))
        return False