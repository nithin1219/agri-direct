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
import secrets

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
def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${digest}"


def password_matches(password, stored_hash):
    salt, expected = stored_hash.split("$", 1)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return secrets.compare_digest(actual, expected)


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
    image = face_recognition.load_image_file(io.BytesIO(image_bytes))
    encodings = face_recognition.face_encodings(image)
    return encodings[0].tolist() if encodings else None


def verify_face(image_bytes, reference):
    if face_recognition is None or not reference:
        return False
    try:
        candidate = face_encoding(image_bytes)
        return bool(candidate and face_recognition.compare_faces([reference], candidate, tolerance=0.48)[0])
    except (OSError, ValueError):
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
    """Best-effort optional S3 snapshot; local session remains the source of truth."""
    bucket, region = storage_config()
    if not bucket or boto3 is None:
        return
    try:
        payload = {
            "products": [{**product, "image_bytes": _encode_bytes(product.get("image_bytes"))} for product in st.session_state.products],
            "users": {email: {**user, "frs_photo": _encode_bytes(user.get("frs_photo"))} for email, user in st.session_state.users.items()},
            "orders": st.session_state.orders,
        }
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket,
            Key="agridirect/state.json",
            Body=json.dumps(payload, default=str).encode(),
            ContentType="application/json",
        )
    except (BotoCoreError, ClientError, OSError, ValueError):
        # Cloud credentials are optional; never make checkout or publishing fail.
        return


