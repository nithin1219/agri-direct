from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.database import get_db_connection

cart_bp = Blueprint("cart_bp", __name__)


@cart_bp.route("/cart", methods=["GET"])
@jwt_required()
def get_cart():
    user_id = int(get_jwt_identity())
    conn = get_db_connection("database/agridirect.db")

    cart = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
    if not cart:
        conn.execute("INSERT INTO carts (user_id) VALUES (?)", (user_id,))
        conn.commit()
        cart = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()

    rows = conn.execute(
        """
        SELECT ci.id, ci.quantity, p.id AS product_id, p.name, p.price, p.unit, p.image, u.name AS farmer_name
        FROM cart_items ci
        JOIN products p ON p.id = ci.product_id
        JOIN users u ON u.id = p.farmer_id
        WHERE ci.cart_id = ?
        """,
        (cart["id"],),
    ).fetchall()
    conn.close()
    return jsonify({"cart": [dict(row) for row in rows]})


@cart_bp.route("/cart", methods=["POST"])
@jwt_required()
def add_to_cart():
    data = request.get_json(silent=True) or {}
    product_id = int(data.get("product_id") or 0)
    quantity = int(data.get("quantity") or 1)
    user_id = int(get_jwt_identity())

    if product_id <= 0 or quantity <= 0:
        return jsonify({"error": "Valid product and quantity are required."}), 400

    conn = get_db_connection("database/agridirect.db")
    cart = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
    if not cart:
        conn.execute("INSERT INTO carts (user_id) VALUES (?)", (user_id,))
        cart = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()

    existing = conn.execute(
        "SELECT id, quantity FROM cart_items WHERE cart_id = ? AND product_id = ?",
        (cart["id"], product_id),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE cart_items SET quantity = quantity + ? WHERE id = ?",
            (quantity, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO cart_items (cart_id, product_id, quantity) VALUES (?, ?, ?)",
            (cart["id"], product_id, quantity),
        )

    conn.commit()
    conn.close()
    return jsonify({"message": "Product added to cart."})
