"""AgriDirect: a self-contained Streamlit marketplace.

Streamlit provides the UI; SQLite supports local development and a private S3
snapshot provides durable shared state for the hosted marketplace.
"""

from datetime import datetime
from datetime import date
import base64
from contextlib import contextmanager
from email.message import EmailMessage
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import smtplib
import ssl
import sqlite3
import time
from email.utils import parseaddr
from urllib.parse import quote, quote_plus, urlsplit

import pandas as pd
import requests
import streamlit as st

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None
    BotoCoreError = ClientError = OSError
    BotoConfig = None

dlib_runtime = None
face_detector = None
landmark_predictor = None
face_encoder = None
np = None
_face_runtime_loaded = False

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
LEGACY_DEMO_EMAILS = {
    "customer@agridirect.local",
    "farmer@agridirect.local",
    "orchard@agridirect.local",
    "admin@agridirect.local",
}
LEGACY_DEMO_FARMER_EMAILS = {
    "farmer@agridirect.local",
    "orchard@agridirect.local",
}
EMAIL_PATTERN = re.compile(r"^[^@\s\r\n]+@[^@\s\r\n]+\.[^@\s\r\n]+$")
FARM_BACKGROUND = "https://images.unsplash.com/photo-1500382017468-9049fed747ef?auto=format&fit=crop&w=2200&q=85"
IMAGE_TYPES = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"]
DATABASE_PATH = os.getenv("AGRIDIRECT_DATABASE", "agridirect_users.db")


@contextmanager
def database_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


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
                last_face_verification TEXT,
                last_face_capture BLOB
            )
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        if "last_face_capture" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN last_face_capture BLOB")
        connection.execute(
            """
            UPDATE users
            SET face_encoding = NULL,
                last_face_capture = NULL,
                last_face_verification = NULL,
                frs_photo = CASE
                    WHEN frs_photo_name = 'first-login-face-capture' THEN NULL
                    ELSE frs_photo
                END,
                frs_photo_name = CASE
                    WHEN frs_photo_name = 'first-login-face-capture' THEN NULL
                    ELSE frs_photo_name
                END
            WHERE role = 'Customer'
            """
        )
        demo_emails = tuple(sorted(LEGACY_DEMO_EMAILS))
        connection.execute(
            f"DELETE FROM users WHERE email IN ({','.join('?' for _ in demo_emails)})",
            demo_emails,
        )
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
            "last_face_capture": row["last_face_capture"],
        }
    return users


