import os, re, json, secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from gmail import send_email

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, request, jsonify, redirect, make_response
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, set_access_cookies, unset_jwt_cookies, jwt_required, get_jwt_identity
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors
from difflib import get_close_matches
from spellchecker import SpellChecker
import requests
print(">>> THIS IS THE APP.PY I AM RUNNING <<<")

try:
    from authlib.integrations.flask_client import OAuth
except Exception:
    OAuth = None

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None

try:
    from pywebpush import webpush, WebPushException
except Exception:
    webpush = None
    WebPushException = Exception

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "final_expanded.xlsx"
DB_FILE = BASE_DIR / "app.db"

app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_FILE}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    JWT_SECRET_KEY=os.getenv("JWT_SECRET_KEY", "change-this-secret-in-production"),
    JWT_TOKEN_LOCATION=["cookies"],
    JWT_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    JWT_COOKIE_SAMESITE="Lax",
    JWT_COOKIE_CSRF_PROTECT=False,
)
CORS(app, supports_credentials=True, origins=os.getenv("FRONTEND_URL", "http://localhost:5173"))
db = SQLAlchemy(app)
jwt = JWTManager(app)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
APP_NAME = "Anxiety Chatbot"
RESET_SECRET = os.getenv("RESET_SECRET", app.config["JWT_SECRET_KEY"])
serializer = URLSafeTimedSerializer(RESET_SECRET)

# ------------------------- Database -------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(320), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)
    role = db.Column(db.String(20), nullable=False, default="patient")
    name = db.Column(db.String(120), nullable=True)
    avatar_data = db.Column(db.Text, nullable=True)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    coping = db.Column(db.Text, default="")
    checkup = db.Column(db.Text, default="")
    has_exercise = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    question = db.Column(db.Text, nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DoctorReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(30), nullable=False, default="low_rated")
    rating = db.Column(db.Integer, nullable=True)
    user_id = db.Column(db.Integer, nullable=True)
    resolved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AppNotification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    message = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(40), default="general")
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UserSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    dark_mode = db.Column(db.Boolean, default=False)
    notifications_enabled = db.Column(db.Boolean, default=True)
    reminders_enabled = db.Column(db.Boolean, default=True)
    reminder_tone = db.Column(db.String(40), default="soft")
    timezone = db.Column(db.String(80), default="Asia/Karachi")

class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)

class Reminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(180), default="Exercise reminder")
    message = db.Column(db.Text, default="Time for your recommended exercise.")
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ------------------------- Chatbot: kept intact in principle -------------------------
df = pd.read_excel(DATA_FILE, sheet_name="Sheet1")
df = df[['Question', 'Answer/Solution', 'Practical Coping Strategy', 'Advised Checkup']].fillna("")

special_responses = {
    "deep breathing": "Try slow deep breathing: inhale gently through your nose for about 4 seconds, pause briefly, then exhale slowly for about 6 seconds. Repeat for a few minutes and stop if you feel uncomfortable.",
    "grounding techniques": "Try the 5-4-3-2-1 grounding technique: name 5 things you see, 4 things you can touch, 3 things you hear, 2 things you smell, and 1 thing you taste.",
    "mindfulness": "Pause and notice your breathing, body sensations, thoughts, and surroundings without judging them. Bring your attention back to the present moment whenever your mind wanders."
}
grounding_keywords = ["grounding techniques", "grounding", "5-4-3-2-1 technique"]
breathing_keywords = ["deep breathing", "breathing", "breathing exercise"]
mindfulness_keywords = ["mindfulness", "mindfulness practice", "mindfulness techniques"]
spell = SpellChecker()

vectorizer = None
model = None
corpus = []

def rebuild_chat_model():
    global vectorizer, model, corpus, df
    df = pd.read_excel(DATA_FILE, sheet_name="Sheet1")
    df = df[['Question', 'Answer/Solution', 'Practical Coping Strategy', 'Advised Checkup']].fillna("")
    # Keep the knowledge source intact but remove exact duplicates for retrieval quality.
    retrieval_df = df.drop_duplicates(subset=["Question"], keep="first").reset_index(drop=True)
    corpus = retrieval_df["Question"].astype(str).tolist()
    vectorizer = TfidfVectorizer(stop_words="english")
    X = vectorizer.fit_transform(corpus)
    model = NearestNeighbors(n_neighbors=1, metric="cosine")
    model.fit(X)
    return retrieval_df

