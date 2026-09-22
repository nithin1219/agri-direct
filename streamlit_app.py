"""AgriDirect: a self-contained Streamlit marketplace demo.

All data lives in st.session_state so the app is zero-setup and suitable for
local demos, classroom use, and quick deployment on Streamlit Community Cloud.
"""

from datetime import datetime
from datetime import date
import base64
import hashlib
import io
import json
import math
import os
import re
import secrets
import sqlite3
from urllib.parse import quote_plus

import pandas as pd
import plotly.express as px
import streamlit as st

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None
    BotoCoreError = ClientError = OSError

try:
    import face_recognition
except ImportError:
    face_recognition = None
try:
    import numpy as np
except ImportError:
    np = None

try:
    import speech_recognition as speech_recognition
except ImportError:
    speech_recognition = None


st.set_page_config(
    page_title="AgriDirect Marketplace",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)

DELIVERY_FEE = 40
ORDER_STATUSES = ["Placed", "Confirmed", "Preparing", "Out for delivery", "Delivered"]
DEMO_ACCOUNTS = {
    "customer@agridirect.local": ("Customer", "customer123", "customer"),
    "farmer@agridirect.local": ("Farmer", "farmer123", "greenvalley"),
    "orchard@agridirect.local": ("Farmer", "orchard123", "sunrise"),
    "admin@agridirect.local": ("Admin", "admin123", "admin"),
}
FARM_BACKGROUND = "https://images.unsplash.com/photo-1500382017468-9049fed747ef?auto=format&fit=crop&w=2200&q=85"
IMAGE_TYPES = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"]
DATABASE_PATH = os.getenv("AGRIDIRECT_DATABASE", "agridirect_users.db")


