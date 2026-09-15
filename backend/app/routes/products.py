from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.database import get_db_connection

products_bp = Blueprint("products_bp", __name__)


@products_bp.route("/products", methods=["GET"])
def list_products():
    conn = get_db_connection("database/agridirect.db")
    rows = conn.execute(
        """
        SELECT p.*, u.name AS farmer_name, c.name AS category_name,
               (SELECT AVG(r.rating) FROM reviews r WHERE r.product_id = p.id) AS rating
        FROM products p
        JOIN users u ON u.id = p.farmer_id
        JOIN categories c ON c.id = p.category_id
        ORDER BY p.created_at DESC
        """
    ).fetchall()
    conn.close()
    return jsonify({"products": [dict(row) for row in rows]})


@products_bp.route("/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    conn = get_db_connection("database/agridirect.db")
    row = conn.execute(
        """
        SELECT p.*, u.name AS farmer_name, c.name AS category_name,
               (SELECT AVG(r.rating) FROM reviews r WHERE r.product_id = p.id) AS rating
        FROM products p
        JOIN users u ON u.id = p.farmer_id
        JOIN categories c ON c.id = p.category_id
        WHERE p.id = ?
        """,
        (product_id,),
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Product not found."}), 404
    return jsonify({"product": dict(row)})


@products_bp.route("/categories", methods=["GET"])
def list_categories():
    conn = get_db_connection("database/agridirect.db")
    rows = conn.execute("SELECT * FROM categories ORDER BY name ASC").fetchall()
    conn.close()
    return jsonify({"categories": [dict(row) for row in rows]})


@products_bp.route("/products", methods=["POST"])
@jwt_required()
def create_product():
    data = request.get_json(silent=True) or {}
    user_id = int(get_jwt_identity())
    conn = get_db_connection("database/agridirect.db")
    user = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user or user["role"] not in {"farmer", "admin"}:
        conn.close()
        return jsonify({"error": "Only farmers and admins can create products."}), 403

    product = (
        user_id,
        int(data.get("category_id") or 0),
        (data.get("name") or "").strip(),
        (data.get("description") or "").strip(),
        float(data.get("price") or 0),
        (data.get("unit") or "kg").strip(),
        int(data.get("quantity") or 0),
        data.get("image") or "",
        data.get("organic_status") or "organic",
        data.get("availability") or "available",
    )

    if not all([product[2], product[1] > 0, product[4] > 0, product[6] > 0]):
        conn.close()
        return jsonify({"error": "Product name, category, price, and quantity are required."}), 400

    conn.execute(
        "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        product,
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Product created successfully."}), 201