retrieval_df = rebuild_chat_model()

def correct_spelling(user_query):
    words = user_query.split()
    corrected_words = [spell.correction(word) or word for word in words]
    return " ".join(corrected_words)

def is_special_query(user_query):
    q = user_query.lower().strip()
    groups = [
        (grounding_keywords, special_responses["grounding techniques"]),
        (breathing_keywords, special_responses["deep breathing"]),
        (mindfulness_keywords, special_responses["mindfulness"]),
    ]
    for keywords, response in groups:
        if any(k in q for k in keywords):
            return response
        close = get_close_matches(q, keywords, n=1, cutoff=0.72)
        if close:
            return response
    return None

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def generate_with_llama4(user_query, retrieved_context):
    if not OPENROUTER_API_KEY:
        return retrieved_context
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "meta-llama/llama-4-scout",
        "messages": [
            {"role": "system", "content": "You are an anxiety coping assistant. Use only the supplied context. Do not diagnose. Preserve the practical coping strategy and advised checkup information. If symptoms may be an emergency, advise urgent professional/emergency care rather than reassurance."},
            {"role": "user", "content": f"Question: {user_query}\n\nRelevant context:\n{retrieved_context}\n\nAnswer:"}
        ]
    }
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=30)
        result = response.json()
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
    except Exception:
        pass
    return retrieved_context

def get_response(user_query):
    corrected_query = correct_spelling(user_query)
    special = is_special_query(corrected_query)
    if special:
        return special, "", "", False
    query_vec = vectorizer.transform([corrected_query])
    distances, indices = model.kneighbors(query_vec, n_neighbors=1)
    idx = indices[0][0]
    similarity = 1 - distances[0][0]
    if similarity < 0.3:
        return "Please enter a question related to anxiety.", "", "", False
    row = retrieval_df.iloc[idx]
    solution = str(row['Answer/Solution'])
    coping = str(row['Practical Coping Strategy'])
    doctor = str(row['Advised Checkup'])
    context = f"Answer/Solution: {solution}\nPractical Coping Strategy: {coping}\nAdvised Checkup: {doctor}"
    answer = generate_with_llama4(corrected_query, context)
    has_exercise = bool(re.search(r"exercise|walk|walking|physical activity|stretch|activity|workout", f"{coping} {solution}", re.I))
    return answer, coping, doctor, has_exercise

# ------------------------- Helpers -------------------------
def current_user():
    uid = int(get_jwt_identity())
    return db.session.get(User, uid)

def name_from_email(email):
    local = email.split("@")[0]
    token = re.split(r"[._\-+]+", local)[0]
    # If an email has no separator, keep it readable rather than guessing a person's legal name.
    return token[:1].upper() + token[1:] if token else "User"

def ensure_settings(user_id):
    settings = UserSettings.query.filter_by(user_id=user_id).first()
    if not settings:
        settings = UserSettings(user_id=user_id)
        db.session.add(settings)
        db.session.commit()
    return settings

def add_notification(user_id, title, message, kind="general"):
    settings = UserSettings.query.filter_by(user_id=user_id).first()
    if settings and not settings.notifications_enabled:
        return None
    n = AppNotification(user_id=user_id, title=title, message=message, kind=kind)
    db.session.add(n)
    db.session.commit()
    return n



def send_auth_email(user, kind):
    if kind == "signup":
        return send_email(
            user.email,
            "Welcome to Calmora",
            f"""Hello {user.name or 'User'},

Your Calmora account was created successfully.

If you did not create this account, please secure your email account.

Regards,
Calmora Chatbot
"""
        )

    if kind == "login":
        return send_email(
            user.email,
            "New login to Calmora",
            f"""Hello {user.name or 'User'},

Your Calmora account was just signed in.

If this was not you, change your password and secure your account.

Regards,
Calmora Chatbot
"""
        )

    return False

