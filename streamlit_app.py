"""AgriDirect: a self-contained Streamlit marketplace demo.

All data lives in st.session_state so the app is zero-setup and suitable for
local demos, classroom use, and quick deployment on Streamlit Community Cloud.
"""

from datetime import datetime

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="AgriDirect Marketplace",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded",
)

DELIVERY_FEE = 40
ORDER_STATUSES = ["Placed", "Confirmed", "Preparing", "Out for delivery", "Delivered"]


def seed_state():
    """Create a fresh in-memory marketplace for the current browser session."""
    if "products" not in st.session_state:
        st.session_state.products = [
            {"id": 1, "name": "Farm Fresh Tomatoes", "category": "Vegetables", "price": 48.0, "unit": "kg", "stock": 32, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Juicy, vine-ripened tomatoes harvested this morning.", "emoji": "🍅"},
            {"id": 2, "name": "Alphonso Mangoes", "category": "Fruits", "price": 180.0, "unit": "kg", "stock": 18, "farmer": "Sunrise Orchards", "farmer_id": "orchard@agridirect.local", "organic": True, "description": "Naturally sweet seasonal mangoes from our orchard.", "emoji": "🥭"},
            {"id": 3, "name": "Organic Basmati Rice", "category": "Grains", "price": 125.0, "unit": "kg", "stock": 50, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Aromatic long-grain rice, grown without synthetic pesticides.", "emoji": "🌾"},
            {"id": 4, "name": "Cold-Pressed Groundnut Oil", "category": "Pantry", "price": 220.0, "unit": "litre", "stock": 12, "farmer": "Harvest Collective", "farmer_id": "collective@agridirect.local", "organic": False, "description": "Small-batch wood-pressed oil with a rich, nutty flavour.", "emoji": "🫙"},
            {"id": 5, "name": "Fresh Spinach", "category": "Vegetables", "price": 35.0, "unit": "bunch", "stock": 40, "farmer": "Green Valley Farm", "farmer_id": "farmer@agridirect.local", "organic": True, "description": "Tender leafy greens picked at sunrise.", "emoji": "🥬"},
            {"id": 6, "name": "Raw Forest Honey", "category": "Pantry", "price": 310.0, "unit": "500 g", "stock": 15, "farmer": "Hilltop Apiary", "farmer_id": "apiary@agridirect.local", "organic": True, "description": "Unfiltered wildflower honey collected from local hives.", "emoji": "🍯"},
        ]
    st.session_state.setdefault("cart", {})
    st.session_state.setdefault("orders", [])
    st.session_state.setdefault("next_product_id", 7)
    st.session_state.setdefault("next_order_id", 1001)


def money(value):
    return f"₹{value:,.2f}"


def current_role():
    return st.session_state.get("role", "Customer")


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
    st.title("🌱 Shop directly from local farms")
    st.write("Fresh produce, fair prices, and transparent farmer relationships.")
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
    }
    st.session_state.orders.insert(0, order)
    st.session_state.next_order_id += 1
    for product, quantity in purchased_rows:
        product["stock"] -= quantity
    st.session_state.cart.clear()
    st.success(f"Order #{order['id']} placed successfully! Pay {money(total)} on delivery.")
    st.balloons()


def render_orders():
    if not st.session_state.orders:
        st.info("Your placed orders will appear here.")
        return
    for order in st.session_state.orders:
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
    st.subheader("Add a product")
    with st.form("new-product"):
        name = st.text_input("Product name")
        description = st.text_area("Description")
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
                })
                st.session_state.next_product_id += 1
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
            st.success(f"Order #{selected} updated to {new_status}.")


def main():
    seed_state()
    st.sidebar.title("AgriDirect")
    st.sidebar.caption("Farm fresh. Fairly traded. Directly delivered.")
    role = st.sidebar.radio("Choose your workspace", ["Customer", "Farmer", "Admin"], index=["Customer", "Farmer", "Admin"].index(current_role()))
    st.session_state.role = role
    if role == "Farmer":
        st.session_state.user_email = st.sidebar.selectbox("Farmer account", ["farmer@agridirect.local", "orchard@agridirect.local", "collective@agridirect.local", "apiary@agridirect.local"])
    else:
        st.session_state.user_email = "customer@agridirect.local"
    if role == "Customer":
        customer_view()
    elif role == "Farmer":
        farmer_view()
    else:
        admin_view()
    st.sidebar.divider()
    st.sidebar.caption("Demo data is stored in this browser session only.")
    if st.sidebar.button("Reset demo data"):
        for key in ["products", "cart", "orders", "next_product_id", "next_order_id"]:
            st.session_state.pop(key, None)
        st.rerun()


if __name__ == "__main__":
    main()
