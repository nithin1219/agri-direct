from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.database import get_db_connection

orders_bp = Blueprint("orders_bp", __name__)


@orders_bp.route("/orders", methods=["GET"])
@jwt_required()
def list_orders():
    user_id = int(get_jwt_identity())
    conn = get_db_connection("database/agridirect.db")
    rows = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return jsonify({"orders": [dict(row) for row in rows]})


@orders_bp.route("/orders", methods=["POST"])
@jwt_required()
def create_order():
    data = request.get_json(silent=True) or {}
    user_id = int(get_jwt_identity())
    conn = get_db_connection("database/agridirect.db")

    cart = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
    if not cart:
        conn.close()
        return jsonify({"error": "Your cart is empty."}), 400

    items = conn.execute(
        "SELECT ci.id, ci.product_id, ci.quantity, p.price, p.farmer_id, p.name FROM cart_items ci JOIN products p ON p.id = ci.product_id WHERE ci.cart_id = ?",
        (cart["id"],),
    ).fetchall()

    if not items:
        conn.close()
        return jsonify({"error": "Your cart is empty."}), 400

    total_amount = sum(item["price"] * item["quantity"] for item in items) + 40
    order_id = conn.execute(
        "INSERT INTO orders (user_id, total_amount, delivery_address, city, state, pincode, payment_method, payment_status, order_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            total_amount,
            data.get("delivery_address") or "Home",
            data.get("city") or "Hyderabad",
            data.get("state") or "Telangana",
            data.get("pincode") or "500001",
            data.get("payment_method") or "cod",
            "pending",
            "placed",
        ),
    ).lastrowid

    for item in items:
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, farmer_id, quantity, price, total_price) VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, item["product_id"], item["farmer_id"], item["quantity"], item["price"], item["price"] * item["quantity"]),
        )

    conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart["id"],))
    conn.commit()
    conn.close()
    return jsonify({"message": "Order placed successfully.", "order_id": order_id}), 201