def review_lists():
    low = DoctorReview.query.filter_by(source="low_rated", resolved=False).order_by(DoctorReview.created_at.desc()).all()
    invalid = DoctorReview.query.filter_by(source="invalid", resolved=False).order_by(DoctorReview.created_at.desc()).all()
    return low, invalid

def generate_reset_token(email):
    return serializer.dumps({"email": email}, salt="password-reset")

def verify_reset_token(token, max_age=1800):
    return serializer.loads(token, salt="password-reset", max_age=max_age)

# ------------------------- Auth -------------------------
@app.post("/api/auth/signup")
def signup():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not email or "@" not in email or len(password) < 6:
        return jsonify({"message": "Enter a valid email and a password of at least 6 characters."}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"message": "An account with this email already exists."}), 409
    user = User(email=email, password_hash=generate_password_hash(password), role="patient", name=name_from_email(email))
    db.session.add(user)
    db.session.commit()
    ensure_settings(user.id)
    add_notification(user.id, "Account created", "Your account was created successfully.", "signup")
    send_auth_email(user, "signup")
    token = create_access_token(identity=str(user.id))
    resp = jsonify({"message": "Account created successfully", "user": serialize_user(user)})
    set_access_cookies(resp, token)
    return resp, 201

@app.post("/api/auth/login")
def login():
    data = request.get_json() or {}

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    user = User.query.filter_by(email=email).first()

    # Check login credentials
    if not user or not user.password_hash:
        return jsonify({"message": "Invalid email or password."}), 401

    if not check_password_hash(user.password_hash, password):
        return jsonify({"message": "Invalid email or password."}), 401

    print("LOGIN SUCCESS:", user.email)

    # In-app notification
    try:
        add_notification(
            user.id,
            "New login",
            "Your account was signed in successfully.",
            "login"
        )
        print("IN-APP LOGIN NOTIFICATION CREATED")
    except Exception as e:
        print("IN-APP NOTIFICATION ERROR:", repr(e))

    # Email notification
    try:
        print("CALLING LOGIN EMAIL...")
        email_result = send_auth_email(user, "login")
        print("LOGIN EMAIL RESULT:", email_result)
    except Exception as e:
        print("LOGIN EMAIL ERROR:", repr(e))

    # Create JWT
    token = create_access_token(identity=str(user.id))

    resp = jsonify({
        "message": "Login successful",
        "user": serialize_user(user)
    })

    set_access_cookies(resp, token)

    print("LOGIN RESPONSE SENT")

    return resp

@app.post("/api/auth/logout")
def logout():
    resp = jsonify({"message": "Logged out"})
    unset_jwt_cookies(resp)
    return resp

@app.get("/api/auth/me")
@jwt_required()
def me():
    return jsonify({"user": serialize_user(current_user())})

@app.post("/api/auth/forgot-password")
def forgot_password():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    user = User.query.filter_by(email=email).first()
    # Same response whether the account exists or not.
    if user:
        token = generate_reset_token(user.email)
        reset_url = f"{FRONTEND_URL}/reset-password?token={token}"
        body = f"Hello {user.name or 'User'},\n\nWe received a request to reset your Anxiety Chatbot password.\n\nReset your password here:\n{reset_url}\n\nThis link expires in 30 minutes. If you did not request this, you can ignore this email."
        send_email(user.email, "Reset your Anxiety Chatbot password", body)
        add_notification(user.id, "Password reset requested", "A password reset link was sent to your email.", "security")
    return jsonify({"message": "If an account exists for this email, a password reset link has been sent."})

@app.post("/api/auth/reset-password")
def reset_password():
    data = request.get_json() or {}
    token = data.get("token", "")
    new_password = data.get("password", "")
    if len(new_password) < 6:
        return jsonify({"message": "Password must be at least 6 characters."}), 400
    try:
        payload = verify_reset_token(token)
    except (BadSignature, SignatureExpired):
        return jsonify({"message": "This reset link is invalid or expired."}), 400
    user = User.query.filter_by(email=payload["email"]).first()
    if not user:
        return jsonify({"message": "Account not found."}), 404
    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    add_notification(user.id, "Password changed", "Your password was changed successfully.", "security")
    return jsonify({"message": "Password changed successfully. You can now sign in."})

