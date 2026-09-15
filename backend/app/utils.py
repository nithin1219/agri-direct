from functools import wraps

from flask import jsonify
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request


def require_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            verify_jwt_in_request()
            return fn(*args, **kwargs)
        except Exception:
            return jsonify({"error": "Authentication required."}), 401

    return wrapped


def get_current_user_id():
    return get_jwt_identity()
