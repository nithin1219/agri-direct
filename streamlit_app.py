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
import os
import re
import secrets
import sqlite3

import pandas as pd
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
    save_local_snapshot(payload)
    bucket, region = storage_config()
    if not bucket or boto3 is None:
        return
    try:
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket,
            Key="agridirect/state.json",
            Body=json.dumps(payload, default=str).encode(),
            ContentType="application/json",
        )
    except (BotoCoreError, ClientError, OSError, ValueError):
        # Cloud credentials are optional; never make checkout or publishing fail.
        return


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
    if snapshot.get("users") is not None:
        current_email = st.session_state.get("authenticated_user")
        st.session_state.users = snapshot["users"]
        for user in st.session_state.users.values():
            user["frs_photo"] = _decode_bytes(user.get("frs_photo"))
            user.setdefault("frs_enabled", user.get("role") in {"Farmer", "Admin"})
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
            {"id": 1, "name": "Farm Fresh Tomatoes", "category": "Vegetables", "price": 48.0, "unit": "kg", "stock": 32, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Juicy, vine-ripened tomatoes harvested this morning.", "emoji": "🍅", "image_url": "https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=900"},
            {"id": 2, "name": "Alphonso Mangoes", "category": "Fruits", "price": 180.0, "unit": "kg", "stock": 18, "farmer": "Sunrise Orchards", "farmer_id": "orchard@agridirect.local", "organic": True, "description": "Naturally sweet seasonal mangoes from our orchard.", "emoji": "🥭", "image_url": "https://images.unsplash.com/photo-1553279768-865429fa0078?w=900"},
            {"id": 3, "name": "Organic Basmati Rice", "category": "Grains", "price": 125.0, "unit": "kg", "stock": 50, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Aromatic long-grain rice, grown without synthetic pesticides.", "emoji": "🌾", "image_url": "https://images.unsplash.com/photo-1536304993881-ff6e9eefa2a6?w=900"},
            {"id": 4, "name": "Cold-Pressed Groundnut Oil", "category": "Pantry", "price": 220.0, "unit": "litre", "stock": 12, "farmer": "Harvest Collective", "farmer_id": "farmer@agridirect.local", "organic": False, "description": "Small-batch wood-pressed oil with a rich, nutty flavour.", "emoji": "🫙", "image_url": "https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?w=900"},
            {"id": 5, "name": "Fresh Spinach", "category": "Vegetables", "price": 35.0, "unit": "bunch", "stock": 40, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Tender leafy greens picked at sunrise.", "emoji": "🥬", "image_url": "https://images.unsplash.com/photo-1576045057995-568f588f82fb?w=900"},
            {"id": 6, "name": "Raw Forest Honey", "category": "Pantry", "price": 310.0, "unit": "500 g", "stock": 15, "farmer": "Sunrise Orchards", "farmer_id": "orchard@agridirect.local", "organic": True, "description": "Unfiltered wildflower honey collected from local hives.", "emoji": "🍯", "image_url": "https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=900"},
        ]
        st.session_state.products = (
            snapshot["products"] if snapshot and "products" in snapshot else seeded_products
        )
        for product in st.session_state.products:
            product["image_bytes"] = _decode_bytes(product.get("image_bytes"))
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
    st.session_state.setdefault("authenticated_user", None)
    if not snapshot:
        save_cloud_snapshot()


def authentication_view():
    st.title("🌱 Welcome to AgriDirect")
    st.success("Welcome! Sign in to continue to your AgriDirect marketplace.")
    st.write("Use your registered email or username and password to shop, manage listings, or review marketplace operations.")
    login_tab, register_tab = st.tabs(["Sign in", "Create account"])
    with login_tab:
        with st.form("login-form"):
            email = st.text_input("Email or username", placeholder="you@example.com or greenvalley")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            login_value = email.strip().lower()
            user = st.session_state.users.get(login_value)
            if not user:
                user = next((candidate for candidate in st.session_state.users.values() if candidate["username"] == login_value), None)
            if user and password_matches(password, user["password"]):
                st.session_state.authenticated_user = user["email"]
                st.session_state.role = user["role"]
                st.rerun()
            else:
                st.error("Invalid email or password.")
        with st.expander("Demo accounts"):
            st.code("customer / customer123\n" "greenvalley / farmer123\n" "sunrise / orchard123\n" "admin / admin123")
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
    if not product:
        return
    existing = st.session_state.cart.get(product_id, 0)
    st.session_state.cart[product_id] = min(existing + quantity, product["stock"])


def cart_rows():
    rows = []
    for product_id, quantity in st.session_state.cart.items():
        product = next((p for p in st.session_state.products if p["id"] == product_id), None)
        if product and quantity:
            rows.append((product, quantity))
    return rows


def show_product_card(product):
    with st.container(border=True):
        if product.get("image_bytes"):
            st.image(product["image_bytes"], caption=product.get("image_name", "Uploaded product image"), use_container_width=True)
        elif product.get("image_url"):
            st.image(product["image_url"], use_container_width=True)
        st.markdown(f"### {product['emoji']} {product['name']}")
        st.caption(f"{product['farmer']} · {product['category']}")
        st.write(product["description"])
        st.markdown(f"**{money(product['price'])} / {product['unit']}**")
        st.caption(f"{'Organic' if product['organic'] else 'Conventional'} · {product['stock']} {product['unit']} available")
        if current_role() == "Customer":
            if st.button("Add to cart", key=f"add-{product['id']}", use_container_width=True):
                add_to_cart(product["id"])
                st.toast(f"Added {product['name']} to your cart")


def customer_view():
    sync_cloud_snapshot()
    st.title("🌱 Shop directly from local farms")
    st.write("Fresh produce, fair prices, and transparent farmer relationships.")
    if st.button("Refresh marketplace listings", help="Load the newest farmer listings from shared storage."):
        if sync_cloud_snapshot():
            st.success("Latest persisted listings are now visible.")
            st.rerun()
        st.info("No persisted snapshot is available yet; showing this deployment's local marketplace.")
    cart_count = sum(st.session_state.cart.values())
    tabs = st.tabs(["Browse products", f"Cart ({cart_count})", "My orders"])

    with tabs[0]:
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

    with tabs[1]:
        render_cart()

    with tabs[2]:
        render_orders()


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
    with st.expander("Checkout with Cash on Delivery", expanded=True):
        with st.form("checkout"):
            address = st.text_area("Delivery address", placeholder="House number, street, locality")
            city = st.text_input("City", value="Hyderabad")
            pincode = st.text_input("PIN code", max_chars=6)
            submitted = st.form_submit_button("Place COD order", type="primary", use_container_width=True)
        if submitted:
            if not address.strip() or len(pincode.strip()) != 6 or not pincode.isdigit():
                st.error("Enter a delivery address and a valid 6-digit PIN code.")
            else:
                place_order(address.strip(), city.strip(), pincode.strip(), subtotal + DELIVERY_FEE)


def update_quantity(product_id):
    st.session_state.cart[product_id] = st.session_state[f"qty-{product_id}"]


def place_order(address, city, pincode, total):
    purchased_rows = cart_rows()
    order = {
        "id": st.session_state.next_order_id,
        "created": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "items": [{"name": p["name"], "quantity": q, "total": p["price"] * q} for p, q in purchased_rows],
        "total": total,
        "address": f"{address}, {city} - {pincode}",
        "status": "Placed",
        "payment": "Cash on Delivery",
        "owner_email": st.session_state.authenticated_user,
    }
    st.session_state.orders.insert(0, order)
    st.session_state.next_order_id += 1
    for product, quantity in purchased_rows:
        product["stock"] -= quantity
    st.session_state.cart.clear()
    save_cloud_snapshot()
    st.success(f"Thank you for your purchase! Order #{order['id']} was placed successfully.")
    st.info(f"Your total is {money(total)}. Payment method: Cash on Delivery.")
    st.balloons()


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
            st.caption(f"{order['payment']} · Deliver to {order['address']}")


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
        description = st.text_area("Description", key="product-description-field")
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
        if st.form_submit_button("Publish listing", type="primary"):
            if not name.strip():
                st.error("A product name is required.")
            else:
                st.session_state.products.append({
                    "id": st.session_state.next_product_id, "name": name.strip(), "category": category,
                    "price": product_price, "unit": unit.strip() or "kg", "stock": int(product_stock),
                    "farmer": "My farm", "farmer_id": farmer_id, "organic": True,
                    "description": description.strip() or "Freshly harvested from our farm.", "emoji": "🌿",
                    "image_url": image_url.strip(),
                    "image_bytes": product_upload.getvalue() if product_upload else None,
                    "image_name": product_upload.name if product_upload else None,
                })
                st.session_state.next_product_id += 1
                save_cloud_snapshot()
                st.success("Your product is now live in the marketplace.")
                st.rerun()
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
    columns = st.columns(4)
    columns[0].metric("Products", len(st.session_state.products))
    columns[1].metric("Farmers", farmers)
    columns[2].metric("Orders", len(st.session_state.orders))
    columns[3].metric("Session revenue", money(revenue))
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
    st.subheader("Marketplace inventory")
    inventory = pd.DataFrame(st.session_state.products)
    st.dataframe(inventory[["name", "category", "farmer", "price", "stock", "organic"]], use_container_width=True, hide_index=True)
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
        order_data = [{"Order": o["id"], "Date": o["created"], "Total": money(o["total"]), "Status": o["status"], "Payment": o["payment"]} for o in st.session_state.orders]
        st.dataframe(pd.DataFrame(order_data), use_container_width=True, hide_index=True)
        st.caption("Demo controls: advance an order status to preview fulfillment management.")
        selected = st.selectbox("Order", [o["id"] for o in st.session_state.orders])
        new_status = st.selectbox("Set status", ORDER_STATUSES)
        if st.button("Update order status"):
            next(order for order in st.session_state.orders if order["id"] == selected)["status"] = new_status
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
    role = user["role"]
    st.sidebar.success(f"Signed in as @{user['username']}")
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
