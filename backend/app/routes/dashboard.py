from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.database import get_db_connection


dashboard_bp = Blueprint("dashboard_bp", __name__)


@dashboard_bp.route("/dashboard", methods=["GET"])
@jwt_required()
def dashboard():
    user_id = int(get_jwt_identity())
    conn = get_db_connection("database/agridirect.db")
    user = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()

    if not user:
        conn.close()
        return jsonify({"error": "User not found."}), 404

    role = user["role"]

    if role == "customer":
        stats = {
            "total_orders": conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (user_id,)).fetchone()[0],
            "active_orders": conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ? AND order_status NOT IN ('delivered', 'cancelled')", (user_id,)).fetchone()[0],
            "completed_orders": conn.execute("SELECT COUNT(*) FROM orders WHERE user_id = ? AND order_status = 'delivered'", (user_id,)).fetchone()[0],
            "wishlist_items": conn.execute("SELECT COUNT(*) FROM wishlist WHERE user_id = ?", (user_id,)).fetchone()[0],
        }
        recent_orders = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()
        response = {"role": role, "stats": stats, "recent_orders": [dict(row) for row in recent_orders]}
        conn.close()
        return jsonify(response)

    if role == "farmer":
        stats = {
            "total_products": conn.execute("SELECT COUNT(*) FROM products WHERE farmer_id = ?", (user_id,)).fetchone()[0],
            "active_products": conn.execute("SELECT COUNT(*) FROM products WHERE farmer_id = ? AND availability = 'available'", (user_id,)).fetchone()[0],
            "orders_received": conn.execute("SELECT COUNT(DISTINCT o.id) FROM orders o JOIN order_items oi ON oi.order_id = o.id WHERE oi.farmer_id = ?", (user_id,)).fetchone()[0],
            "completed_orders": conn.execute("SELECT COUNT(DISTINCT o.id) FROM orders o JOIN order_items oi ON oi.order_id = o.id WHERE oi.farmer_id = ? AND o.order_status = 'delivered'", (user_id,)).fetchone()[0],
            "total_sales": conn.execute("SELECT COALESCE(SUM(oi.total_price), 0) FROM order_items oi WHERE oi.farmer_id = ?", (user_id,)).fetchone()[0],
            "total_earnings": conn.execute("SELECT COALESCE(SUM(oi.total_price), 0) FROM order_items oi WHERE oi.farmer_id = ?", (user_id,)).fetchone()[0],
        }
        recent_orders = conn.execute(
            "SELECT o.* FROM orders o JOIN order_items oi ON oi.order_id = o.id WHERE oi.farmer_id = ? GROUP BY o.id ORDER BY o.created_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()
        response = {"role": role, "stats": stats, "recent_orders": [dict(row) for row in recent_orders]}
        conn.close()
        return jsonify(response)

    stats = {
        "total_users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "total_farmers": conn.execute("SELECT COUNT(*) FROM users WHERE role = 'farmer'").fetchone()[0],
        "total_customers": conn.execute("SELECT COUNT(*) FROM users WHERE role = 'customer'").fetchone()[0],
        "total_products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
        "total_orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "total_revenue": conn.execute("SELECT COALESCE(SUM(total_amount), 0) FROM orders").fetchone()[0],
    }
    recent_orders = conn.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 5").fetchall()
    response = {"role": role, "stats": stats, "recent_orders": [dict(row) for row in recent_orders]}
    conn.close()
    return jsonify(response)