def sync_cloud_snapshot():
    """Refresh shared marketplace data without disturbing the signed-in session."""
    snapshot = load_cloud_snapshot()
    if not snapshot:
        return False
    if snapshot.get("products"):
        st.session_state.products = snapshot["products"]
        for product in st.session_state.products:
            product["image_bytes"] = _decode_bytes(product.get("image_bytes"))
    if snapshot.get("users"):
        current_email = st.session_state.get("authenticated_user")
        st.session_state.users = snapshot["users"]
        for user in st.session_state.users.values():
            user["frs_photo"] = _decode_bytes(user.get("frs_photo"))
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
    snapshot = load_cloud_snapshot()
    if "products" not in st.session_state:
        st.session_state.products = (snapshot or {}).get("products") or [
            {"id": 1, "name": "Farm Fresh Tomatoes", "category": "Vegetables", "price": 48.0, "unit": "kg", "stock": 32, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Juicy, vine-ripened tomatoes harvested this morning.", "emoji": "🍅", "image_url": "https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=900"},
            {"id": 2, "name": "Alphonso Mangoes", "category": "Fruits", "price": 180.0, "unit": "kg", "stock": 18, "farmer": "Sunrise Orchards", "farmer_id": "orchard@agridirect.local", "organic": True, "description": "Naturally sweet seasonal mangoes from our orchard.", "emoji": "🥭", "image_url": "https://images.unsplash.com/photo-1553279768-865429fa0078?w=900"},
            {"id": 3, "name": "Organic Basmati Rice", "category": "Grains", "price": 125.0, "unit": "kg", "stock": 50, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Aromatic long-grain rice, grown without synthetic pesticides.", "emoji": "🌾", "image_url": "https://images.unsplash.com/photo-1536304993881-ff6e9eefa2a6?w=900"},
            {"id": 4, "name": "Cold-Pressed Groundnut Oil", "category": "Pantry", "price": 220.0, "unit": "litre", "stock": 12, "farmer": "Harvest Collective", "farmer_id": "farmer@agridirect.local", "organic": False, "description": "Small-batch wood-pressed oil with a rich, nutty flavour.", "emoji": "🫙", "image_url": "https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?w=900"},
            {"id": 5, "name": "Fresh Spinach", "category": "Vegetables", "price": 35.0, "unit": "bunch", "stock": 40, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Tender leafy greens picked at sunrise.", "emoji": "🥬", "image_url": "https://images.unsplash.com/photo-1576045057995-568f588f82fb?w=900"},
            {"id": 6, "name": "Raw Forest Honey", "category": "Pantry", "price": 310.0, "unit": "500 g", "stock": 15, "farmer": "Sunrise Orchards", "farmer_id": "orchard@agridirect.local", "organic": True, "description": "Unfiltered wildflower honey collected from local hives.", "emoji": "🍯", "image_url": "https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=900"},
        ]
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
        st.session_state.users = {
            email: {"email": email, "role": role, "username": username, "password": password_hash(password)}
            for email, (role, password, username) in DEMO_ACCOUNTS.items()
        }
        if snapshot and snapshot.get("users"):
            st.session_state.users = snapshot["users"]
            for user in st.session_state.users.values():
                user["frs_photo"] = _decode_bytes(user.get("frs_photo"))
    st.session_state.setdefault("authenticated_user", None)


def authentication_view():
    st.title("🌱 Welcome to AgriDirect")
    st.write("Sign in to shop from local farms, manage listings, or review marketplace operations.")
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
            registered = st.form_submit_button("Create account", use_container_width=True)
        if registered:
            normalized_email = new_email.strip().lower()
            username = new_username.strip().lower()
            if "@" not in normalized_email or not new_password or not username:
                st.error("Enter a valid email and password.")
            elif len(new_password) < 8:
                st.error("Password must be at least 8 characters.")
            elif new_password != confirm_password:
                st.error("Passwords do not match.")
            elif normalized_email in st.session_state.users:
                st.error("An account with that email already exists.")
            elif any(user["username"] == username for user in st.session_state.users.values()):
                st.error("That username is already taken.")
            elif account_role == "Farmer" and not farmer_photo:
                st.error("Farmer registration requires an FRS profile photo.")
            else:
                st.session_state.users[normalized_email] = {
                    "email": normalized_email, "role": account_role, "username": username,
                    "password": password_hash(new_password),
                    "frs_photo": farmer_photo.getvalue() if farmer_photo else None,
                    "frs_photo_name": farmer_photo.name if farmer_photo else None,
                    "face_encoding": face_encoding(farmer_photo.getvalue()) if farmer_photo and account_role == "Farmer" else None,
                }
                save_cloud_snapshot()
                st.session_state.authenticated_user = normalized_email
                st.session_state.role = account_role
                st.success("Account created.")
                st.rerun()


def money(value):
    return f"₹{value:,.2f}"


def current_role():
    email = st.session_state.get("authenticated_user")
    return st.session_state.users.get(email, {}).get("role", "Customer")


def current_user():
    return st.session_state.users[st.session_state.authenticated_user]


def daily_farmer_verification():
    user = current_user()
    if user["role"] != "Farmer" or user.get("last_face_verification") == date.today().isoformat():
        return True
    st.subheader("Daily farmer verification")
    if face_recognition is None or not user.get("face_encoding"):
        st.info("Face matching is not enabled in this deployment. Continue with your secure farmer login.")
        user["last_face_verification"] = date.today().isoformat()
        return True
    photo = st.camera_input("Allow camera access and capture your face to continue")
    if not photo:
        st.warning("Camera access is required for today's farmer dashboard verification.")
        return False
    if verify_face(photo.getvalue(), user["face_encoding"]):
        user["last_face_verification"] = date.today().isoformat()
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
            st.success("Latest farmer listings are now visible.")
            st.rerun()
        st.info("Shared storage is not configured; this demo is using the current session's listings.")
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
    st.success(f"Order #{order['id']} placed successfully! Pay {money(total)} on delivery.")
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
    st.subheader("Add a product")
    product_upload = st.file_uploader(
        "Upload product image",
        type=IMAGE_TYPES,
        help="Supported: JPG, JPEG, PNG, WEBP, GIF, BMP, TIF, and TIFF.",
        key="product-image-upload",
    )
    with st.form("new-product"):
        name = st.text_input("Product name")
        description = st.text_area("Description")
        image_url = st.text_input("Product image URL", placeholder="https://...")
        category = st.selectbox("Category", ["Vegetables", "Fruits", "Grains", "Pantry", "Dairy"])
        price, stock = st.columns(2)
        with price:
            product_price = st.number_input("Price (₹)", min_value=1.0, value=50.0)
        with stock:
            product_stock = st.number_input("Quantity in stock", min_value=1, value=10)
        unit = st.text_input("Unit", value="kg")
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
    customers = 1
    farmers = len({p["farmer_id"] for p in st.session_state.products})
    revenue = sum(order["total"] for order in st.session_state.orders)
    columns = st.columns(4)
    columns[0].metric("Products", len(st.session_state.products))
    columns[1].metric("Farmers", farmers)
    columns[2].metric("Orders", len(st.session_state.orders))
    columns[3].metric("Session revenue", money(revenue))
    st.subheader("Marketplace inventory")
    inventory = pd.DataFrame(st.session_state.products)
    st.dataframe(inventory[["name", "category", "farmer", "price", "stock", "organic"]], use_container_width=True, hide_index=True)
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
    st.sidebar.caption("Demo data is stored in this browser session only.")
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.authenticated_user = None
        st.rerun()
    if st.sidebar.button("Reset demo data"):
        for key in ["products", "cart", "orders", "next_product_id", "next_order_id"]:
            st.session_state.pop(key, None)
        st.rerun()


if __name__ == "__main__":
    main()