@app.post("/api/auth/change-password")
@jwt_required()
def change_password():
    data = request.get_json() or {}
    user = current_user()
    if not user.password_hash or not check_password_hash(user.password_hash, data.get("currentPassword", "")):
        return jsonify({"message": "Current password is incorrect."}), 400
    if len(data.get("newPassword", "")) < 6:
        return jsonify({"message": "New password must be at least 6 characters."}), 400
    user.password_hash = generate_password_hash(data["newPassword"])
    db.session.commit()
    # Per requirement: no email notification for password change.
    add_notification(user.id, "Password updated", "Your password was changed successfully.", "security")
    return jsonify({"message": "Password changed successfully."})

# ------------------------- Google OAuth -------------------------
if OAuth:
    oauth = OAuth(app)
    if os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
        google = oauth.register(
            name="google",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    else:
        google = None
else:
    google = None

@app.get("/api/auth/google")
def google_login():
    if not google:
        return jsonify({"message": "Google OAuth is not configured yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env."}), 503
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:5000/api/auth/google/callback")
    return google.authorize_redirect(redirect_uri)

@app.get("/api/auth/google/callback")
def google_callback():
    if not google:
        return redirect(f"{FRONTEND_URL}/login?error=google_not_configured")
    try:
        token = google.authorize_access_token()
        userinfo = token.get("userinfo") or google.userinfo()
        email = userinfo["email"].strip().lower()
        google_id = userinfo.get("sub")
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(email=email, role="patient", name=userinfo.get("given_name") or name_from_email(email), google_id=google_id)
            db.session.add(user)
        else:
            user.google_id = google_id or user.google_id
            if not user.name:
                user.name = userinfo.get("given_name") or name_from_email(email)
        db.session.commit()
        ensure_settings(user.id)
        add_notification(user.id, "Google sign-in", "You signed in with Google successfully.", "login")
        send_auth_email(user, "login")
        jwt_token = create_access_token(identity=str(user.id))
        resp = make_response(redirect(f"{FRONTEND_URL}/dashboard"))
        set_access_cookies(resp, jwt_token)
        return resp
    except Exception as exc:
        print("Google OAuth error:", exc)
        return redirect(f"{FRONTEND_URL}/login?error=google_failed")

# ------------------------- User profile/settings -------------------------
def serialize_user(user):
    return {"id": user.id, "email": user.email, "name": user.name or name_from_email(user.email), "role": user.role, "avatarData": user.avatar_data}

@app.get("/api/profile")
@jwt_required()
def get_profile():
    return jsonify({"user": serialize_user(current_user())})

@app.put("/api/profile")
@jwt_required()
def update_profile():
    data = request.get_json() or {}
    user = current_user()
    if data.get("avatarData") is not None:
        user.avatar_data = data["avatarData"]
    # Email intentionally cannot be changed.
    if data.get("name"):
        user.name = data["name"].strip()
    db.session.commit()
    return jsonify({"user": serialize_user(user)})

@app.get("/api/settings")
@jwt_required()
def get_settings():
    s = ensure_settings(current_user().id)
    return jsonify({"darkMode": s.dark_mode, "notificationsEnabled": s.notifications_enabled, "remindersEnabled": s.reminders_enabled, "reminderTone": s.reminder_tone})

@app.put("/api/settings")
@jwt_required()
def update_settings():
    s = ensure_settings(current_user().id)
    data = request.get_json() or {}
    for key, attr in [("darkMode", "dark_mode"), ("notificationsEnabled", "notifications_enabled"), ("remindersEnabled", "reminders_enabled"), ("reminderTone", "reminder_tone")]:
        if key in data:
            setattr(s, attr, data[key])
    db.session.commit()
    return jsonify({"message": "Settings saved"})

# ------------------------- Chat/history/feedback -------------------------
@app.post("/api/chat")
@jwt_required()
def chat():
    user = current_user()
    question = (request.get_json() or {}).get("question", "").strip()
    if not question:
        return jsonify({"message": "Please enter a question."}), 400
    answer, coping, checkup, has_exercise = get_response(question)
    if "Please enter a question related to anxiety" in answer:
        db.session.add(DoctorReview(question=question, source="invalid", user_id=user.id))
        db.session.commit()
        for doctor in User.query.filter_by(role="doctor", is_active=True).all():
            add_notification(doctor.id, "Question needs review", question, "doctor_review")
        return jsonify({"answer": answer, "coping": "", "checkup": "", "hasExercise": False})
    h = ChatHistory(user_id=user.id, question=question, answer=answer, coping=coping, checkup=checkup, has_exercise=has_exercise)
    db.session.add(h)
    db.session.commit()
    return jsonify({"id": h.id, "answer": answer, "coping": coping, "checkup": checkup, "hasExercise": has_exercise})

@app.get("/api/history")
@jwt_required()
def history():
    rows = ChatHistory.query.filter_by(user_id=current_user().id).order_by(ChatHistory.created_at.desc()).all()
    return jsonify({"questions": [{"id": x.id, "question": x.question, "createdAt": x.created_at.isoformat()} for x in rows]})

@app.get("/api/history/<int:item_id>")
@jwt_required()
def history_item(item_id):
    x = ChatHistory.query.filter_by(id=item_id, user_id=current_user().id).first_or_404()
    return jsonify({"question": x.question, "answer": x.answer, "coping": x.coping, "checkup": x.checkup, "hasExercise": x.has_exercise, "createdAt": x.created_at.isoformat()})

@app.post("/api/feedback")
@jwt_required()
def feedback():
    data = request.get_json() or {}
    rating = int(data.get("rating", 0))
    question = data.get("question", "").strip()
    if not question or rating < 1 or rating > 5:
        return jsonify({"message": "Invalid feedback."}), 400
    db.session.add(Feedback(user_id=current_user().id, question=question, rating=rating))
    if rating < 3:
        db.session.add(DoctorReview(question=question, source="low_rated", rating=rating, user_id=current_user().id))
        db.session.commit()
        for doctor in User.query.filter_by(role="doctor", is_active=True).all():
            add_notification(doctor.id, "Low-rated question", f"A patient rated this question {rating}/5: {question}", "doctor_review")
        return jsonify({"message": "Feedback submitted and sent for doctor review."})
    db.session.commit()
    return jsonify({"message": "Feedback submitted."})

# ------------------------- Notifications -------------------------
@app.get("/api/notifications")
@jwt_required()
def notifications():
    rows = AppNotification.query.filter_by(user_id=current_user().id).order_by(AppNotification.created_at.desc()).limit(50).all()
    unread = AppNotification.query.filter_by(user_id=current_user().id, read=False).count()
    return jsonify({"unread": unread, "items": [{"id": n.id, "title": n.title, "message": n.message, "kind": n.kind, "read": n.read, "createdAt": n.created_at.isoformat()} for n in rows]})

@app.post("/api/notifications/read-all")
@jwt_required()
def read_all_notifications():
    AppNotification.query.filter_by(user_id=current_user().id, read=False).update({"read": True})
    db.session.commit()
    return jsonify({"message": "Notifications marked as read."})

# ------------------------- Doctor -------------------------
@app.get("/api/doctor/reviews")
@jwt_required()
def doctor_reviews():
    user = current_user()
    if user.role != "doctor":
        return jsonify({"message": "Doctor access required."}), 403
    low, invalid = review_lists()
    return jsonify({
        "lowRated": [{"id": x.id, "question": x.question, "rating": x.rating} for x in low],
        "invalid": [{"id": x.id, "question": x.question} for x in invalid],
        "pendingCount": len(low) + len(invalid)
    })

@app.post("/api/doctor/reply")
@jwt_required()
def doctor_reply():
    user = current_user()
    if user.role != "doctor":
        return jsonify({"message": "Doctor access required."}), 403
    data = request.get_json() or {}
    question = data.get("question", "").strip()
    answer = data.get("answer", "").strip()
    coping = data.get("coping", "").strip()
    checkup = data.get("checkup", "").strip()
    review_id = data.get("reviewId")
    if not all([question, answer, coping, checkup]):
        return jsonify({"message": "All answer, coping strategy and checkup fields are required."}), 400
    existing = pd.read_excel(DATA_FILE, sheet_name="Sheet1")
    new_entry = pd.DataFrame([{"Question": question, "Answer/Solution": answer, "Practical Coping Strategy": coping, "Advised Checkup": checkup}])
    updated = pd.concat([existing, new_entry], ignore_index=True)
    updated.to_excel(DATA_FILE, sheet_name="Sheet1", index=False)
    global retrieval_df
    retrieval_df = rebuild_chat_model()
    if review_id:
        r = db.session.get(DoctorReview, int(review_id))
        if r: r.resolved = True
    else:
        for r in DoctorReview.query.filter_by(question=question, resolved=False).all():
            r.resolved = True
    db.session.commit()
    return jsonify({"message": "Doctor reply saved and chatbot knowledge refreshed."})

# ------------------------- Reminders + Push -------------------------
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS_EMAIL = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:admin@example.com")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")

