from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow


BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_FILE = BASE_DIR / "gmail_credentials.json"
TOKEN_FILE = BASE_DIR / "gmail_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send"
]


if not CREDENTIALS_FILE.exists():
    print("gmail_credentials.json not found!")
    raise SystemExit(1)


flow = InstalledAppFlow.from_client_secrets_file(
    str(CREDENTIALS_FILE),
    SCOPES
)

credentials = flow.run_local_server(
    port=0,
    access_type="offline",
    prompt="consent"
)

TOKEN_FILE.write_text(
    credentials.to_json(),
    encoding="utf-8"
)

print("SUCCESS: Gmail authorization completed!")
print("Token saved at:", TOKEN_FILE)