def database_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL CHECK(role IN ('Customer', 'Farmer', 'Admin')),
                password_hash TEXT NOT NULL,
                frs_photo BLOB,
                frs_photo_name TEXT,
                face_encoding TEXT,
                last_face_verification TEXT
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        if "frs_enabled" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN frs_enabled INTEGER NOT NULL DEFAULT 0")
            connection.execute("UPDATE users SET frs_enabled = 1 WHERE role IN ('Farmer', 'Admin')")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS marketplace_state (
                state_key TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def load_database_users():
    try:
        initialize_database()
        with database_connection() as connection:
            rows = connection.execute("SELECT * FROM users").fetchall()
    except sqlite3.Error:
        return {}
    users = {}
    for row in rows:
        try:
            encoding = json.loads(row["face_encoding"]) if row["face_encoding"] else None
        except json.JSONDecodeError:
            encoding = None
        users[row["email"]] = {
            "email": row["email"],
            "username": row["username"],
            "role": row["role"],
            "password": row["password_hash"],
            "frs_photo": row["frs_photo"],
            "frs_photo_name": row["frs_photo_name"],
            "face_encoding": encoding,
            "last_face_verification": row["last_face_verification"],
            "frs_enabled": bool(row["frs_enabled"]),
        }
    return users


def save_database_user(user):
    try:
        initialize_database()
        with database_connection() as connection:
            connection.execute(
            """
            INSERT INTO users
                (email, username, role, password_hash, frs_photo, frs_photo_name, face_encoding, last_face_verification, frs_enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                username=excluded.username,
                role=excluded.role,
                password_hash=excluded.password_hash,
                frs_photo=excluded.frs_photo,
                frs_photo_name=excluded.frs_photo_name,
                face_encoding=excluded.face_encoding,
                last_face_verification=excluded.last_face_verification,
                frs_enabled=excluded.frs_enabled
            """,
                (
                    user["email"],
                    user["username"],
                    user["role"],
                    user["password"],
                    user.get("frs_photo"),
                    user.get("frs_photo_name"),
                    json.dumps(user.get("face_encoding")) if user.get("face_encoding") else None,
                    user.get("last_face_verification"),
                    int(user.get("frs_enabled", user["role"] in {"Farmer", "Admin"})),
                ),
            )
            connection.commit()
    except sqlite3.Error:
        return False
    return True


def delete_database_user(email):
    try:
        initialize_database()
        with database_connection() as connection:
            connection.execute("DELETE FROM users WHERE email = ?", (email,))
            connection.commit()
    except sqlite3.Error:
        return False
    return True


def enforce_single_admin(users, configured_admin=None):
    """Keep exactly one administrator, preferring the configured owner account."""
    admin_emails = sorted(
        email for email, user in users.items() if user.get("role") == "Admin"
    )
    if configured_admin:
        canonical_email = configured_admin["email"]
    elif "admin@agridirect.local" in users and users["admin@agridirect.local"].get("role") == "Admin":
        canonical_email = "admin@agridirect.local"
    elif admin_emails:
        canonical_email = admin_emails[0]
    else:
        return users
    for email in admin_emails:
        if email != canonical_email:
            users.pop(email, None)
            delete_database_user(email)
    return users


def database_account_exists(email, username):
    try:
        initialize_database()
        with database_connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM users WHERE lower(email) = ? OR lower(username) = ? LIMIT 1",
                (email.strip().lower(), username.strip().lower()),
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def load_local_snapshot():
    """Load marketplace data from the persistent SQLite store used by this deployment."""
    try:
        initialize_database()
        with database_connection() as connection:
            row = connection.execute(
                "SELECT state_json FROM marketplace_state WHERE state_key = 'marketplace'"
            ).fetchone()
        return json.loads(row["state_json"]) if row else None
    except (sqlite3.Error, TypeError, json.JSONDecodeError):
        return None


def save_local_snapshot(payload):
    try:
        initialize_database()
        with database_connection() as connection:
            connection.execute(
                """
                INSERT INTO marketplace_state (state_key, state_json, updated_at)
                VALUES ('marketplace', ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    state_json=excluded.state_json, updated_at=excluded.updated_at
                """,
                (json.dumps(payload, default=str), datetime.now().isoformat()),
            )
            connection.commit()
        return True
    except (sqlite3.Error, TypeError, ValueError):
        return False


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def password_matches(password, stored_hash):
    salt, expected = stored_hash.split("$", 1)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return secrets.compare_digest(actual, expected)


def configured_admin_account():
    values = {
        "email": os.getenv("ADMIN_EMAIL"),
        "username": os.getenv("ADMIN_USERNAME"),
        "password": os.getenv("ADMIN_PASSWORD"),
    }
    try:
        section = st.secrets.get("admin", {})
        if hasattr(section, "get"):
            for key in values:
                values[key] = section.get(key) or values[key]
        for key in values:
            values[key] = st.secrets.get(f"ADMIN_{key.upper()}") or values[key]
    except (FileNotFoundError, KeyError, TypeError):
        pass
    if not all(values.values()) or len(values["password"]) < 8:
        return None
    return {
        "email": values["email"].strip().lower(),
        "username": values["username"].strip().lower(),
        "role": "Admin",
        "password": values["password"],
    }


def _encode_bytes(value):
    return base64.b64encode(value).decode("ascii") if isinstance(value, bytes) else value


def _decode_bytes(value):
    try:
        return base64.b64decode(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return None


def load_cloud_snapshot():
    """Restore persisted app state when an optional S3 bucket is configured."""
    bucket, region = storage_config()
    if not bucket or boto3 is None:
        return None
    try:
        response = boto3.client("s3", region_name=region).get_object(
            Bucket=bucket, Key="agridirect/state.json"
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except (BotoCoreError, ClientError, OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def face_encoding(image_bytes):
    if face_recognition is None:
        return None
    try:
        image = face_recognition.load_image_file(io.BytesIO(image_bytes))
        encodings = face_recognition.face_encodings(image)
        return encodings[0].tolist() if encodings else None
    except (OSError, ValueError, RuntimeError):
        return None


def verify_face(image_bytes, reference):
    if face_recognition is None or np is None or not reference:
        return False
    try:
        candidate = face_encoding(image_bytes)
        return bool(candidate and face_recognition.compare_faces([np.asarray(reference)], np.asarray(candidate), tolerance=0.48)[0])
    except (OSError, ValueError, RuntimeError):
        return False


def storage_config():
    bucket = os.getenv("AGRI_S3_BUCKET")
    region = os.getenv("AWS_REGION")
    try:
        bucket = bucket or st.secrets.get("AGRI_S3_BUCKET")
        region = region or st.secrets.get("AWS_REGION")
    except (FileNotFoundError, KeyError, AttributeError, TypeError):
        pass
    return bucket, region


def save_cloud_snapshot():
    """Persist a complete snapshot locally and, when configured, to shared S3 storage."""
    payload = {
        "products": [{**product, "image_bytes": _encode_bytes(product.get("image_bytes"))} for product in st.session_state.products],
        "users": {email: {**user, "frs_photo": _encode_bytes(user.get("frs_photo"))} for email, user in st.session_state.users.items()},
        "orders": st.session_state.orders,
    }
    local_saved = save_local_snapshot(payload)
    bucket, region = storage_config()
    if not bucket or boto3 is None:
        return local_saved
    try:
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket,
            Key="agridirect/state.json",
            Body=json.dumps(payload, default=str).encode(),
            ContentType="application/json",
        )
    except (BotoCoreError, ClientError, OSError, ValueError):
        # Cloud credentials are optional; never make checkout or publishing fail.
        return local_saved
    return local_saved


def clear_persisted_marketplace():
    try:
        initialize_database()
        with database_connection() as connection:
            connection.execute("DELETE FROM marketplace_state WHERE state_key = 'marketplace'")
            connection.commit()
    except sqlite3.Error:
        pass
    bucket, region = storage_config()
    if bucket and boto3 is not None:
        try:
            boto3.client("s3", region_name=region).delete_object(Bucket=bucket, Key="agridirect/state.json")
        except (BotoCoreError, ClientError, OSError):
            pass


def sync_cloud_snapshot():
    """Refresh shared marketplace data without disturbing the signed-in session."""
    snapshot = load_cloud_snapshot() or load_local_snapshot()
    if not snapshot:
        return False
    if snapshot.get("products") is not None:
        st.session_state.products = snapshot["products"]
        for product in st.session_state.products:
            product["image_bytes"] = _decode_bytes(product.get("image_bytes"))
            product.setdefault("farmer_location", "Location not provided")
            product.setdefault("crop_details", product.get("description", ""))
    if snapshot.get("users") is not None:
        current_email = st.session_state.get("authenticated_user")
        st.session_state.users = snapshot["users"]
        for user in st.session_state.users.values():
            user["frs_photo"] = _decode_bytes(user.get("frs_photo"))
            user.setdefault("frs_enabled", user.get("role") in {"Farmer", "Admin"})
        st.session_state.users = enforce_single_admin(
            st.session_state.users, configured_admin_account()
        )
        if current_email and current_email not in st.session_state.users:
            st.session_state.authenticated_user = None
    if snapshot.get("orders") is not None:
        st.session_state.orders = snapshot["orders"]
    st.session_state.next_product_id = max(
        (product.get("id", 0) for product in st.session_state.products), default=0
    ) + 1
    st.session_state.next_order_id = max(
        (order.get("id", 1000) for order in st.session_state.orders), default=1000
    ) + 1
    return True


def seed_state():
    """Create a fresh in-memory marketplace for the current browser session."""
    snapshot = load_cloud_snapshot() or load_local_snapshot()
    if "products" not in st.session_state:
        seeded_products = [
            {"id": 1, "name": "Farm Fresh Tomatoes", "category": "Vegetables", "price": 48.0, "unit": "kg", "stock": 32, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "farmer_location": "Shamirpet, Hyderabad", "farmer_lat": 17.595, "farmer_lon": 78.561, "crop_details": "Vine-ripened; harvested this morning.", "organic": True, "description": "Juicy, vine-ripened tomatoes harvested this morning.", "emoji": "🍅", "image_url": "https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=900"},
            {"id": 2, "name": "Alphonso Mangoes", "category": "Fruits", "price": 180.0, "unit": "kg", "stock": 18, "farmer": "Sunrise Orchards", "farmer_id": "orchard@agridirect.local", "farmer_location": "Vikarabad, Telangana", "farmer_lat": 17.338, "farmer_lon": 77.904, "crop_details": "Naturally ripened seasonal mangoes.", "organic": True, "description": "Naturally sweet seasonal mangoes from our orchard.", "emoji": "🥭", "image_url": "https://images.unsplash.com/photo-1553279768-865429fa0078?w=900"},
            {"id": 3, "name": "Organic Basmati Rice", "category": "Grains", "price": 125.0, "unit": "kg", "stock": 50, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "farmer_location": "Shamirpet, Hyderabad", "farmer_lat": 17.595, "farmer_lon": 78.561, "crop_details": "Aromatic long-grain rice; no synthetic pesticides.", "organic": True, "description": "Aromatic long-grain rice, grown without synthetic pesticides.", "emoji": "🌾", "image_url": "https://images.unsplash.com/photo-1536304993881-ff6e9eefa2a6?w=900"},
            {"id": 4, "name": "Cold-Pressed Groundnut Oil", "category": "Pantry", "price": 220.0, "unit": "litre", "stock": 12, "farmer": "Harvest Collective", "farmer_id": "farmer@agridirect.local", "farmer_location": "Medchal, Telangana", "farmer_lat": 17.629, "farmer_lon": 78.481, "crop_details": "Small-batch wood-pressed groundnuts.", "organic": False, "description": "Small-batch wood-pressed oil with a rich, nutty flavour.", "emoji": "🫙", "image_url": "https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?w=900"},
            {"id": 5, "name": "Fresh Spinach", "category": "Vegetables", "price": 35.0, "unit": "bunch", "stock": 40, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "farmer_location": "Shamirpet, Hyderabad", "farmer_lat": 17.595, "farmer_lon": 78.561, "crop_details": "Tender leafy greens picked at sunrise.", "organic": True, "description": "Tender leafy greens picked at sunrise.", "emoji": "🥬", "image_url": "https://images.unsplash.com/photo-1576045057995-568f588f82fb?w=900"},
            {"id": 6, "name": "Raw Forest Honey", "category": "Pantry", "price": 310.0, "unit": "500 g", "stock": 15, "farmer": "Sunrise Orchards", "farmer_id": "orchard@agridirect.local", "farmer_location": "Vikarabad, Telangana", "farmer_lat": 17.338, "farmer_lon": 77.904, "crop_details": "Unfiltered wildflower honey from local hives.", "organic": True, "description": "Unfiltered wildflower honey collected from local hives.", "emoji": "🍯", "image_url": "https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=900"},
        ]
        st.session_state.products = (
            snapshot["products"] if snapshot and "products" in snapshot else seeded_products
        )
        for product in st.session_state.products:
            product["image_bytes"] = _decode_bytes(product.get("image_bytes"))
            product.setdefault("farmer_location", "Location not provided")
            product.setdefault("crop_details", product.get("description", ""))
    st.session_state.setdefault("cart", {})
    st.session_state.setdefault("orders", (snapshot or {}).get("orders", []))
    st.session_state.setdefault(
        "next_product_id",
        max((product.get("id", 0) for product in st.session_state.products), default=6) + 1,
    )
    st.session_state.setdefault(
        "next_order_id",
        max((order.get("id", 1000) for order in st.session_state.orders), default=1000) + 1,
    )
    if "users" not in st.session_state:
        st.session_state.users = load_database_users() or {
            email: {
                "email": email,
                "role": role,
                "username": username,
                "password": password_hash(password),
                "frs_enabled": role in {"Farmer", "Admin"},
            }
            for email, (role, password, username) in DEMO_ACCOUNTS.items()
        }
        if snapshot and snapshot.get("users"):
            st.session_state.users = snapshot["users"]
            for user in st.session_state.users.values():
                user["frs_photo"] = _decode_bytes(user.get("frs_photo"))
        for user in st.session_state.users.values():
            save_database_user(user)
    admin = configured_admin_account()
    st.session_state.users = enforce_single_admin(st.session_state.users, admin)
    if admin:
        existing = st.session_state.users.get(admin["email"], {})
        account = {
            **existing,
            "email": admin["email"],
            "username": admin["username"],
            "role": "Admin",
            "password": password_hash(admin["password"]),
            "frs_enabled": True,
        }
        st.session_state.users[admin["email"]] = account
        save_database_user(account)
    else:
        for user in st.session_state.users.values():
            if user.get("role") == "Admin":
                save_database_user(user)
    st.session_state.setdefault("authenticated_user", None)
    if not snapshot:
        save_cloud_snapshot()


def registration_view():
    st.subheader("Create your AgriDirect account")
    st.caption("Complete the form once. Your account is saved for future sign-ins.")
    st.markdown("#### 🎙️ AI account assistant")
    st.caption("Speak or type your email, username, and account type. The assistant fills those fields; enter your password manually.")
    registration_language = st.selectbox("Assistant language", list(ASSISTANT_LANGUAGES), key="registration-assistant-language")
    registration_audio_input = getattr(st, "audio_input", None)
    if callable(registration_audio_input):
        registration_audio = registration_audio_input("Record account details (optional)", key="registration-assistant-audio")
        if registration_audio and st.button("Transcribe account details", key="transcribe-registration-audio"):
            if speech_recognition is None:
                st.warning("Voice transcription is unavailable; use the text fallback.")
            else:
                try:
                    recognizer = speech_recognition.Recognizer()
                    with speech_recognition.AudioFile(io.BytesIO(registration_audio.getvalue())) as source:
                        transcript = recognizer.record(source)
                    st.session_state["registration-assistant-text"] = recognizer.recognize_google(
                        transcript, language=ASSISTANT_LANGUAGES[registration_language]
                    )
                except (OSError, ValueError, speech_recognition.UnknownValueError, speech_recognition.RequestError):
                    st.warning("The recording could not be transcribed. Please use the text fallback.")
    registration_request = st.text_area(
        "Voice transcript or text details",
        key="registration-assistant-text",
        placeholder="Example: customer, email me@example.com, username freshbuyer",
    )
    if st.button("Fill account details", key="apply-registration-assistance"):
        if apply_account_assistance(registration_request, "registration-page"):
            st.success("Email, username, and account type filled. Enter your password manually.")
    account_role = st.selectbox("Account type", ["Customer", "Farmer"], key="registration-page-role")
    farmer_photo = st.file_uploader(
        "Farmer FRS profile photo (required for farmer accounts)",
        type=IMAGE_TYPES,
        help="Upload a clear face photo for the farmer registration profile.",
        disabled=account_role != "Farmer",
        key="registration-page-photo",
    )
    with st.form("registration-page-form"):
        new_email = st.text_input("Email address", key="registration-page-email")
        new_username = st.text_input("Username", key="registration-page-username")
        new_password = st.text_input("Password", type="password", key="registration-page-password")
        confirm_password = st.text_input("Confirm password", type="password", key="registration-page-confirm")
        create_account = st.form_submit_button("Create account", type="primary", use_container_width=True)
    if create_account:
        normalized_email = new_email.strip().lower()
        username = new_username.strip().lower()
        if "@" not in normalized_email or not new_password or not username:
            st.error("Enter a valid email, username, and password.")
        elif len(new_password) < 8:
            st.error("Password must be at least 8 characters.")
        elif new_password != confirm_password:
            st.error("Passwords do not match.")
        elif normalized_email in st.session_state.users or database_account_exists(normalized_email, username):
            st.error("An account with that email already exists.")
        elif any(user["username"] == username for user in st.session_state.users.values()):
            st.error("That username is already taken.")
        elif account_role == "Farmer" and not farmer_photo:
            st.error("Farmer registration requires an FRS profile photo.")
        else:
            enrolled_encoding = face_encoding(farmer_photo.getvalue()) if farmer_photo and account_role == "Farmer" else None
            if account_role == "Farmer" and face_recognition is not None and not enrolled_encoding:
                st.error("No clear face was found in that photo. Upload one clear, front-facing farmer photo.")
                return
            account = {
                "email": normalized_email, "role": account_role, "username": username,
                "password": password_hash(new_password),
                "frs_photo": farmer_photo.getvalue() if farmer_photo else None,
                "frs_photo_name": farmer_photo.name if farmer_photo else None,
                "face_encoding": enrolled_encoding,
                "frs_enabled": account_role == "Farmer",
            }
            if not save_database_user(account):
                st.error("Your account could not be saved. Check the database location and try again.")
            else:
                st.session_state.users[account["email"]] = account
                save_cloud_snapshot()
                st.session_state.authenticated_user = account["email"]
                st.session_state.role = account["role"]
                st.session_state.pop("show_create_account", None)
                st.success("Account created successfully. Welcome to AgriDirect!")
                st.rerun()
    if st.button("Back to sign in", key="back-to-signin"):
        st.session_state.pop("show_create_account", None)
        st.rerun()


def password_reset_view():
    st.subheader("Reset your password")
    st.caption("Use the email address or username saved on this local AgriDirect deployment.")
    with st.form("password-reset-form"):
        identity = st.text_input("Registered email or username", key="reset-identity")
        new_password = st.text_input("New password", type="password", key="reset-password")
        confirm_password = st.text_input("Confirm new password", type="password", key="reset-confirm")
        reset_submitted = st.form_submit_button("Save new password", type="primary", use_container_width=True)
    if reset_submitted:
        login_value = identity.strip().lower()
        user = st.session_state.users.get(login_value)
        if not user:
            user = next(
                (candidate for candidate in st.session_state.users.values()
                 if candidate.get("username") == login_value),
                None,
            )
        if not user:
            st.error("No account was found for that email or username.")
        elif len(new_password) < 8:
            st.error("Password must be at least 8 characters.")
        elif new_password != confirm_password:
            st.error("Passwords do not match.")
        else:
            user["password"] = password_hash(new_password)
            if save_database_user(user):
                save_cloud_snapshot()
                st.session_state.pop("show_password_reset", None)
                st.success("Your password was reset successfully. Sign in with the new password.")
                st.rerun()
            else:
                st.error("The password could not be saved. Check the database location and try again.")
    if st.button("Back to sign in", key="back-from-password-reset"):
        st.session_state.pop("show_password_reset", None)
        st.rerun()


def authentication_view():
    st.title("🌱 Welcome to AgriDirect")
    st.success("Welcome! Sign in to continue to your AgriDirect marketplace.")
    st.write("Use your registered email or username and password to shop, manage listings, or review marketplace operations.")
    st.session_state.setdefault("login_voice_mode", False)
    voice_left, voice_right = st.columns([4, 1])
    with voice_left:
        st.markdown("#### 🎙️ AI voice assistance")
        st.caption("Choose a language and use your microphone or text to get help before signing in.")
    with voice_right:
        voice_label = "Voice help: ON" if st.session_state.login_voice_mode else "AI voice help"
        if st.button(voice_label, type="primary" if st.session_state.login_voice_mode else "secondary", key="login-voice-toggle", use_container_width=True):
            st.session_state.login_voice_mode = not st.session_state.login_voice_mode
            st.rerun()
    if st.session_state.login_voice_mode:
        with st.container(border=True):
            language = st.selectbox(
                "Assistance language",
                list(ASSISTANT_LANGUAGES),
                key="login-assistant-language",
            )
            st.info(
                f"Voice assistance is active in {language}. Allow microphone access, record your question, "
                "then transcribe it. If recording is unavailable, type your request below."
            )
            audio_input = getattr(st, "audio_input", None)
            if callable(audio_input):
                audio = audio_input("Record a sign-in question (optional)", key="login-assistant-audio")
                if audio and st.button("Transcribe sign-in help", key="transcribe-login-audio"):
                    if speech_recognition is None:
                        st.warning("Transcription support is unavailable; use the text box below.")
                    else:
                        try:
                            recognizer = speech_recognition.Recognizer()
                            with speech_recognition.AudioFile(io.BytesIO(audio.getvalue())) as source:
                                transcript = recognizer.record(source)
                            st.session_state["login-assistant-text"] = recognizer.recognize_google(
                                transcript, language=ASSISTANT_LANGUAGES[language]
                            )
                            st.success("Voice request transcribed. Review the text below.")
                        except (OSError, ValueError, speech_recognition.UnknownValueError, speech_recognition.RequestError):
                            st.warning("The recording could not be transcribed. Please use the text fallback.")
            else:
                st.info("Microphone input is unavailable in this Streamlit version; text help is enabled.")
            request = st.text_area(
                "Voice transcript or text request",
                key="login-assistant-text",
                placeholder="Example: How do I sign in or create a farmer account?",
            )
            if st.button("Fill sign-in details", key="apply-login-assistance"):
                if apply_account_assistance(request, "login"):
                    st.success("Email or username filled. Enter your password manually.")
            if st.button("Show sign-in instructions", key="login-assistant-submit"):
                st.info(
                    "Enter your registered email or username and password in the Sign in tab. "
                    "Use Create account for a new customer or farmer account. Existing accounts are saved and cannot be registered twice."
                )
    pending_face_email = st.session_state.get("pending_face_login")
    if pending_face_email:
        pending_user = st.session_state.users.get(pending_face_email)
        if not pending_user:
            st.session_state.pop("pending_face_login", None)
            st.error("The pending face-login request is no longer valid.")
            return
        st.subheader("Face verification required")
        st.info("Password accepted. Capture the enrolled farmer's face to finish signing in.")
        if face_recognition is None:
            st.error("Face matching is unavailable on this deployment. A native face-recognition installation is required for farmer login.")
            if st.button("Cancel face verification", key="cancel-face-login"):
                st.session_state.pop("pending_face_login", None)
                st.rerun()
            return
        if not pending_user.get("face_encoding"):
            st.error("This farmer has no enrolled face profile and cannot sign in.")
            if st.button("Cancel face verification", key="cancel-face-login-missing"):
                st.session_state.pop("pending_face_login", None)
                st.rerun()
            return
        face_capture = st.camera_input("Capture the enrolled farmer's face", key="login-face-capture")
        if face_capture:
            if verify_face(face_capture.getvalue(), pending_user["face_encoding"]):
                st.session_state.pop("pending_face_login", None)
                st.session_state.authenticated_user = pending_user["email"]
                st.session_state.role = pending_user["role"]
                st.success("Face matched. Sign-in complete.")
                st.rerun()
            else:
                st.error("Face mismatch. This farmer account cannot be opened by another person.")
        return
    if st.session_state.get("show_create_account"):
        registration_view()
        return
    if st.session_state.get("show_password_reset"):
        password_reset_view()
        return
    login_tab, register_tab = st.tabs(["Sign in", "Create account"])
    with login_tab:
        with st.form("login-form"):
            email = st.text_input("Email or username", key="login-email", placeholder="you@example.com or greenvalley")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            login_value = email.strip().lower()
            user = st.session_state.users.get(login_value)
            if not user:
                user = next((candidate for candidate in st.session_state.users.values() if candidate["username"] == login_value), None)
            if user and password_matches(password, user["password"]):
                if user["role"] == "Farmer":
                    st.session_state.pending_face_login = user["email"]
                else:
                    st.session_state.authenticated_user = user["email"]
                    st.session_state.role = user["role"]
                st.rerun()
            else:
                st.error("Invalid email or password.")
        if st.button("Create account", use_container_width=True, key="create-account-below-signin"):
            st.session_state["show_create_account"] = True
            st.rerun()
        if st.button("Forgot password?", use_container_width=True, key="forgot-password"):
            st.session_state["show_password_reset"] = True
            st.rerun()
        if st.session_state.get("show_create_account"):
            st.info("Open the Create account tab above to register once. Your account is saved for future sign-ins.")
    with register_tab:
        account_role = st.selectbox("Account type", ["Customer", "Farmer"], key="register-role")
        farmer_photo = st.file_uploader(
            "Farmer FRS profile photo (required for farmer accounts)",
            type=IMAGE_TYPES,
            help="Upload a clear face photo for the farmer registration profile.",
            disabled=account_role != "Farmer",
            key="frs-profile-photo",
        )
        with st.form("register-form"):
            new_email = st.text_input("Email address", key="register-email")
            new_username = st.text_input("Username", key="register-username", help="Farmers use this username to sign in to the FRS portal.")
            new_password = st.text_input("Password", type="password", key="register-password")
            confirm_password = st.text_input("Confirm password", type="password")
            create_account = st.form_submit_button("Create account", type="primary", use_container_width=True)
        if create_account:
            normalized_email = new_email.strip().lower()
            username = new_username.strip().lower()
            if "@" not in normalized_email or not new_password or not username:
                st.error("Enter a valid email and password.")
            elif len(new_password) < 8:
                st.error("Password must be at least 8 characters.")
            elif new_password != confirm_password:
                st.error("Passwords do not match.")
            elif normalized_email in st.session_state.users or database_account_exists(normalized_email, username):
                st.error("An account with that email already exists.")
            elif any(user["username"] == username for user in st.session_state.users.values()):
                st.error("That username is already taken.")
            elif account_role == "Farmer" and not farmer_photo:
                st.error("Farmer registration requires an FRS profile photo.")
            else:
                enrolled_encoding = (
                    face_encoding(farmer_photo.getvalue())
                    if farmer_photo and account_role == "Farmer" else None
                )
                if account_role == "Farmer" and face_recognition is not None and not enrolled_encoding:
                    st.error("No clear face was found in that photo. Upload one clear, front-facing farmer photo.")
                    return
                account = {
                    "email": normalized_email,
                    "role": account_role,
                    "username": username,
                    "password": password_hash(new_password),
                    "frs_photo": farmer_photo.getvalue() if farmer_photo else None,
                    "frs_photo_name": farmer_photo.name if farmer_photo else None,
                    "face_encoding": enrolled_encoding,
                    "frs_enabled": account_role == "Farmer",
                }
                if not save_database_user(account):
                    st.error("Your account could not be saved. Check the database location and try again.")
                else:
                    st.session_state.users[account["email"]] = account
                    save_cloud_snapshot()
                    st.session_state.authenticated_user = account["email"]
                    st.session_state.role = account["role"]
                    st.success("Your account is ready. Welcome to AgriDirect!")
                    st.rerun()


def money(value):
    return f"₹{value:,.2f}"


ASSISTANT_LANGUAGES = {
    "English": "en-IN",
    "Telugu": "te-IN",
    "Hindi": "hi-IN",
    "Tamil": "ta-IN",
    "Kannada": "kn-IN",
    "Malayalam": "ml-IN",
    "Bengali": "bn-IN",
    "Marathi": "mr-IN",
}


def apply_listing_assistance(text):
    """Use lightweight, dependency-free extraction to prefill a listing draft."""
    text = text.strip()
    if not text:
        return False
    lowered = text.lower()
    category = next(
        (value for value in ["Vegetables", "Fruits", "Grains", "Pantry", "Dairy"]
         if value[:-1].lower() in lowered or value.lower() in lowered),
        None,
    )
    number = re.search(r"(?:₹|rs\.?|price\s*)\s*(\d+(?:\.\d+)?)", lowered)
    stock = re.search(r"(?:stock|quantity|available)\s*(?:is|:)?\s*(\d+)", lowered)
    unit = re.search(r"\b(kg|kilograms?|g|grams?|litres?|liters?|l|bunch(?:es)?)\b", lowered)
    name = re.search(r"(?:product|crop|name)\s*(?:is|:)?\s*([a-z][\w -]{2,40})", lowered)
    st.session_state["product-name-field"] = (name.group(1).strip(" .,") if name else text.split(",")[0][:50]).title()
    st.session_state["product-description-field"] = text
    if category:
        st.session_state["product-category-field"] = category
    if number:
        st.session_state["product-price-field"] = float(number.group(1))
    if stock:
        st.session_state["product-stock-field"] = int(stock.group(1))
    if unit:
        st.session_state["product-unit-field"] = unit.group(1).lower().rstrip("s")
    return True


def apply_account_assistance(text, field_prefix):
    text = text.strip()
    if not text:
        return False
    lowered = text.lower()
    email = re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", lowered)
    username = re.search(r"(?:username|user name|login)\s*(?:is|:)?\s*([a-z0-9_.-]{3,32})", lowered)
    role = "Farmer" if re.search(r"\bfarmer\b|\bformar\b|\bరైతు\b", lowered) else "Customer"
    if email:
        st.session_state[f"{field_prefix}-email"] = email.group(0)
    if username:
        st.session_state[f"{field_prefix}-username"] = username.group(1)
        if field_prefix == "login" and not email:
            st.session_state["login-email"] = username.group(1)
    st.session_state[f"{field_prefix}-role"] = role
    return bool(email or username)


def listing_assistant():
    st.subheader("AI / voice listing assistant")
    language = st.selectbox("Assistant language", list(ASSISTANT_LANGUAGES), key="assistant-language")
    st.caption("Describe your crop or product in your language. The assistant provides draft field values; review them before publishing.")
    audio_input = getattr(st, "audio_input", None)
    if callable(audio_input):
        audio = audio_input("Record a description (optional)", key="listing-audio")
        if audio and st.button("Transcribe recording", key="transcribe-listing-audio"):
            if speech_recognition is None:
                st.warning("Voice transcription is unavailable. Install the optional speech_recognition package, or use the text box below.")
            else:
                try:
                    recognizer = speech_recognition.Recognizer()
                    with speech_recognition.AudioFile(io.BytesIO(audio.getvalue())) as source:
                        transcript = recognizer.record(source)
                    st.session_state["listing-assistant-text"] = recognizer.recognize_google(
                        transcript, language=ASSISTANT_LANGUAGES[language]
                    )
                    st.success("Recording transcribed. Review the text and apply it to the draft.")
                except (OSError, ValueError, speech_recognition.UnknownValueError, speech_recognition.RequestError):
                    st.warning("The recording could not be transcribed. Please use the text fallback.")
    else:
        st.info("Microphone input is not available in this Streamlit version; text fallback is enabled.")
    assistant_text = st.text_area(
        "Text fallback / transcript",
        key="listing-assistant-text",
        placeholder="Example: Product is tomatoes, price 60 per kg, stock 25, vegetables.",
    )
    if st.button("Apply details to listing draft", key="apply-listing-assistance") and apply_listing_assistance(assistant_text):
        st.success("Draft fields filled. Review all values before publishing.")


def ai_voice_mode(user_role):
    st.session_state.setdefault("ai_voice_mode", False)
    header_left, header_right = st.columns([5, 1])
    with header_left:
        st.markdown("### AgriDirect AI assistant")
        st.caption("Use your voice or type instructions in your selected language.")
    with header_right:
        label = "🎙️ AI Voice Mode: ON" if st.session_state.ai_voice_mode else "🎙️ AI Voice Mode"
        if st.button(label, type="primary" if st.session_state.ai_voice_mode else "secondary", use_container_width=True):
            st.session_state.ai_voice_mode = not st.session_state.ai_voice_mode
            st.rerun()
    if not st.session_state.ai_voice_mode:
        return
    with st.container(border=True):
        st.success("AI Voice Mode is active.")
        st.markdown(
            "1. Select a language.  \n"
            "2. Allow microphone access when your browser asks.  \n"
            "3. Record your crop or product details, or type them as a fallback.  \n"
            "4. Transcribe the recording, review the suggested fields, and apply them before saving."
        )
        st.caption(
            "The assistant only prepares a draft. Check price, stock, crop name, and description yourself before publishing. "
            "Microphone transcription depends on optional browser/package support."
        )
        if user_role == "Farmer":
            listing_assistant()
        else:
            language = st.selectbox(
                "Conversation language",
                list(ASSISTANT_LANGUAGES),
                key="general-assistant-language",
            )
            st.info(
                f"Selected language: {language}. Voice input can help you describe marketplace needs; "
                "customers can still use the text fallback if microphone support is unavailable."
            )


def current_role():
    email = st.session_state.get("authenticated_user")
    return st.session_state.users.get(email, {}).get("role", "Customer")


def current_user():
    return st.session_state.users[st.session_state.authenticated_user]


def daily_farmer_verification():
    user = current_user()
    if user["role"] not in {"Farmer", "Admin"} or not user.get("frs_enabled", True):
        return True
    if user.get("last_face_verification") == date.today().isoformat():
        return True
    st.subheader(f"Daily {user['role'].lower()} FRS verification")
    if face_recognition is None:
        st.info("Live camera access is active. Native face matching is unavailable, so secure login plus today's camera capture is used.")
        camera_capture = st.camera_input("Allow camera access and capture your face to continue")
        if not camera_capture:
            st.warning("Camera access is required for today's FRS verification.")
            return False
        user["last_face_verification"] = date.today().isoformat()
        save_database_user(user)
        save_cloud_snapshot()
        st.success("Daily FRS camera verification complete.")
        return True
    if not user.get("face_encoding"):
        st.error("Native face verification is enabled, but this farmer has no enrolled face profile. Ask an administrator to reset enrollment.")
        return False
    photo = st.camera_input("Allow camera access and capture your face to continue")
    if not photo:
        st.warning("Camera access is required for today's farmer dashboard verification.")
        return False
    if verify_face(photo.getvalue(), user["face_encoding"]):
        user["last_face_verification"] = date.today().isoformat()
        save_database_user(user)
        save_cloud_snapshot()
        st.success("Daily verification complete.")
        return True
    st.error("Face verification failed. Try again with good lighting and one face in the frame.")
    return False


def add_to_cart(product_id, quantity=1):
    product = next((p for p in st.session_state.products if p["id"] == product_id), None)
    if not product or product.get("stock", 0) <= 0:
        return
    existing = st.session_state.cart.get(product_id, 0)
    st.session_state.cart[product_id] = min(
        max(existing + int(quantity), 1), int(product["stock"])
    )


def refresh_app():
    """Rerun the complete app so cart badges and tabs reflect mutations immediately."""
    st.rerun()


def product_location(product):
    return product.get("farmer_location") or "Location not provided"


def route_link(product, destination):
    origin = product_location(product)
    if product.get("farmer_lat") is not None and product.get("farmer_lon") is not None:
        origin = f"{product['farmer_lat']},{product['farmer_lon']}"
    return (
        "https://www.google.com/maps/dir/?api=1&origin="
        f"{quote_plus(str(origin))}&destination={quote_plus(destination)}"
    )


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    lat1, lon1, lat2, lon2 = [math.radians(float(value)) for value in (lat1, lon1, lat2, lon2)]
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return radius * 2 * math.asin(math.sqrt(value))


def estimated_eta(distance_km):
    return max(20, round(25 + float(distance_km) * 4))


def order_date(order):
    try:
        return datetime.fromisoformat(order.get("created_iso", "")).date().isoformat()
    except (TypeError, ValueError):
        try:
            return datetime.strptime(order.get("created", ""), "%d %b %Y, %I:%M %p").date().isoformat()
        except (TypeError, ValueError):
            return ""


def cart_rows():
    rows = []
    stale_ids = []
    for product_id, quantity in st.session_state.cart.items():
        product = next((p for p in st.session_state.products if p["id"] == product_id), None)
        if product and product.get("stock", 0) > 0 and quantity:
            safe_quantity = min(int(quantity), int(product["stock"]))
            if safe_quantity != quantity:
                st.session_state.cart[product_id] = safe_quantity
            rows.append((product, safe_quantity))
        elif product_id in st.session_state.cart:
            stale_ids.append(product_id)
    for product_id in stale_ids:
        del st.session_state.cart[product_id]
    return rows


def show_product_card(product):
    with st.container(border=True):
        if product.get("image_bytes"):
            st.image(product["image_bytes"], caption=product.get("image_name", "Uploaded product image"), use_container_width=True)
        elif product.get("image_url"):
            st.image(product["image_url"], use_container_width=True)
        st.markdown(f"### {product['emoji']} {product['name']}")
        st.caption(f"{product['farmer']} · {product['category']}")
        st.write(f"**Farmer location:** {product_location(product)}")
        st.caption(f"**Crop details:** {product.get('crop_details') or 'Details not provided'}")
        st.write(product["description"])
        st.markdown(f"**{money(product['price'])} / {product['unit']}**")
        st.caption(f"{'Organic' if product['organic'] else 'Conventional'} · {product['stock']} {product['unit']} available")
        if current_role() == "Customer":
            if st.button("Add to cart", key=f"add-{product['id']}", use_container_width=True):
                add_to_cart(product["id"])
                st.toast(f"Added {product['name']} to your cart")
                refresh_app()


def streamlit_fragment(**kwargs):
    fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
    if fragment is None:
        return lambda function: function
    return fragment(**kwargs)


def customer_view():
    st.title("🌱 Shop directly from local farms")
    st.write("Fresh produce, fair prices, and transparent farmer relationships.")
    st.success("All farmer listings are shared across customer accounts and refresh automatically.")
    cart_count = sum(st.session_state.cart.values())
    cart_preview = cart_rows()
    if cart_preview:
        st.info(
            "Cart items: " + " · ".join(
                f"{product['name']} × {quantity}" for product, quantity in cart_preview
            )
        )
    else:
        st.caption("Your cart is empty. Add products from Browse products.")
    tabs = st.tabs(["Browse products", f"Cart ({cart_count})", "My orders"])

    with tabs[0]:
        marketplace_browser()

    with tabs[1]:
        render_cart()

    with tabs[2]:
        render_orders()


@streamlit_fragment(run_every="1s")
def marketplace_browser():
    """Refresh shared listings every second without interrupting cart or checkout."""
    synced = sync_cloud_snapshot()
    st.caption("Marketplace listings update automatically every second.")
    if st.button("Refresh marketplace now", key="refresh-marketplace"):
        if synced or sync_cloud_snapshot():
            st.success("Latest farmer listings are now visible.")
        else:
            st.info("No shared snapshot is available yet; showing this deployment's local marketplace.")
        st.rerun(scope="fragment")
    left, right = st.columns([2, 1])
    with left:
        search = st.text_input("Search products", placeholder="Try tomatoes, rice, or honey")
    with right:
        categories = ["All categories"] + sorted({p["category"] for p in st.session_state.products})
        category = st.selectbox("Category", categories)
    organic_only = st.checkbox("Show organic products only")
    filtered = [
        p for p in st.session_state.products
        if (not search or search.lower() in f"{p['name']} {p['description']} {p['farmer']}".lower())
        and (category == "All categories" or p["category"] == category)
        and (not organic_only or p["organic"])
        and p["stock"] > 0
    ]
    if not filtered:
        st.info("No products match those filters.")
    else:
        columns = st.columns(3)
        for index, product in enumerate(filtered):
            with columns[index % 3]:
                show_product_card(product)


def render_cart():
    rows = cart_rows()
    if not rows:
        st.info("Your cart is empty. Add something fresh from the Browse products tab.")
        return
    subtotal = 0
    for product, quantity in rows:
        line_total = product["price"] * quantity
        subtotal += line_total
        columns = st.columns([3, 1, 1, 1])
        columns[0].write(f"**{product['emoji']} {product['name']}**\n\n{money(product['price'])} / {product['unit']}")
        columns[1].number_input("Qty", min_value=1, max_value=product["stock"], value=quantity, key=f"qty-{product['id']}", on_change=update_quantity, args=(product["id"],))
        columns[2].write(money(line_total))
        if columns[3].button("Remove", key=f"remove-{product['id']}"):
            del st.session_state.cart[product["id"]]
            st.rerun()
    st.divider()
    st.metric("Subtotal", money(subtotal))
    st.caption(f"Delivery fee: {money(DELIVERY_FEE)} · Total: {money(subtotal + DELIVERY_FEE)}")
    with st.expander("Checkout and complete purchase", expanded=True):
        context_options = {
            f"{product['name']} · {product['farmer']} ({product_location(product)})": product
            for product, _ in rows
        }
        selected_context_label = st.selectbox(
            "Purchase context (farmer/listing)",
            list(context_options),
            help="Choose the farmer/listing used for delivery ETA and route details.",
            key="checkout-context",
        )
        with st.form("checkout"):
            address = st.text_area("Delivery address", placeholder="House number, street, locality")
            city = st.text_input("City", value="Hyderabad")
            pincode = st.text_input("PIN code", max_chars=6)
            customer_lat = st.text_input("Your latitude (optional)", placeholder="17.385")
            customer_lon = st.text_input("Your longitude (optional)", placeholder="78.486")
            manual_distance = st.number_input(
                "Distance from selected farm (km, optional fallback)",
                min_value=0.0,
                value=0.0,
                step=0.5,
                help="Used only when both farmer and customer coordinates are not available.",
            )
            st.info("Payment option: Cash on Delivery. Payment is collected when the order is delivered.")
            submitted = st.form_submit_button("Complete purchase", type="primary", use_container_width=True)
        if submitted:
            if not address.strip() or len(pincode.strip()) != 6 or not pincode.isdigit():
                st.error("Enter a delivery address and a valid 6-digit PIN code.")
            else:
                selected_product = context_options[selected_context_label]
                distance = manual_distance
                if customer_lat.strip() and customer_lon.strip() and selected_product.get("farmer_lat") is not None:
                    try:
                        distance = haversine_km(
                            selected_product["farmer_lat"], selected_product["farmer_lon"],
                            float(customer_lat), float(customer_lon),
                        )
                    except ValueError:
                        st.warning("Coordinates were not valid, so the manual distance fallback was used.")
                eta = estimated_eta(distance)
                destination = f"{address.strip()}, {city.strip()} - {pincode.strip()}"
                st.info(f"Estimated delivery: **{eta} minutes** (20–30 minute baseline + distance adjustment).")
                st.markdown(
                    f"[Open live route in Google Maps]({route_link(selected_product, destination)}) · "
                    f"[Open route in OpenStreetMap](https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route="
                    f"{quote_plus(product_location(selected_product))}%3B{quote_plus(destination)})"
                )
                place_order(
                    address.strip(), city.strip(), pincode.strip(), subtotal + DELIVERY_FEE,
                    selected_product, distance, eta, destination,
                )


def update_quantity(product_id):
    st.session_state.cart[product_id] = st.session_state[f"qty-{product_id}"]


def place_order(address, city, pincode, total, context_product, distance_km, eta_minutes, destination):
    purchased_rows = cart_rows()
    transaction_id = f"AGR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3).upper()}"
    order = {
        "id": st.session_state.next_order_id,
        "created": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "created_iso": datetime.now().isoformat(),
        "items": [
            {"product_id": p["id"], "name": p["name"], "quantity": q, "total": p["price"] * q}
            for p, q in purchased_rows
        ],
        "total": total,
        "address": f"{address}, {city} - {pincode}",
        "status": "Placed",
        "payment": "Cash on Delivery",
        "payment_status": "Pay at delivery",
        "transaction_id": transaction_id,
        "owner_email": st.session_state.authenticated_user,
        "farmer": context_product.get("farmer"),
        "farmer_id": context_product.get("farmer_id"),
        "farmer_location": product_location(context_product),
        "listing": context_product.get("name"),
        "crop_details": context_product.get("crop_details", ""),
        "distance_km": round(float(distance_km), 1),
        "eta_minutes": int(eta_minutes),
        "route_url": route_link(context_product, destination),
    }
    st.session_state.orders.insert(0, order)
    st.session_state.next_order_id += 1
    for product, quantity in purchased_rows:
        product["stock"] -= quantity
    st.session_state.cart.clear()
    save_cloud_snapshot()
    st.success(f"Purchase completed successfully! Order #{order['id']} was created.")
    st.info(
        f"Transaction **{transaction_id}** · Total **{money(total)}** · "
        f"Payment: **Cash on Delivery** ({order['payment_status']})."
    )
    st.balloons()


def cancel_order(order):
    if order.get("status") not in {"Placed", "Confirmed", "Preparing"}:
        return False
    for item in order.get("items", []):
        product = next(
            (
                product for product in st.session_state.products
                if product.get("id") == item.get("product_id")
                or (
                    not item.get("product_id")
                    and product.get("name") == item.get("name")
                    and product.get("farmer_id") == order.get("farmer_id")
                )
            ),
            None,
        )
        if product:
            product["stock"] = int(product.get("stock", 0)) + int(item.get("quantity", 0))
    order["status"] = "Cancelled"
    order["payment_status"] = "Not collected"
    order["cancelled_at"] = datetime.now().isoformat()
    save_cloud_snapshot()
    return True


def render_orders():
    orders = [order for order in st.session_state.orders if order.get("owner_email") == st.session_state.authenticated_user]
    if not orders:
        st.info("Your placed orders will appear here.")
        return
    for order in orders:
        with st.container(border=True):
            columns = st.columns([2, 2, 1])
            columns[0].markdown(f"**Order #{order['id']}**\n\n{order['created']}")
            columns[1].write(" · ".join(f"{item['name']} × {item['quantity']}" for item in order["items"]))
            columns[2].metric(order["status"], money(order["total"]))
            st.caption(
                f"{order['payment']} ({order.get('payment_status', 'Recorded')}) · "
                f"Transaction: {order.get('transaction_id', 'Legacy order')} · "
                f"Deliver to {order['address']} · "
                f"Farmer: {order.get('farmer', 'Not recorded')} ({order.get('farmer_location', 'Location not provided')})"
            )
            st.caption(
                f"Listing: {order.get('listing', 'Not recorded')} · "
                f"Distance: {order.get('distance_km', '—')} km · "
                f"ETA estimate: {order.get('eta_minutes', '—')} minutes"
            )
            if order.get("route_url"):
                st.markdown(f"[View live delivery route]({order['route_url']})")
            if order.get("status") in {"Placed", "Confirmed", "Preparing"}:
                if st.button("Cancel order", key=f"cancel-order-{order['id']}", type="secondary"):
                    if cancel_order(order):
                        st.success(f"Your order #{order['id']} was cancelled immediately.")
                        st.rerun()


def farmer_view():
    sync_cloud_snapshot()
    st.title("🚜 Farmer workspace")
    st.write("Manage your listings and see what customers are buying.")
    farmer_id = st.session_state.get("user_email", "farmer@agridirect.local")
    mine = [p for p in st.session_state.products if p["farmer_id"] == farmer_id]
    columns = st.columns(3)
    columns[0].metric("Your listings", len(mine))
    columns[1].metric("Available stock", sum(p["stock"] for p in mine))
    columns[2].metric("Marketplace status", "Active")
    with st.expander("My FRS profile", expanded=True):
        st.write(f"**Username:** @{st.session_state.users[st.session_state.authenticated_user]['username']}")
        st.write(f"**Email:** {st.session_state.authenticated_user}")
        profile_photo = st.session_state.users[st.session_state.authenticated_user].get("frs_photo")
        if profile_photo:
            st.image(profile_photo, caption="FRS profile photo", width=180)
        else:
            st.info("No FRS profile photo has been saved for this session.")
    if not st.session_state.get("ai_voice_mode"):
        listing_assistant()
    st.subheader("Add a product")
    product_upload = st.file_uploader(
        "Upload product image",
        type=IMAGE_TYPES,
        help="Supported: JPG, JPEG, PNG, WEBP, GIF, BMP, TIF, and TIFF.",
        key="product-image-upload",
    )
    with st.form("new-product"):
        name = st.text_input("Product name", key="product-name-field")
        farm_name = st.text_input("Farm / farmer display name", value="My farm", key="farm-name-field")
        farmer_location = st.text_input(
            "Farm location (town / area)",
            placeholder="Example: Shamirpet, Hyderabad",
            key="farmer-location-field",
        )
        location_columns = st.columns(2)
        with location_columns[0]:
            farmer_lat = st.text_input("Farm latitude (optional)", key="farmer-lat-field")
        with location_columns[1]:
            farmer_lon = st.text_input("Farm longitude (optional)", key="farmer-lon-field")
        description = st.text_area("Description", key="product-description-field")
        crop_details = st.text_area(
            "Crop details",
            placeholder="Variety, harvest timing, growing method, or seasonal notes",
            key="crop-details-field",
        )
        image_url = st.text_input("Product image URL", placeholder="https://...")
        category = st.selectbox(
            "Category", ["Vegetables", "Fruits", "Grains", "Pantry", "Dairy"],
            key="product-category-field",
        )
        price, stock = st.columns(2)
        with price:
            product_price = st.number_input("Price (₹)", min_value=1.0, value=50.0, key="product-price-field")
        with stock:
            product_stock = st.number_input("Quantity in stock", min_value=1, value=10, key="product-stock-field")
        unit = st.text_input("Unit", value="kg", key="product-unit-field")
        organic = st.checkbox("Organic / naturally grown", value=True, key="product-organic-field")
        if st.form_submit_button("Publish listing", type="primary"):
            if not name.strip():
                st.error("A product name is required.")
            elif not farmer_location.strip():
                st.error("Add the farm location so customers can compare nearby listings.")
            else:
                try:
                    parsed_lat = float(farmer_lat) if farmer_lat.strip() else None
                    parsed_lon = float(farmer_lon) if farmer_lon.strip() else None
                    if parsed_lat is not None and not -90 <= parsed_lat <= 90:
                        raise ValueError
                    if parsed_lon is not None and not -180 <= parsed_lon <= 180:
                        raise ValueError
                except ValueError:
                    st.error("Farm latitude must be -90 to 90 and longitude must be -180 to 180.")
                    return
                st.session_state.products.append({
                    "id": st.session_state.next_product_id, "name": name.strip(), "category": category,
                    "price": product_price, "unit": unit.strip() or "kg", "stock": int(product_stock),
                    "farmer": farm_name.strip() or "My farm", "farmer_id": farmer_id,
                    "farmer_location": farmer_location.strip(), "farmer_lat": parsed_lat, "farmer_lon": parsed_lon,
                    "crop_details": crop_details.strip() or "Freshly harvested crop.",
                    "organic": organic,
                    "description": description.strip() or "Freshly harvested from our farm.", "emoji": "🌿",
                    "image_url": image_url.strip(),
                    "image_bytes": product_upload.getvalue() if product_upload else None,
                    "image_name": product_upload.name if product_upload else None,
                })
                st.session_state.next_product_id += 1
                if save_cloud_snapshot():
                    st.success("Your product is saved permanently and is now live in every customer marketplace.")
                    st.rerun()
                else:
                    st.session_state.products.pop()
                    st.session_state.next_product_id -= 1
                    st.error("The listing could not be saved to the database. Your product was not published; try again.")
    st.subheader("Your listings")
    if mine:
        st.dataframe(pd.DataFrame(mine)[["name", "category", "price", "unit", "stock", "organic"]], use_container_width=True, hide_index=True)
    else:
        st.info("You have no listings yet.")


def admin_view():
    st.title("🛡️ Admin dashboard")
    st.caption("Admin-only controls for farmer FRS verification and marketplace operations.")
    configured_admin = configured_admin_account()
    if configured_admin:
        st.info(f"Configured admin login: `{configured_admin['email']}` (or `{configured_admin['username']}`).")
    else:
        st.info("Demo admin login: `admin@agridirect.local` (or `admin`) · password: `admin123`. Configure a real admin in Streamlit secrets before production use.")
    customers = sum(1 for user in st.session_state.users.values() if user["role"] == "Customer")
    farmers = len({p["farmer_id"] for p in st.session_state.products})
    revenue = sum(order["total"] for order in st.session_state.orders)
    today = date.today().isoformat()
    daily_orders = [
        order for order in st.session_state.orders
        if order_date(order) == today
    ]
    daily_income = sum(order["total"] for order in daily_orders)
    pending_cod = sum(
        order["total"] for order in st.session_state.orders
        if order.get("status") != "Delivered"
    )
    columns = st.columns(5)
    columns[0].metric("Products", len(st.session_state.products))
    columns[1].metric("Farmers", farmers)
    columns[2].metric("Orders", len(st.session_state.orders))
    columns[3].metric("Total income", money(revenue), help="Gross value of all COD orders recorded by this deployment.")
    columns[4].metric("Pending COD", money(pending_cod), help="Order value not yet marked Delivered.")
    st.caption(
        f"Income today: **{money(daily_income)}** · "
        f"All-time gross income: **{money(revenue)}** · "
        "Amounts update when customers place orders and when order status changes."
    )
    report_tabs = st.tabs(["Daily income", "Transaction summary", "Full order history"])
    with report_tabs[0]:
        st.metric("Today's income", money(daily_income), help="Completed or placed COD order value recorded today.")
        st.dataframe(
            pd.DataFrame([
                {"Order": order["id"], "Time": order["created"], "Income": money(order["total"]),
                 "Farmer": order.get("farmer", "—"), "Status": order["status"]}
                for order in daily_orders
            ]),
            use_container_width=True, hide_index=True,
        )
    with report_tabs[1]:
        status_counts = pd.Series([order["status"] for order in st.session_state.orders]).value_counts() if st.session_state.orders else pd.Series(dtype=int)
        summary = pd.DataFrame([
            {"Metric": "All transactions", "Value": len(st.session_state.orders)},
            {"Metric": "Gross order value", "Value": money(revenue)},
            {"Metric": "Average order value", "Value": money(revenue / len(st.session_state.orders)) if st.session_state.orders else money(0)},
            *({"Metric": f"Orders — {status}", "Value": int(count)} for status, count in status_counts.items()),
        ])
        st.dataframe(summary, use_container_width=True, hide_index=True)
    with report_tabs[2]:
        history = [
            {
                "Order": order["id"], "Transaction": order.get("transaction_id", "Legacy order"),
                "Date": order["created"], "Customer": order.get("owner_email", "—"),
                "Listing": order.get("listing", "—"), "Farmer": order.get("farmer", "—"),
                "Farmer location": order.get("farmer_location", "—"), "Distance (km)": order.get("distance_km", "—"),
                "ETA (min)": order.get("eta_minutes", "—"), "Total": money(order["total"]),
                "Status": order["status"], "Payment": order.get("payment", "—"),
                "Payment status": order.get("payment_status", "—"), "Delivery": order["address"],
            }
            for order in st.session_state.orders
        ]
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
    st.subheader("Visual reports")
    delivered_value = sum(
        order["total"] for order in st.session_state.orders
        if order.get("status") == "Delivered"
    )
    cancelled_value = sum(
        order["total"] for order in st.session_state.orders
        if str(order.get("status", "")).lower() in {"cancelled", "refunded"}
    )
    pending_value = max(revenue - delivered_value - cancelled_value, 0)
    gain_loss_data = pd.DataFrame([
        {"Type": "Delivered gains", "Amount": delivered_value},
        {"Type": "Pending COD value", "Amount": pending_value},
        {"Type": "Cancelled/refunded losses", "Amount": cancelled_value},
    ])
    chart_columns = st.columns(2)
    with chart_columns[0]:
        st.markdown("**Gains, pending value, and recorded losses**")
        st.bar_chart(gain_loss_data.set_index("Type"), y="Amount", color="#2e7d32")
        st.caption("Losses include only orders explicitly marked Cancelled or Refunded; no operating costs are recorded.")
    with chart_columns[1]:
        status_data = pd.DataFrame([
            {"Status": status, "Orders": sum(
                order.get("status") == status for order in st.session_state.orders
            )}
            for status in ORDER_STATUSES + ["Cancelled", "Refunded"]
        ])
        status_data = status_data[status_data["Orders"] > 0]
        st.markdown("**Order status distribution**")
        if status_data.empty:
            st.info("No orders recorded yet.")
        else:
            st.bar_chart(status_data.set_index("Status"), y="Orders", color="#1565c0")
    farmer_interest = {}
    for order in st.session_state.orders:
        farmer = order.get("farmer") or order.get("farmer_id") or "Unknown farmer"
        farmer_interest.setdefault(farmer, {"orders": 0, "revenue": 0.0})
        farmer_interest[farmer]["orders"] += 1
        farmer_interest[farmer]["revenue"] += float(order.get("total", 0))
    if farmer_interest:
        interest_data = pd.DataFrame([
            {"Farmer": farmer, "Orders": values["orders"], "Revenue": values["revenue"]}
            for farmer, values in farmer_interest.items()
        ]).sort_values("Orders", ascending=False)
        interest_columns = st.columns(2)
        with interest_columns[0]:
            st.markdown("**Customer interest by farmer**")
            interest_pie = px.pie(
                interest_data,
                names="Farmer",
                values="Orders",
                hole=0.35,
                title="Order share",
            )
            st.plotly_chart(interest_pie, use_container_width=True)
        with interest_columns[1]:
            st.markdown("**Farmer revenue comparison**")
            st.bar_chart(interest_data.set_index("Farmer"), y="Revenue", color="#ef6c00")
    else:
        st.info("Farmer interest charts will appear after the first customer order.")
    st.subheader("Registered accounts")
    account_rows = [
        {
            "Username": f"@{user['username']}",
            "Email": user["email"],
            "Role": user["role"],
            "FRS photo": "Saved" if user.get("frs_photo") else "Not required",
        }
        for user in st.session_state.users.values()
    ]
    st.dataframe(pd.DataFrame(account_rows), use_container_width=True, hide_index=True)
    st.subheader("Farmer FRS verification")
    farmer_users = [
        user for user in st.session_state.users.values() if user["role"] == "Farmer"
    ]
    st.subheader("Registered farmer details")
    farmer_rows = []
    for user in farmer_users:
        listings = [
            product for product in st.session_state.products
            if product.get("farmer_id") == user["email"]
        ]
        locations = sorted({
            product.get("farmer_location", "Location not provided")
            for product in listings
        })
        crops = sorted({
            product.get("crop_details", "Not provided")
            for product in listings
        })
        farmer_rows.append({
            "Farmer": f"@{user['username']}",
            "Email": user["email"],
            "FRS photo": "Saved" if user.get("frs_photo") else "Missing",
            "Face matching": "Enabled" if user.get("face_encoding") else "Unavailable",
            "FRS status": "Active" if user.get("frs_enabled", True) else "Disabled",
            "Listings": len(listings),
            "Farm locations": " | ".join(locations) if locations else "No listing yet",
            "Crop details": " | ".join(crops) if crops else "No crop details yet",
        })
    if farmer_rows:
        st.dataframe(pd.DataFrame(farmer_rows), use_container_width=True, hide_index=True)
        st.caption("This table updates whenever a farmer registers or publishes a new crop listing.")
    else:
        st.info("No farmer accounts have been registered yet.")
    if farmer_users:
        verification_rows = [
            {
                "Username": f"@{user['username']}",
                "Email": user["email"],
                "Profile photo": "Saved" if user.get("frs_photo") else "Missing",
                "Face matching": "Enabled" if user.get("face_encoding") else "Optional/unavailable",
                "FRS": "Active" if user.get("frs_enabled", True) else "Disabled",
                "Last daily verification": user.get("last_face_verification") or "Not verified today",
            }
            for user in farmer_users
        ]
        st.dataframe(pd.DataFrame(verification_rows), use_container_width=True, hide_index=True)
        selected_farmer = st.selectbox(
            "Farmer account to manage",
            [user["email"] for user in farmer_users],
            key="admin-farmer-account",
        )
        selected_profile = st.session_state.users[selected_farmer]
        admin_camera_photo = st.camera_input(
            "Admin FRS camera access: capture the selected farmer",
            help="Camera access is used only for this verification attempt.",
            key="admin-frs-camera",
        )
        if admin_camera_photo:
            if face_recognition is None:
                st.warning("Face matching is not installed in this deployment. Install the optional face-recognition package to verify camera captures.")
            elif not selected_profile.get("face_encoding"):
                st.error("This farmer has no enrolled face profile. Register the farmer with an FRS photo first.")
            elif verify_face(admin_camera_photo.getvalue(), selected_profile["face_encoding"]):
                selected_profile["last_face_verification"] = date.today().isoformat()
                save_database_user(selected_profile)
                save_cloud_snapshot()
                st.success(f"FRS camera verification completed for @{selected_profile['username']}.")
            else:
                st.error("FRS camera verification failed. Use one clear face and good lighting.")
        if st.button("Require verification again today", key="admin-reset-farmer-verification"):
            farmer = st.session_state.users[selected_farmer]
            farmer["last_face_verification"] = None
            save_database_user(farmer)
            save_cloud_snapshot()
            st.success(f"Daily verification reset for @{farmer['username']}.")
            st.rerun()
        frs_enabled = selected_profile.get("frs_enabled", True)
        if st.button(
            "Disable FRS for this farmer" if frs_enabled else "Activate FRS for this farmer",
            key="admin-toggle-farmer-frs",
        ):
            selected_profile["frs_enabled"] = not frs_enabled
            save_database_user(selected_profile)
            save_cloud_snapshot()
            st.success(
                f"FRS {'activated' if selected_profile['frs_enabled'] else 'disabled'} for @{selected_profile['username']}."
            )
            st.rerun()
    else:
        st.info("No farmer accounts have been registered yet.")
    st.subheader("Live farmer location map")
    map_rows = []
    seen_farmer_ids = set()
    for product in st.session_state.products:
        farmer_id = product.get("farmer_id")
        latitude = product.get("farmer_lat")
        longitude = product.get("farmer_lon")
        if farmer_id in seen_farmer_ids or latitude is None or longitude is None:
            continue
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            continue
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            seen_farmer_ids.add(farmer_id)
            map_rows.append({
                "lat": latitude,
                "lon": longitude,
                "farmer": product.get("farmer", farmer_id or "Farmer"),
                "location": product.get("farmer_location", "Location not provided"),
            })
    if map_rows:
        st.map(pd.DataFrame(map_rows), latitude="lat", longitude="lon", zoom=8, size=180)
        st.caption("Map markers use farmer coordinates saved with their latest crop listings and refresh with the dashboard.")
        st.dataframe(
            pd.DataFrame(map_rows)[["farmer", "location", "lat", "lon"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No valid farmer coordinates are available yet. Ask farmers to add latitude and longitude when publishing a listing.")
    st.subheader("Marketplace inventory")
    inventory = pd.DataFrame(st.session_state.products)
    for column, default in {"farmer_location": "Location not provided", "crop_details": ""}.items():
        if column not in inventory:
            inventory[column] = default
    st.dataframe(inventory[["name", "category", "farmer", "farmer_location", "crop_details", "price", "stock", "organic"]], use_container_width=True, hide_index=True)
    if st.session_state.products:
        selected_product = st.selectbox(
            "Product to moderate",
            [product["id"] for product in st.session_state.products],
            format_func=lambda product_id: next(
                product["name"] for product in st.session_state.products if product["id"] == product_id
            ),
            key="admin-product-moderation",
        )
        if st.button("Remove product from marketplace", key="admin-remove-product"):
            st.session_state.products = [
                product for product in st.session_state.products if product["id"] != selected_product
            ]
            st.session_state.cart.pop(selected_product, None)
            save_cloud_snapshot()
            st.success("Product removed from the customer marketplace.")
            st.rerun()
    if st.session_state.orders:
        st.subheader("Recent orders")
        order_data = [
            {
                "Order": o["id"], "Date": o["created"], "Total": money(o["total"]),
                "Status": o["status"], "Payment": o["payment"], "Farmer": o.get("farmer", "—"),
                "Location": o.get("farmer_location", "—"), "Distance (km)": o.get("distance_km", "—"),
                "ETA (min)": o.get("eta_minutes", "—"),
            }
            for o in st.session_state.orders
        ]
        st.dataframe(pd.DataFrame(order_data), use_container_width=True, hide_index=True)
        st.caption("Demo controls: advance an order status to preview fulfillment management.")
        selected = st.selectbox("Order", [o["id"] for o in st.session_state.orders])
        new_status = st.selectbox("Set status", ORDER_STATUSES + ["Cancelled"])
        if st.button("Update order status"):
            selected_order = next(order for order in st.session_state.orders if order["id"] == selected)
            selected_order["status"] = new_status
            if new_status == "Cancelled":
                selected_order["payment_status"] = "Not collected"
            save_cloud_snapshot()
            st.success(f"Order #{selected} updated to {new_status}.")


def main():
    seed_state()
    st.markdown(
        f"""<style>
        .stApp {{
            background-image: linear-gradient(rgba(248,252,246,.92), rgba(248,252,246,.96)), url('{FARM_BACKGROUND}');
            background-size: cover;
            background-attachment: fixed;
        }}
        [data-testid="stSidebar"] {{ background: rgba(238, 248, 235, .96); }}
        </style>""",
        unsafe_allow_html=True,
    )
    if not st.session_state.authenticated_user:
        authentication_view()
        return
    st.sidebar.title("AgriDirect")
    st.sidebar.caption("Farm fresh. Fairly traded. Directly delivered.")
    user = st.session_state.users[st.session_state.authenticated_user]
    # Load the latest shared users, listings, and orders before rendering any
    # authenticated dashboard, including newly registered customer sessions.
    sync_cloud_snapshot()
    user = st.session_state.users.get(st.session_state.authenticated_user)
    if not user:
        st.session_state.authenticated_user = None
        st.rerun()
    role = user["role"]
    st.sidebar.success(f"Signed in as @{user['username']}")
    ai_voice_mode(role)
    if not daily_farmer_verification():
        return
    if role == "Farmer":
        st.session_state.user_email = user["email"]
    else:
        st.session_state.user_email = user["email"]
    if role == "Customer":
        customer_view()
    elif role == "Farmer":
        farmer_view()
    else:
        admin_view()
    st.sidebar.divider()
    st.sidebar.caption("Accounts and marketplace data persist in SQLite; shared S3 sync is optional.")
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.authenticated_user = None
        st.rerun()
    if role == "Admin" and st.sidebar.button("Reset demo data"):
        clear_persisted_marketplace()
        for key in ["products", "cart", "orders", "next_product_id", "next_order_id"]:
            st.session_state.pop(key, None)
        st.rerun()


if __name__ == "__main__":
    main()