@app.get("/api/push/public-key")
def push_public_key():
    return jsonify({"publicKey": VAPID_PUBLIC_KEY})

@app.post("/api/push/subscribe")
@jwt_required()
def push_subscribe():
    data = request.get_json() or {}
    keys = data.get("keys", {})
    if not data.get("endpoint") or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"message": "Invalid push subscription."}), 400
    old = PushSubscription.query.filter_by(endpoint=data["endpoint"]).first()
    if old:
        old.user_id = current_user().id
        old.p256dh = keys["p256dh"]
        old.auth = keys["auth"]
    else:
        db.session.add(PushSubscription(user_id=current_user().id, endpoint=data["endpoint"], p256dh=keys["p256dh"], auth=keys["auth"]))
    db.session.commit()
    return jsonify({"message": "Push subscription saved."})

@app.get("/api/reminders")
@jwt_required()
def get_reminders():
    rows = Reminder.query.filter_by(user_id=current_user().id, active=True).all()
    return jsonify({"reminders": [{"id": r.id, "title": r.title, "message": r.message, "active": r.active} for r in rows]})

@app.post("/api/reminders")
@jwt_required()
def create_reminder():
    data = request.get_json() or {}
    r = Reminder(user_id=current_user().id, title=data.get("title", "Exercise reminder"), message=data.get("message", "Time for your recommended exercise."), active=True)
    db.session.add(r)
    db.session.commit()
    return jsonify({"id": r.id, "message": "Reminder enabled."}), 201

