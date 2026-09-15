from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.database import get_db_connection

admin_bp = Blueprint("admin_bp", __name__)


@admin_bp.route("/admin/dashboard", methods=["GET"])
@jwt_required()
def dashboard():
    user_id = int(get_jwt_identity())
    conn = get_db_connection("database/agridirect.db")
    user = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user or user["role"] != "admin":
        conn.close()
        return jsonify({"error": "Admin access required."}), 403

    stats = {
        "total_users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_farmers": conn.execute("SELECT COUNT(*) FROM users WHERE role = 'farmer'").fetchone()[0],
        "total_customers": conn.execute("SELECT COUNT(*) FROM users WHERE role = 'customer'").fetchone()[0],
        "total_products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "total_orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "total_revenue": conn.execute("SELECT COALESCE(SUM(total_amount), 0) FROM orders").fetchone()[0],
    }
    conn.close()
    return jsonify({"stats": stats})
