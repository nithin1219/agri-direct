from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required
from werkzeug.security import check_password_hash, generate_password_hash

from app.database import get_db_connection

auth_bp = Blueprint("auth_bp", __name__)


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""
    role = (data.get("role") or "customer").strip()

    if not all([name, email, phone, password]):
        return jsonify({"error": "Name, email, phone, and password are required."}), 400

    if role not in {"customer", "farmer", "admin"}:
        return jsonify({"error": "Invalid role selected."}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters long."}), 400

    conn = get_db_connection("database/agridirect.db")
    existing = conn.execute("SELECT id FROM users WHERE email = ? OR phone = ?", (email, phone)).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "An account with this email or phone number already exists."}), 409

    user_id = conn.execute(
        "INSERT INTO users (name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
        (name, email, phone, generate_password_hash(password), role),
    ).lastrowid
    conn.commit()
    conn.close()

    token = create_access_token(identity=str(user_id))
    return jsonify({"message": "Registration successful.", "token": token, "user": {"id": user_id, "name": name, "email": email, "role": role}}), 201


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    conn = get_db_connection("database/agridirect.db")
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid login credentials."}), 401

    token = create_access_token(identity=str(user["id"]))
    return jsonify({
        "message": "Login successful.",
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "role": user["role"]
        }
    })


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user_id = int(get_jwt_identity())
    conn = get_db_connection("database/agridirect.db")
    user = conn.execute("SELECT id, name, email, role, phone FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()

    if not user:
        return jsonify({"error": "User not found."}), 404

    return jsonify({"user": dict(user)})