def send_push(user_id, title, message):
    if not webpush or not VAPID_PRIVATE_KEY:
        return False
    subs = PushSubscription.query.filter_by(user_id=user_id).all()
    sent = False
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=json.dumps({"title": title, "body": message, "url": "/dashboard?tab=reminders"}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIMS_EMAIL},
            )
            sent = True
        except Exception:
            db.session.delete(sub)
    db.session.commit()
    return sent

def reminder_job():
    with app.app_context():
        now = datetime.now(ZoneInfo("Asia/Karachi"))
        if now.minute != 0 or now.hour not in [7, 10, 13, 16, 19]:
            return
        for r in Reminder.query.filter_by(active=True).all():
            settings = ensure_settings(r.user_id)
            if not settings.reminders_enabled:
                continue
            add_notification(r.user_id, "Exercise reminder", r.message, "reminder")
            send_push(r.user_id, "Exercise reminder", r.message)

scheduler = None
if BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(reminder_job, "interval", minutes=1, id="exercise-reminders", replace_existing=True)
    scheduler.start()

# ------------------------- Health -------------------------
@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "chatbot": True, "llama": bool(OPENROUTER_API_KEY), "email": bool(os.getenv("SMTP_HOST")), "google": bool(google), "push": bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)})

with app.app_context():
    db.create_all()
    doctor_email = os.getenv("DOCTOR_EMAIL", "").strip().lower()
    doctor_password = os.getenv("DOCTOR_PASSWORD", "")
    if doctor_email and doctor_password:
        doctor = User.query.filter_by(email=doctor_email).first()
        if not doctor:
            doctor = User(email=doctor_email, password_hash=generate_password_hash(doctor_password), role="doctor", name=name_from_email(doctor_email))
            db.session.add(doctor)
            db.session.commit()
        elif doctor.role != "doctor":
            doctor.role = "doctor"
            db.session.commit()
        ensure_settings(doctor.id)

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