def save_database_user(user):
    try:
        initialize_database()
        with database_connection() as connection:
            connection.execute(
            """
            INSERT INTO users
                (email, username, role, password_hash, frs_photo, frs_photo_name, face_encoding, last_face_verification, last_face_capture)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                username=excluded.username,
                role=excluded.role,
                password_hash=excluded.password_hash,
                frs_photo=excluded.frs_photo,
                frs_photo_name=excluded.frs_photo_name,
                face_encoding=excluded.face_encoding,
                last_face_verification=excluded.last_face_verification,
                last_face_capture=excluded.last_face_capture
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
                    user.get("last_face_capture"),
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
    else:
        for email in admin_emails:
            users.pop(email, None)
            delete_database_user(email)
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


def merge_persistent_users(users):
    """Use shared snapshots as canonical whenever the app has configured storage."""
    for user in users.values():
        if user.get("role") == "Customer":
            user["face_encoding"] = None
            user["last_face_capture"] = None
            user["last_face_verification"] = None
            if user.get("frs_photo_name") == "first-login-face-capture":
                user["frs_photo"] = None
                user["frs_photo_name"] = None
    if hosted_streamlit_deployment() or storage_config()[0]:
        return users
    database_users = load_database_users()
    for email, user in database_users.items():
        users[email] = user
    return users


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
    try:
        salt, expected = stored_hash.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
        return secrets.compare_digest(actual, expected)
    except (AttributeError, TypeError, ValueError):
        return False


def find_account(identity):
    login_value = (identity or "").strip().lower()
    user = st.session_state.users.get(login_value)
    if user:
        return user
    return next(
        (
            candidate for candidate in st.session_state.users.values()
            if candidate.get("username", "").strip().lower() == login_value
        ),
        None,
    )


def continue_after_password(user):
    st.session_state.pop("pending_face_login", None)
    if user["role"] in {"Farmer", "Admin"}:
        st.session_state.authenticated_user = None
        st.session_state.pending_face_login = user["email"]
        return
    st.session_state.authenticated_user = user["email"]
    st.session_state.role = user["role"]


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
    if (
        not all(values.values())
        or not EMAIL_PATTERN.fullmatch(values["email"].strip())
        or not values["username"].strip()
        or len(values["password"]) < 12
    ):
        return None
    return {
        "email": values["email"].strip().lower(),
        "username": values["username"].strip().lower(),
        "role": "Admin",
        "password": values["password"],
    }


def email_transport_config():
    values = {
        "host": os.getenv("SMTP_HOST"),
        "port": os.getenv("SMTP_PORT", "587"),
        "username": os.getenv("SMTP_USERNAME"),
        "password": os.getenv("SMTP_PASSWORD"),
        "from_address": os.getenv("SMTP_FROM"),
    }
    try:
        section = st.secrets.get("email", {})
        if hasattr(section, "get"):
            for key in values:
                values[key] = section.get(key) or values[key]
    except (FileNotFoundError, KeyError, TypeError):
        pass
    if not all(values[key] for key in ("host", "username", "password", "from_address")):
        return None
    try:
        port = int(values["port"])
    except (TypeError, ValueError):
        return None
    sender_email = parseaddr(values["from_address"])[1]
    if not 1 <= port <= 65535 or not EMAIL_PATTERN.fullmatch(sender_email):
        return None
    return {**values, "port": port}


def send_security_code(address, subject, purpose):
    config = email_transport_config()
    if not config:
        return None
    code = f"{secrets.randbelow(1_000_000):06d}"
    message = EmailMessage()
    message["From"] = config["from_address"]
    message["To"] = address
    message["Subject"] = subject
    message.set_content(
        f"Your AgriDirect verification code for {purpose} is {code}.\n\n"
        "This code expires in 10 minutes. If you did not request it, ignore this message."
    )
    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=15) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(config["username"], config["password"])
            server.send_message(message)
    except (OSError, smtplib.SMTPException):
        return None
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def start_verification(email, subject, purpose):
    code_hash = send_security_code(email, subject, purpose)
    if not code_hash:
        return False
    st.session_state.pending_email_verification = {
        "email": email,
        "purpose": purpose,
        "code_hash": code_hash,
        "expires_at": time.time() + 600,
        "attempts": 0,
    }
    return True


def check_verification(email, purpose, code):
    pending = st.session_state.get("pending_email_verification")
    if not pending or pending.get("email") != email or pending.get("purpose") != purpose:
        return False, "Request a new verification code."
    if time.time() > pending["expires_at"]:
        st.session_state.pop("pending_email_verification", None)
        return False, "The verification code expired. Request a new one."
    pending["attempts"] += 1
    if pending["attempts"] > 5:
        st.session_state.pop("pending_email_verification", None)
        return False, "Too many incorrect attempts. Request a new code."
    actual_hash = hashlib.sha256((code or "").strip().encode("ascii", errors="ignore")).hexdigest()
    if not hmac.compare_digest(actual_hash, pending["code_hash"]):
        return False, "The verification code is incorrect."
    st.session_state.pop("pending_email_verification", None)
    return True, ""


def hosted_streamlit_deployment():
    try:
        headers = st.context.headers
        host = (
            headers.get("x-forwarded-host")
            or headers.get("host")
            or ""
        ).split(":", 1)[0].lower()
    except (AttributeError, KeyError, TypeError):
        host = ""
    return host.endswith(".streamlit.app") or host.endswith(".streamlit.io")


def durable_storage_configured():
    if supabase_storage_config():
        return ensure_supabase_bucket()
    bucket, _, _, _, _ = storage_config()
    return bool(bucket and boto3 is not None)


def durable_storage_setup_message():
    error = st.session_state.get("storage_error")
    if error:
        return (
            f"Durable storage check failed: {error} "
            "In Streamlit app settings, verify SUPABASE_URL and "
            "SUPABASE_SERVICE_ROLE_KEY. No SQL setup is required."
        )
    return (
        "Hosted signup is paused because durable storage is not configured. "
        "In Streamlit app settings → Secrets, add SUPABASE_URL and "
        "SUPABASE_SERVICE_ROLE_KEY (a private server-side key, not a publishable key). "
        "The app creates its private Supabase Storage bucket automatically; no SQL setup is required."
    )


class SupabaseStorageError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def supabase_storage_config():
    values = {
        "url": os.getenv("SUPABASE_URL"),
        "key": os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        "bucket": os.getenv("SUPABASE_STORAGE_BUCKET"),
    }
    try:
        for key, secret_name in (
            ("url", "SUPABASE_URL"),
            ("key", "SUPABASE_SERVICE_ROLE_KEY"),
            ("bucket", "SUPABASE_STORAGE_BUCKET"),
        ):
            values[key] = values[key] or st.secrets.get(secret_name)
    except (FileNotFoundError, KeyError, AttributeError, TypeError):
        pass
    url = (values["url"] or "").strip().rstrip("/")
    key = (values["key"] or "").strip()
    bucket = (values["bucket"] or "agridirect-private").strip()
    parsed_url = urlsplit(url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.netloc
        or parsed_url.path not in ("", "/")
        or parsed_url.query
        or parsed_url.fragment
        or parsed_url.username
        or parsed_url.password
        or not key
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", bucket)
    ):
        return None
    return {"url": url, "key": key, "bucket": bucket}


def supabase_storage_request(
    method, path, *, json_body=None, data=None, extra_headers=None
):
    config = supabase_storage_config()
    if not config:
        raise SupabaseStorageError("Supabase Storage is not configured.")
    headers = {
        "apikey": config["key"],
        "Authorization": f"Bearer {config['key']}",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    try:
        response = requests.request(
            method,
            f"{config['url']}/storage/v1/{path.lstrip('/')}",
            headers=headers,
            json=json_body,
            data=data,
            timeout=(3, 5),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise SupabaseStorageError(
            "Could not connect to Supabase Storage. Check the project URL and network."
        ) from exc
    if not 200 <= response.status_code < 300:
        raise SupabaseStorageError(
            f"Supabase Storage returned HTTP {response.status_code}. "
            "Check the service-role secret and private bucket permissions.",
            response.status_code,
        )
    return response


def ensure_supabase_bucket():
    config = supabase_storage_config()
    if not config:
        return False
    ready_key = f"_supabase_bucket_ready:{config['url']}:{config['bucket']}"
    if st.session_state.get(ready_key):
        return True
    encoded_bucket = quote(config["bucket"], safe="")
    try:
        try:
            bucket_response = supabase_storage_request(
                "GET", f"bucket/{encoded_bucket}"
            )
        except SupabaseStorageError as exc:
            if exc.status_code != 404:
                raise
            try:
                supabase_storage_request(
                    "POST",
                    "bucket",
                    json_body={
                        "id": config["bucket"],
                        "name": config["bucket"],
                        "public": False,
                    },
                )
            except SupabaseStorageError as create_error:
                if create_error.status_code != 409:
                    raise
            bucket_response = supabase_storage_request(
                "GET", f"bucket/{encoded_bucket}"
            )
        try:
            bucket_details = bucket_response.json()
        except requests.JSONDecodeError as exc:
            raise SupabaseStorageError(
                "Supabase Storage returned invalid bucket metadata."
            ) from exc
        if not isinstance(bucket_details, dict):
            raise SupabaseStorageError(
                "Supabase Storage returned invalid bucket metadata."
            )
        if bucket_details.get("public") is not False:
            raise SupabaseStorageError(
                "Could not confirm the Supabase bucket is private. Set it to private before storing marketplace accounts or orders."
            )
    except SupabaseStorageError as exc:
        st.session_state["storage_error"] = str(exc)
        return False
    st.session_state.pop("storage_error", None)
    st.session_state[ready_key] = True
    return True


def load_persistent_snapshot():
    snapshot = load_cloud_snapshot()
    if snapshot is not None or hosted_streamlit_deployment() or storage_config()[0]:
        return snapshot
    return load_local_snapshot()


def _encode_bytes(value):
    return base64.b64encode(value).decode("ascii") if isinstance(value, bytes) else value


def _decode_bytes(value):
    try:
        return base64.b64decode(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return None


def remove_legacy_demo_state(snapshot):
    if not isinstance(snapshot, dict):
        return snapshot, False
    sanitized = dict(snapshot)
    users = sanitized.get("users", {})
    products = sanitized.get("products", [])
    orders = sanitized.get("orders", [])
    clean_users = {
        email: user
        for email, user in users.items()
        if email not in LEGACY_DEMO_EMAILS
    } if isinstance(users, dict) else users
    clean_products = [
        product for product in products
        if not isinstance(product, dict)
        or product.get("farmer_id") not in LEGACY_DEMO_FARMER_EMAILS
    ] if isinstance(products, list) else products
    clean_orders = [
        order for order in orders
        if not isinstance(order, dict)
        or (
            order.get("owner_email") not in LEGACY_DEMO_EMAILS
            and order.get("farmer_id") not in LEGACY_DEMO_FARMER_EMAILS
            and not any(
                isinstance(item, dict)
                and item.get("farmer_id") in LEGACY_DEMO_FARMER_EMAILS
                for item in (
                    order.get("items")
                    if isinstance(order.get("items"), list)
                    else []
                )
            )
        )
    ] if isinstance(orders, list) else orders
    changed = (
        clean_users != users
        or clean_products != products
        or clean_orders != orders
    )
    sanitized.update(users=clean_users, products=clean_products, orders=clean_orders)
    return sanitized, changed


def load_cloud_snapshot():
    """Restore marketplace state from configured Supabase Storage or S3."""
    supabase_config = supabase_storage_config()
    if supabase_config:
        if not ensure_supabase_bucket():
            return None
        bucket = quote(supabase_config["bucket"], safe="")
        try:
            response = supabase_storage_request(
                "GET", f"object/{bucket}/state.json"
            )
        except SupabaseStorageError as exc:
            if exc.status_code == 404:
                st.session_state.pop("storage_error", None)
                return None
            st.session_state["storage_error"] = str(exc)
            return None
        st.session_state.pop("storage_error", None)
        try:
            return response.json()
        except requests.JSONDecodeError as exc:
            st.session_state["storage_error"] = (
                "Supabase Storage returned invalid marketplace JSON."
            )
            return None

    bucket, region, access_key, secret_key, session_token = storage_config()
    if not bucket or boto3 is None:
        st.session_state["cloud_snapshot_etag"] = None
        return None
    try:
        response = storage_client(region, access_key, secret_key, session_token).get_object(
            Bucket=bucket, Key="agridirect/state.json"
        )
        st.session_state["cloud_snapshot_etag"] = response.get("ETag")
        return json.loads(response["Body"].read().decode("utf-8"))
    except (BotoCoreError, ClientError, OSError, ValueError, KeyError, json.JSONDecodeError):
        st.session_state["cloud_snapshot_etag"] = None
        return None


def normalize_product(product):
    """Fill fields absent from older persisted marketplace snapshots."""
    normalized = dict(product)
    defaults = {
        "id": 0,
        "name": "Unavailable product",
        "category": "Other",
        "price": 0,
        "unit": "unit",
        "stock": 0,
        "farmer": "Unknown farmer",
        "farmer_id": None,
        "farmer_location": "Location not provided",
        "farmer_lat": None,
        "farmer_lon": None,
        "crop_details": normalized.get("description", ""),
        "organic": False,
        "description": "",
        "emoji": "🌱",
        "image_url": "",
    }
    for key, value in defaults.items():
        if normalized.get(key) is None:
            normalized[key] = value
    if not normalized["name"]:
        normalized["name"] = "Unavailable product"
    if not normalized["category"]:
        normalized["category"] = "Other"
    try:
        normalized["price"] = float(normalized["price"])
        normalized["stock"] = max(0, int(normalized["stock"]))
    except (TypeError, ValueError):
        normalized["price"] = 0
        normalized["stock"] = 0
    if not math.isfinite(normalized["price"]) or normalized["price"] <= 0 or not normalized["farmer_id"]:
        normalized["stock"] = 0
    normalized["image_bytes"] = _decode_bytes(normalized.get("image_bytes"))
    return normalized


def normalize_order(order, index):
    """Fill display fields absent from older persisted order snapshots."""
    normalized = dict(order)
    normalized.setdefault("id", 1001 + index)
    normalized.setdefault("created", normalized.get("created_iso", "Date not recorded"))
    normalized.setdefault("address", "Address not recorded")
    normalized.setdefault("status", "Placed")
    normalized.setdefault("payment", "Cash on Delivery")
    normalized.setdefault(
        "order_reference", normalized.get("transaction_id", "Reference not available")
    )
    try:
        normalized["total"] = float(normalized.get("total", 0))
    except (TypeError, ValueError):
        normalized["total"] = 0
    items = normalized.get("items", [])
    normalized["items"] = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    return normalized


def face_encoding(image_bytes):
    if not load_face_runtime():
        return None
    try:
        from PIL import Image

        image = np.asarray(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        faces = face_detector(image, 1)
        if not faces:
            return None
        landmarks = landmark_predictor(image, faces[0])
        return list(face_encoder.compute_face_descriptor(image, landmarks, 1))
    except (OSError, ValueError, RuntimeError):
        return None


def load_face_runtime():
    global dlib_runtime, face_detector, landmark_predictor, face_encoder, np, _face_runtime_loaded
    if dlib_runtime is not None and np is not None:
        return True
    if _face_runtime_loaded:
        return False
    _face_runtime_loaded = True
    try:
        import dlib as dlib_module
        import face_recognition_models
        import numpy as numpy_runtime
        detector = dlib_module.get_frontal_face_detector()
        predictor = dlib_module.shape_predictor(
            face_recognition_models.pose_predictor_five_point_model_location()
        )
        encoder = dlib_module.face_recognition_model_v1(
            face_recognition_models.face_recognition_model_location()
        )
    except (ImportError, OSError, RuntimeError):
        dlib_runtime = None
        np = None
        return False
    dlib_runtime = dlib_module
    face_detector = detector
    landmark_predictor = predictor
    face_encoder = encoder
    np = numpy_runtime
    return True


def verify_face(image_bytes, reference):
    if not load_face_runtime() or not reference:
        return False
    try:
        candidate = face_encoding(image_bytes)
        return bool(
            candidate
            and np.linalg.norm(np.asarray(reference) - np.asarray(candidate)) <= 0.48
        )
    except (OSError, ValueError, RuntimeError):
        return False


def persist_login_face(user, image_bytes, enroll=False):
    if not load_face_runtime():
        return False, "Face matching is unavailable. Login is blocked until the FRS runtime is installed."
    if enroll:
        encoding = face_encoding(image_bytes)
        if not encoding:
            return False, "No face was detected. Allow camera access and capture one clear, front-facing face."
    elif not verify_face(image_bytes, user.get("face_encoding")):
        return False, "Face mismatch. This account cannot be opened with a different face."

    previous = {
        key: user.get(key)
        for key in ("face_encoding", "frs_photo", "frs_photo_name", "last_face_capture", "last_face_verification")
    }
    if enroll:
        user["face_encoding"] = encoding
        if not user.get("frs_photo"):
            user["frs_photo"] = image_bytes
            user["frs_photo_name"] = "first-login-face-capture"
    user["last_face_capture"] = image_bytes
    user["last_face_verification"] = date.today().isoformat()
    if not save_database_user(user):
        user.update(previous)
        return False, "Face enrollment could not be saved to the account database."
    snapshot_saved = save_cloud_snapshot()
    if not snapshot_saved:
        user.update(previous)
        save_database_user(user)
        return False, "Face enrollment could not be saved to durable shared storage. Configure Supabase Storage or S3 before signing in."
    return True, "Face enrolled and saved." if enroll else "Face matched. Sign-in complete."


def storage_config():
    bucket = os.getenv("AGRI_S3_BUCKET")
    region = os.getenv("AWS_REGION")
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")
    try:
        bucket = bucket or st.secrets.get("AGRI_S3_BUCKET")
        region = region or st.secrets.get("AWS_REGION")
        access_key = access_key or st.secrets.get("AWS_ACCESS_KEY_ID")
        secret_key = secret_key or st.secrets.get("AWS_SECRET_ACCESS_KEY")
        session_token = session_token or st.secrets.get("AWS_SESSION_TOKEN")
    except (FileNotFoundError, KeyError, AttributeError, TypeError):
        pass
    return bucket, region, access_key, secret_key, session_token


def storage_client(region, access_key, secret_key, session_token):
    if boto3 is None:
        return None
    client_options = {}
    if BotoConfig is not None:
        client_options["config"] = BotoConfig(
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 2, "mode": "standard"},
        )
    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        aws_session_token=session_token or None,
        **client_options,
    )


def save_cloud_snapshot():
    """Persist a complete snapshot locally and to the configured hosted object store."""
    payload = {
        "products": [{**product, "image_bytes": _encode_bytes(product.get("image_bytes"))} for product in st.session_state.products],
        "users": {
            email: {
                **user,
                "frs_photo": _encode_bytes(user.get("frs_photo")),
                "last_face_capture": _encode_bytes(user.get("last_face_capture")),
            }
            for email, user in st.session_state.users.items()
        },
        "orders": st.session_state.orders,
    }
    hosted = hosted_streamlit_deployment()
    supabase_config = supabase_storage_config()
    if supabase_config:
        if not ensure_supabase_bucket():
            return False
        encoded_bucket = quote(supabase_config["bucket"], safe="")
        try:
            supabase_storage_request(
                "POST",
                f"object/{encoded_bucket}/state.json",
                data=json.dumps(payload, default=str).encode(),
                extra_headers={
                    "Content-Type": "application/json",
                    "x-upsert": "true",
                },
            )
        except SupabaseStorageError as exc:
            st.session_state["storage_error"] = str(exc)
            return False
        st.session_state.pop("storage_error", None)
        save_local_snapshot(payload)
        return True

    bucket, region, access_key, secret_key, session_token = storage_config()
    if not bucket or boto3 is None:
        if hosted or bucket:
            return False
        return save_local_snapshot(payload)
    try:
        expected_etag = st.session_state.get("cloud_snapshot_etag")
        conditional_write = (
            {"IfMatch": expected_etag}
            if expected_etag
            else {"IfNoneMatch": "*"}
        )
        response = storage_client(region, access_key, secret_key, session_token).put_object(
            Bucket=bucket,
            Key="agridirect/state.json",
            Body=json.dumps(payload, default=str).encode(),
            ContentType="application/json",
            ServerSideEncryption="AES256",
            **conditional_write,
        )
    except (BotoCoreError, ClientError, OSError, ValueError):
        if hosted or bucket:
            return False
        return save_local_snapshot(payload)
    st.session_state["cloud_snapshot_etag"] = response.get("ETag")
    save_local_snapshot(payload)
    return True


def clear_persisted_marketplace():
    try:
        initialize_database()
        with database_connection() as connection:
            connection.execute("DELETE FROM marketplace_state WHERE state_key = 'marketplace'")
            connection.commit()
    except sqlite3.Error:
        pass
    supabase_config = supabase_storage_config()
    if supabase_config:
        try:
            encoded_bucket = quote(supabase_config["bucket"], safe="")
            supabase_storage_request(
                "DELETE",
                f"object/{encoded_bucket}",
                json_body={"prefixes": ["state.json"]},
            )
        except SupabaseStorageError as exc:
            st.session_state["storage_error"] = str(exc)
        return

    bucket, region, access_key, secret_key, session_token = storage_config()
    if bucket and boto3 is not None:
        try:
            storage_client(region, access_key, secret_key, session_token).delete_object(
                Bucket=bucket,
                Key="agridirect/state.json",
            )
        except (BotoCoreError, ClientError, OSError):
            pass


def sync_cloud_snapshot():
    """Refresh shared marketplace data without disturbing the signed-in session."""
    snapshot = load_persistent_snapshot()
    if not snapshot:
        return False
    snapshot, _ = remove_legacy_demo_state(snapshot)
    if snapshot.get("products") is not None:
        st.session_state.products = [
            normalize_product(product)
            for product in snapshot["products"]
            if isinstance(product, dict)
        ]
    if snapshot.get("users") is not None:
        current_email = st.session_state.get("authenticated_user")
        st.session_state.users = merge_persistent_users(dict(snapshot["users"]))
        for user in st.session_state.users.values():
            user["frs_photo"] = _decode_bytes(user.get("frs_photo"))
            user["last_face_capture"] = _decode_bytes(user.get("last_face_capture"))
        st.session_state.users = enforce_single_admin(
            st.session_state.users, configured_admin_account()
        )
        if current_email and current_email not in st.session_state.users:
            st.session_state.authenticated_user = None
    if snapshot.get("orders") is not None:
        st.session_state.orders = [
            normalize_order(order, index)
            for index, order in enumerate(snapshot["orders"])
            if isinstance(order, dict)
        ]
    st.session_state.next_product_id = max(
        (product.get("id", 0) for product in st.session_state.products), default=0
    ) + 1
    st.session_state.next_order_id = max(
        (order.get("id", 1000) for order in st.session_state.orders), default=1000
    ) + 1
    return True


def seed_state():
    """Create a fresh in-memory marketplace for the current browser session."""
    if st.session_state.get("_marketplace_initialized"):
        return False
    snapshot = load_persistent_snapshot()
    snapshot, migrated_legacy_data = remove_legacy_demo_state(snapshot)
    if "products" not in st.session_state:
        st.session_state.products = (
            snapshot["products"] if snapshot and "products" in snapshot else []
        )
        st.session_state.products = [
            normalize_product(product)
            for product in st.session_state.products
            if isinstance(product, dict)
        ]
    st.session_state.setdefault("cart", {})
    st.session_state.setdefault("orders", (snapshot or {}).get("orders", []))
    st.session_state.orders = [
        normalize_order(order, index)
        for index, order in enumerate(st.session_state.orders)
        if isinstance(order, dict)
    ]
    st.session_state.setdefault(
        "next_product_id",
        max((product.get("id", 0) for product in st.session_state.products), default=6) + 1,
    )
    st.session_state.setdefault(
        "next_order_id",
        max((order.get("id", 1000) for order in st.session_state.orders), default=1000) + 1,
    )
    if "users" not in st.session_state:
        st.session_state.users = merge_persistent_users({})
        if snapshot and snapshot.get("users"):
            st.session_state.users.update(snapshot["users"])
            for user in st.session_state.users.values():
                user["frs_photo"] = _decode_bytes(user.get("frs_photo"))
                user["last_face_capture"] = _decode_bytes(user.get("last_face_capture"))
            st.session_state.users = merge_persistent_users(st.session_state.users)
        for user in st.session_state.users.values():
            save_database_user(user)
    session_state, migrated_session_data = remove_legacy_demo_state({
        "users": st.session_state.users,
        "products": st.session_state.products,
        "orders": st.session_state.orders,
    })
    st.session_state.users = session_state["users"]
    st.session_state.products = session_state["products"]
    st.session_state.orders = session_state["orders"]
    for user in st.session_state.users.values():
        if user.get("role") == "Customer":
            if any(
                user.get(field)
                for field in (
                    "face_encoding",
                    "last_face_capture",
                    "last_face_verification",
                )
            ):
                migrated_session_data = True
            user["face_encoding"] = None
            user["last_face_capture"] = None
            user["last_face_verification"] = None
            if user.get("frs_photo_name") == "first-login-face-capture":
                user["frs_photo"] = None
                user["frs_photo_name"] = None
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
        }
        st.session_state.users[admin["email"]] = account
        save_database_user(account)
    else:
        for user in st.session_state.users.values():
            if user.get("role") == "Admin":
                save_database_user(user)
    if st.session_state.get("authenticated_user") not in st.session_state.users:
        st.session_state.authenticated_user = None
        st.session_state.pop("pending_face_login", None)
    st.session_state.setdefault("authenticated_user", None)
    if migrated_legacy_data or migrated_session_data:
        save_cloud_snapshot()
    st.session_state["_marketplace_initialized"] = True
    return True


def registration_view():
    st.subheader("Create your AgriDirect account")
    st.caption("Create an account and sign in immediately. Email verification is not required.")
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
        if not EMAIL_PATTERN.fullmatch(normalized_email) or not new_password or not username:
            st.error("Enter a valid email, username, and password.")
        elif len(new_password) < 12:
            st.error("Password must be at least 12 characters.")
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
            if account_role == "Farmer" and dlib_runtime is not None and not enrolled_encoding:
                st.error("No clear face was found in that photo. Upload one clear, front-facing farmer photo.")
                return
            account = {
                "email": normalized_email, "role": account_role, "username": username,
                "password": password_hash(new_password),
                "frs_photo": farmer_photo.getvalue() if farmer_photo else None,
                "frs_photo_name": farmer_photo.name if farmer_photo else None,
                "face_encoding": enrolled_encoding,
            }
            if hosted_streamlit_deployment() and not durable_storage_configured():
                st.error(durable_storage_setup_message())
            elif not save_database_user(account):
                st.error("Your account could not be saved. Check the database location and try again.")
            else:
                st.session_state.users[normalized_email] = account
                if not save_cloud_snapshot():
                    st.session_state.users.pop(normalized_email, None)
                    delete_database_user(normalized_email)
                    st.error("Account creation failed because durable marketplace storage is unavailable. No account was created.")
                else:
                    st.session_state.pop("show_create_account", None)
                    continue_after_password(account)
                    st.success("Account created. Email verification is not required.")
                    st.rerun()
    if st.button("Back to sign in", key="back-to-signin"):
        st.session_state.pop("show_create_account", None)
        st.rerun()


def password_reset_view():
    st.subheader("Reset your password")
    st.caption("A one-time code is sent to your registered email. Admin credentials are managed in deployment secrets.")
    pending = st.session_state.get("pending_email_verification")
    if pending and pending.get("purpose") == "password_reset":
        email = pending["email"]
        with st.form("password-reset-form"):
            code = st.text_input("Email verification code", max_chars=6)
            new_password = st.text_input("New password", type="password")
            confirm_password = st.text_input("Confirm new password", type="password")
            reset_submitted = st.form_submit_button("Verify and save password", type="primary")
        if reset_submitted:
            valid, message = check_verification(email, "password_reset", code)
            user = st.session_state.users.get(email)
            if not valid:
                st.error(message)
            elif not user or user["role"] == "Admin":
                st.session_state.pop("pending_email_verification", None)
                st.error("Admin passwords must be changed through the protected deployment secrets.")
            elif len(new_password) < 12:
                st.error("Password must be at least 12 characters.")
            elif new_password != confirm_password:
                st.error("Passwords do not match.")
            elif hosted_streamlit_deployment() and not durable_storage_configured():
                st.error("Password reset is unavailable until durable storage is configured.")
            else:
                previous_password = user["password"]
                user["password"] = password_hash(new_password)
                if save_database_user(user) and save_cloud_snapshot():
                    st.session_state.pop("show_password_reset", None)
                    st.success("Your password was reset successfully. Sign in with the new password.")
                    st.rerun()
                else:
                    user["password"] = previous_password
                    save_database_user(user)
                    st.error("The password could not be saved to durable storage.")
        if st.button("Resend password code", key="resend-password-code"):
            if start_verification(email, "AgriDirect password reset", "password_reset"):
                st.success("A new verification code was sent.")
            else:
                st.error("Email delivery is unavailable. Check the SMTP settings in Streamlit secrets.")
    else:
        with st.form("password-reset-request-form"):
            email = st.text_input("Registered email address", key="reset-email")
            request_code = st.form_submit_button("Send verification code", type="primary")
        if request_code:
            normalized_email = email.strip().lower()
            user = st.session_state.users.get(normalized_email)
            if user and user["role"] != "Admin" and email_transport_config():
                if not start_verification(normalized_email, "AgriDirect password reset", "password_reset"):
                    st.error("The verification email could not be sent. Check SMTP settings and try again.")
                else:
                    st.success("If that address belongs to an account, a verification code was sent.")
                    st.rerun()
            elif not email_transport_config():
                st.error("Password recovery requires verified-email delivery. Configure the SMTP settings in Streamlit secrets first.")
            else:
                st.success("If that address belongs to an account, a verification code was sent.")
    if st.button("Back to sign in", key="back-from-password-reset"):
        st.session_state.pop("show_password_reset", None)
        st.rerun()


def authentication_view():
    st.title("🌱 Welcome to AgriDirect")
    st.success("Sign in to continue to AgriDirect.")
    st.write("Use your registered email or username and password to shop, manage listings, or review marketplace operations.")
    if not configured_admin_account():
        st.warning(
            "Administrator setup is incomplete. Configure a unique admin email, username, "
            "and password of at least 12 characters in Streamlit secrets before operating the marketplace."
        )
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
        if pending_user["role"] == "Customer":
            continue_after_password(pending_user)
            st.rerun()
            return
        enrolling_face = not pending_user.get("face_encoding")
        st.subheader("Save your face to finish sign-in" if enrolling_face else "Face verification required")
        if enrolling_face:
            st.info("Password accepted. Capture your face once to securely enroll it; later logins must match this face.")
        else:
            st.info("Password accepted. Capture the saved account face to finish signing in.")
        if not load_face_runtime():
            st.error("Face matching is unavailable on this deployment. Login is blocked until the native face-recognition runtime is installed.")
            if st.button("Cancel face verification", key="cancel-face-login"):
                st.session_state.pop("pending_face_login", None)
                st.rerun()
            return
        camera_permission_guidance()
        face_capture = st.camera_input(
            "Capture your face to enroll" if enrolling_face else "Capture the enrolled account face",
            help="Allow browser camera permission and position one face clearly in the frame.",
            key="login-face-capture",
        )
        if face_capture:
            matched, message = persist_login_face(
                pending_user, face_capture.getvalue(), enroll=enrolling_face
            )
            if matched:
                st.session_state.pop("pending_face_login", None)
                st.session_state.authenticated_user = pending_user["email"]
                st.session_state.role = pending_user["role"]
                st.success(message)
                st.rerun()
            else:
                st.error(message)
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
            user = find_account(email)
            if user and password_matches(password, user["password"]):
                continue_after_password(user)
                st.rerun()
            else:
                st.error("Invalid email or password.")
        if st.button("Create account", use_container_width=True, key="create-account-below-signin"):
            st.session_state["show_create_account"] = True
            st.rerun()
        if st.button("Forgot password?", use_container_width=True, key="forgot-password"):
            st.session_state["show_password_reset"] = True
            st.rerun()
    with register_tab:
        st.write("Create a new account without an email verification step.")
        if st.button("Start registration", key="start-registration"):
            st.session_state["show_create_account"] = True
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


def camera_permission_guidance():
    st.caption(
        "Allow camera access when your browser prompts; permission must be granted separately on each device. "
        "Camera capture requires HTTPS or localhost. If access was blocked, enable Camera for this site in "
        "the browser's site settings and reload; plain-HTTP LAN links may be blocked by browser security."
    )


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


@streamlit_fragment(run_every="5s")
def marketplace_browser():
    """Refresh shared listings every five seconds without interrupting cart or checkout."""
    synced = sync_cloud_snapshot()
    st.caption("Marketplace listings update automatically every five seconds.")
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
    if not st.session_state.products:
        st.info("No farmer listings are available yet. Verified farmers can create real listings from the Farmer workspace.")
    elif not filtered:
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
    order_reference = f"AGR-ORD-{secrets.token_hex(6).upper()}"
    order = {
        "id": st.session_state.next_order_id,
        "created": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "created_iso": datetime.now().isoformat(),
        "items": [
            {
                "product_id": p["id"],
                "name": p["name"],
                "quantity": q,
                "total": p["price"] * q,
                "farmer_id": p.get("farmer_id"),
                "farmer": p.get("farmer", "Farmer"),
                "farmer_location": product_location(p),
                "crop_details": p.get("crop_details", ""),
                "unit": p.get("unit", ""),
            }
            for p, q in purchased_rows
        ],
        "total": total,
        "address": f"{address}, {city} - {pincode}",
        "status": "Placed",
        "payment": "Cash on Delivery",
        "payment_status": "Pay at delivery",
        "order_reference": order_reference,
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
    previous_cart = dict(st.session_state.cart)
    st.session_state.cart.clear()
    if not save_cloud_snapshot():
        st.session_state.orders.remove(order)
        st.session_state.next_order_id -= 1
        for product, quantity in purchased_rows:
            product["stock"] += quantity
        st.session_state.cart.update(previous_cart)
        st.error("The order could not be saved to the database. No purchase was completed; please try again.")
        return
    st.success(f"Purchase completed successfully! Order #{order['id']} was created.")
    st.info(
        f"Order reference **{order_reference}** · Total **{money(total)}** · "
        f"Cash on Delivery is due to the farmer/operator at delivery."
    )
    st.balloons()


def items_for_farmer(order, farmer_id, products=None):
    items = order.get("items", [])
    product_by_id = {
        product.get("id"): product for product in (products or [])
    }
    owned_items = []
    ownership_known = False
    for item in items:
        item_farmer_id = item.get("farmer_id")
        if not item_farmer_id and item.get("product_id") in product_by_id:
            item_farmer_id = product_by_id[item["product_id"]].get("farmer_id")
        if item_farmer_id:
            ownership_known = True
            if item_farmer_id == farmer_id:
                owned_items.append(item)
    if owned_items:
        return owned_items
    if not ownership_known and order.get("farmer_id") == farmer_id:
        return items
    return []


@streamlit_fragment(run_every="5s")
def farmer_order_notifications(farmer_id):
    sync_cloud_snapshot()
    farmer_orders = [
        (order, items_for_farmer(order, farmer_id, st.session_state.products))
        for order in st.session_state.orders
    ]
    farmer_orders = [
        (order, items) for order, items in farmer_orders if items
    ]
    active_count = sum(
        order.get("status") in {"Placed", "Confirmed", "Preparing", "Out for delivery"}
        for order, _ in farmer_orders
    )
    farmer_orders = farmer_orders[:10]
    st.subheader(f"🔔 Customer orders ({active_count} active)")
    if not farmer_orders:
        st.info("New orders for your products will appear here automatically.")
        return
    for order, items in farmer_orders:
        details = ", ".join(
            f"{item.get('name', 'Product')} × {item.get('quantity', 0)} "
            f"{item.get('unit', '')} ({money(float(item.get('total', 0)))})"
            for item in items
        )
        amount = sum(float(item.get("total", 0)) for item in items)
        with st.container(border=True):
            st.markdown(f"**Order #{order.get('id')} · {order.get('status', 'Placed')}**")
            st.write(f"Items ordered: {details}")
            st.caption(
                f"Customer: {order.get('owner_email', '—')} · "
                f"Your items total: {money(amount)} · "
                f"{order.get('payment', 'Cash on Delivery')} · Placed: {order.get('created', '—')}"
            )
            st.caption(
                f"Deliver to: {order.get('address', '—')} · "
                f"Farm: {items[0].get('farmer_location', order.get('farmer_location', '—'))} · "
                f"ETA: {order.get('eta_minutes', '—')} min"
            )


@streamlit_fragment(run_every="5s")
def admin_order_notifications():
    sync_cloud_snapshot()
    orders = st.session_state.orders[:10]
    active_count = sum(
        order.get("status") in {"Placed", "Confirmed", "Preparing", "Out for delivery"}
        for order in st.session_state.orders
    )
    st.subheader(f"🔔 Order notifications ({active_count} active)")
    if not orders:
        st.info("Customer orders will appear here automatically.")
        return
    for order in orders:
        item_details = ", ".join(
            f"{item.get('name', 'Product')} × {item.get('quantity', 0)}"
            for item in order.get("items", [])
        )
        with st.container(border=True):
            st.markdown(
                f"**Order #{order.get('id')} · {order.get('status', 'Placed')} · "
                f"{money(float(order.get('total', 0)))}**"
            )
            st.write(f"Items: {item_details or order.get('listing', '—')}")
            st.caption(
                f"Customer: {order.get('owner_email', '—')} · "
                f"Farmer: {order.get('farmer', '—')} · "
                f"{order.get('payment', 'Cash on Delivery')} · {order.get('created', '—')}"
            )
            st.caption(
                "Farmer split: " + " | ".join(
                    f"{item.get('farmer', 'Farmer')}: {item.get('name', 'Product')} × "
                    f"{item.get('quantity', 0)} ({money(float(item.get('total', 0)))})"
                    for item in order.get("items", [])
                )
            )
            st.caption(f"Delivery address: {order.get('address', '—')}")


def cancel_order(order):
    if order.get("status") not in {"Placed", "Confirmed", "Preparing"}:
        return False
    restored = []
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
            restored.append((product, int(product.get("stock", 0))))
            product["stock"] = int(product.get("stock", 0)) + int(item.get("quantity", 0))
    previous_status = order.get("status")
    previous_payment_status = order.get("payment_status")
    previous_cancelled_at = order.get("cancelled_at")
    order["status"] = "Cancelled"
    order["payment_status"] = "Not collected"
    order["cancelled_at"] = datetime.now().isoformat()
    if save_cloud_snapshot():
        return True
    order["status"] = previous_status
    order["payment_status"] = previous_payment_status
    if previous_cancelled_at is None:
        order.pop("cancelled_at", None)
    else:
        order["cancelled_at"] = previous_cancelled_at
    for product, stock in restored:
        product["stock"] = stock
    return False


def render_orders():
    orders = [order for order in st.session_state.orders if order.get("owner_email") == st.session_state.authenticated_user]
    if not orders:
        st.info("Your placed orders will appear here.")
        return
    for order in orders:
        with st.container(border=True):
            columns = st.columns([2, 2, 1])
            columns[0].markdown(f"**Order #{order['id']}**\n\n{order['created']}")
            columns[1].write(
                "\n\n".join(
                    f"**{item.get('name', 'Product')} × {item.get('quantity', 0)} "
                    f"{item.get('unit', '')}** · {money(float(item.get('total', 0)))}\n\n"
                    f"Farmer: {item.get('farmer', order.get('farmer', '—'))} · "
                    f"{item.get('farmer_location', order.get('farmer_location', '—'))}"
                    for item in order.get("items", [])
                )
            )
            columns[2].metric(order["status"], money(order["total"]))
            st.caption(
                f"{order['payment']} ({order.get('payment_status', 'Recorded')}) · "
                f"Order reference: {order.get('order_reference', 'Not recorded')} · "
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
                    else:
                        st.error("Cancellation was not saved to durable storage. Your order and stock were left unchanged.")


def farmer_view():
    sync_cloud_snapshot()
    st.title("🚜 Farmer workspace")
    st.write("Manage your listings and see what customers are buying.")
    farmer_id = st.session_state.get("user_email", "")
    mine = [p for p in st.session_state.products if p["farmer_id"] == farmer_id]
    columns = st.columns(3)
    columns[0].metric("Your listings", len(mine))
    columns[1].metric("Available stock", sum(p["stock"] for p in mine))
    columns[2].metric("Marketplace status", "Active")
    farmer_order_notifications(farmer_id)
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
        st.error("Admin secrets are not configured. No fallback administrator account exists.")
    customers = sum(1 for user in st.session_state.users.values() if user["role"] == "Customer")
    farmers = len({p["farmer_id"] for p in st.session_state.products})
    gross_order_value = sum(
        order["total"] for order in st.session_state.orders
        if order.get("status") not in {"Cancelled", "Refunded"}
    )
    today = date.today().isoformat()
    daily_orders = [
        order for order in st.session_state.orders
        if order_date(order) == today and order.get("status") not in {"Cancelled", "Refunded"}
    ]
    daily_order_value = sum(order["total"] for order in daily_orders)
    collected_cod = sum(
        order["total"] for order in st.session_state.orders
        if order.get("payment_status") == "Collected"
    )
    pending_cod = sum(
        order["total"] for order in st.session_state.orders
        if order.get("payment_status") != "Collected"
        and order.get("status") not in {"Cancelled", "Refunded"}
    )
    columns = st.columns(7)
    columns[0].metric("Products", len(st.session_state.products))
    columns[1].metric("Farmers", farmers)
    columns[2].metric("Customers", customers)
    columns[3].metric("Orders", len(st.session_state.orders))
    columns[4].metric("Gross order value", money(gross_order_value), help="Value of non-cancelled orders; this is not collected revenue.")
    columns[5].metric("COD collected", money(collected_cod), help="Cash manually confirmed as received by an administrator.")
    columns[6].metric("COD due", money(pending_cod), help="Non-cancelled COD orders not manually confirmed as collected.")
    st.caption(
        f"Today's non-cancelled order value: **{money(daily_order_value)}** · "
        f"Recorded COD collections: **{money(collected_cod)}**. "
        "Order value is not payment; COD collection must be confirmed by an operator."
    )
    admin_order_notifications()
    report_tabs = st.tabs(
        ["Daily order value", "Order summary", "Full order history", "Marketplace data"]
    )
    with report_tabs[0]:
        st.metric("Today's order value", money(daily_order_value), help="Non-cancelled COD order value placed today, not money received.")
        st.dataframe(
            pd.DataFrame([
                {"Order": order["id"], "Time": order["created"], "Order value": money(order["total"]),
                 "Farmer": order.get("farmer", "—"), "Status": order["status"]}
                for order in daily_orders
            ]),
            use_container_width=True, hide_index=True,
        )
    with report_tabs[1]:
        status_counts = pd.Series([order["status"] for order in st.session_state.orders]).value_counts() if st.session_state.orders else pd.Series(dtype=int)
        summary = pd.DataFrame([
            {"Metric": "All orders", "Value": str(len(st.session_state.orders))},
            {"Metric": "Gross order value", "Value": money(gross_order_value)},
            {"Metric": "COD collected", "Value": money(collected_cod)},
            {"Metric": "COD due", "Value": money(pending_cod)},
            {"Metric": "Average order value", "Value": money(gross_order_value / len(st.session_state.orders)) if st.session_state.orders else money(0)},
            *({"Metric": f"Orders — {status}", "Value": str(int(count))} for status, count in status_counts.items()),
        ])
        st.dataframe(summary, use_container_width=True, hide_index=True)
    with report_tabs[2]:
        history = [
            {
                "Order": order["id"], "Order reference": order.get("order_reference", "Not recorded"),
                "Date": order["created"], "Customer": order.get("owner_email", "—"),
                "Items": " | ".join(
                    f"{item.get('name', 'Product')} × {item.get('quantity', 0)} "
                    f"({item.get('farmer', order.get('farmer', '—'))}; {money(float(item.get('total', 0)))})"
                    for item in order.get("items", [])
                ) or order.get("listing", "—"),
                "Farmer": order.get("farmer", "—"),
                "Farmer location": order.get("farmer_location", "—"), "Distance (km)": order.get("distance_km", "—"),
                "ETA (min)": order.get("eta_minutes", "—"), "Total": money(order["total"]),
                "Status": order["status"], "Payment": order.get("payment", "—"),
                "Payment status": order.get("payment_status", "—"), "Delivery": order["address"],
            }
            for order in st.session_state.orders
        ]
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
    with report_tabs[3]:
        st.caption(
            "Central admin view of marketplace records. Records remain in the configured "
            "private durable snapshot so customer and farmer sessions share the same data. "
            "Passwords and face templates are never displayed here."
        )
        st.subheader("Accounts")
        account_rows = [
            {
                "Username": f"@{user['username']}",
                "Email": user["email"],
                "Role": user["role"],
                "FRS photo": "Saved" if user.get("frs_photo") else "Not required",
                "Face enrolled": "Yes" if user.get("face_encoding") else "No",
            }
            for user in st.session_state.users.values()
        ]
        st.dataframe(
            pd.DataFrame(account_rows),
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("Listings and inventory")
        inventory_columns = [
            "id", "name", "category", "farmer", "farmer_id", "farmer_location",
            "crop_details", "price", "unit", "stock", "organic", "image_url",
        ]
        st.dataframe(
            pd.DataFrame(st.session_state.products).reindex(columns=inventory_columns),
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("Orders")
        all_orders = [
            {
                "Order": order["id"],
                "Order reference": order.get("order_reference", "Not recorded"),
                "Date": order["created"],
                "Customer": order.get("owner_email", "—"),
                "Items": " | ".join(
                    f"{item.get('name', 'Product')} × {item.get('quantity', 0)} "
                    f"({item.get('farmer', order.get('farmer', '—'))}; "
                    f"{money(float(item.get('total', 0)))})"
                    for item in order.get("items", [])
                ) or order.get("listing", "—"),
                "Total": money(order["total"]),
                "Status": order["status"],
                "Payment status": order.get("payment_status", "—"),
                "Delivery": order["address"],
            }
            for order in st.session_state.orders
        ]
        st.dataframe(
            pd.DataFrame(all_orders),
            use_container_width=True,
            hide_index=True,
        )
    st.subheader("Visual reports")
    delivered_value = sum(
        order["total"] for order in st.session_state.orders
        if order.get("status") == "Delivered" and order.get("payment_status") == "Collected"
    )
    cancelled_value = sum(
        order["total"] for order in st.session_state.orders
        if str(order.get("status", "")).lower() in {"cancelled", "refunded"}
    )
    pending_value = pending_cod
    gain_loss_data = pd.DataFrame([
        {"Type": "COD collected", "Amount": delivered_value},
        {"Type": "Pending COD value", "Amount": pending_value},
        {"Type": "Cancelled order value", "Amount": cancelled_value},
    ])
    chart_columns = st.columns(2)
    with chart_columns[0]:
        st.markdown("**COD collected, pending value, and cancelled value**")
        st.bar_chart(gain_loss_data.set_index("Type"), y="Amount", color="#2e7d32")
        st.caption("COD collection is recorded manually after an operator confirms payment. Cancelled orders are not payments or losses.")
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
        farmer_interest.setdefault(farmer, {"orders": 0, "order_value": 0.0})
        farmer_interest[farmer]["orders"] += 1
        farmer_interest[farmer]["order_value"] += float(order.get("total", 0))
    if farmer_interest:
        interest_data = pd.DataFrame([
            {"Farmer": farmer, "Orders": values["orders"], "Order value": values["order_value"]}
            for farmer, values in farmer_interest.items()
        ]).sort_values("Orders", ascending=False)
        interest_columns = st.columns(2)
        with interest_columns[0]:
            st.markdown("**Customer interest by farmer**")
            st.vega_lite_chart(
                interest_data,
                {
                    "mark": {"type": "arc", "innerRadius": 45},
                    "encoding": {
                        "theta": {"field": "Orders", "type": "quantitative"},
                        "color": {"field": "Farmer", "type": "nominal"},
                        "tooltip": [
                            {"field": "Farmer", "type": "nominal"},
                            {"field": "Orders", "type": "quantitative"},
                            {"field": "Order value", "type": "quantitative"},
                        ],
                    },
                    "title": "Customer order share by farmer",
                },
                use_container_width=True,
            )
        with interest_columns[1]:
            st.markdown("**Farmer order-value comparison**")
            st.bar_chart(interest_data.set_index("Farmer"), y="Order value", color="#ef6c00")
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
                "Face matching": "Enabled" if user.get("face_encoding") else "Unavailable — sign-in blocked",
                "Last face verification": user.get("last_face_verification") or "Not recorded",
                "Latest capture": "Saved" if user.get("last_face_capture") else "Not captured",
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
        camera_permission_guidance()
        admin_camera_photo = st.camera_input(
            "Admin FRS camera access: capture the selected farmer",
            help="Camera access is used only for this verification attempt.",
            key="admin-frs-camera",
        )
        if admin_camera_photo:
            if not load_face_runtime():
                st.warning("Face matching is not installed in this deployment. Install the optional face-recognition package to verify camera captures.")
            elif not selected_profile.get("face_encoding"):
                st.error("This farmer has no enrolled face profile. Register the farmer with an FRS photo first.")
            elif verify_face(admin_camera_photo.getvalue(), selected_profile["face_encoding"]):
                selected_profile["last_face_verification"] = date.today().isoformat()
                selected_profile["last_face_capture"] = admin_camera_photo.getvalue()
                save_database_user(selected_profile)
                save_cloud_snapshot()
                st.success(f"FRS camera verification completed for @{selected_profile['username']}.")
            else:
                st.error("FRS camera verification failed. Use one clear face and good lighting.")
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
    inventory_columns = [
        "name", "category", "farmer", "farmer_location",
        "crop_details", "price", "stock", "organic",
    ]
    inventory = pd.DataFrame(st.session_state.products).reindex(columns=inventory_columns)
    st.dataframe(inventory, use_container_width=True, hide_index=True)
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
            removed_product = next(
                product for product in st.session_state.products
                if product["id"] == selected_product
            )
            previous_cart_quantity = st.session_state.cart.pop(selected_product, None)
            st.session_state.products = [
                product for product in st.session_state.products if product["id"] != selected_product
            ]
            if save_cloud_snapshot():
                st.success("Product removed from the customer marketplace.")
                st.rerun()
            st.session_state.products.append(removed_product)
            if previous_cart_quantity is not None:
                st.session_state.cart[selected_product] = previous_cart_quantity
            st.error("The product could not be removed from durable storage.")
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
        st.caption("Record fulfillment only after the responsible farmer/operator confirms each real delivery step.")
        selected = st.selectbox("Order", [o["id"] for o in st.session_state.orders])
        new_status = st.selectbox("Set status", ORDER_STATUSES + ["Cancelled"])
        if st.button("Update order status"):
            selected_order = next(order for order in st.session_state.orders if order["id"] == selected)
            if new_status == "Cancelled":
                if cancel_order(selected_order):
                    st.success(f"Order #{selected} cancelled and stock restored.")
                else:
                    st.error("Only orders not yet dispatched can be cancelled; stock was not changed.")
            else:
                old_status = selected_order["status"]
                selected_order["status"] = new_status
                if save_cloud_snapshot():
                    st.success(f"Order #{selected} updated to {new_status}.")
                else:
                    selected_order["status"] = old_status
                    st.error("The order update could not be saved to durable storage.")
        selected_order = next(order for order in st.session_state.orders if order["id"] == selected)
        if selected_order["status"] == "Delivered" and selected_order.get("payment_status") != "Collected":
            st.warning("Only record this after the farmer/operator confirms the COD cash was received.")
            if st.button("Record COD cash received", key="record-cod-collected"):
                previous_status = selected_order.get("payment_status")
                previous_collected_at = selected_order.get("collected_at")
                selected_order["payment_status"] = "Collected"
                selected_order["collected_at"] = datetime.now().isoformat()
                if save_cloud_snapshot():
                    st.success(f"COD collection recorded for order #{selected}.")
                else:
                    selected_order["payment_status"] = previous_status
                    if previous_collected_at is None:
                        selected_order.pop("collected_at", None)
                    else:
                        selected_order["collected_at"] = previous_collected_at
                    st.error("COD collection could not be recorded in durable storage.")


def main():
    initialized_session = seed_state()
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
        if not initialized_session:
            sync_cloud_snapshot()
        authentication_view()
        return
    st.sidebar.title("AgriDirect")
    st.sidebar.caption("Farm fresh. Fairly traded. Directly delivered.")
    user = st.session_state.users[st.session_state.authenticated_user]
    # Load the latest shared users, listings, and orders before rendering any
    # authenticated dashboard, including newly registered customer sessions.
    if not initialized_session:
        sync_cloud_snapshot()
    user = st.session_state.users.get(st.session_state.authenticated_user)
    if not user:
        st.session_state.authenticated_user = None
        st.rerun()
    role = user["role"]
    st.sidebar.success(f"Signed in as @{user['username']}")
    ai_voice_mode(role)
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
    st.sidebar.caption(
        "Hosted marketplace changes require configured Supabase Storage or S3."
        if hosted_streamlit_deployment()
        else "Local development data persists in SQLite."
    )
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.authenticated_user = None
        st.rerun()
if __name__ == "__main__":
    main()
