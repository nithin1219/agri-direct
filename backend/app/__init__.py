import os
from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from dotenv import load_dotenv

from app.database import init_db
from app.routes.auth import auth_bp
from app.routes.products import products_bp
from app.routes.cart import cart_bp
from app.routes.orders import orders_bp
from app.routes.admin import admin_bp
from app.routes.dashboard import dashboard_bp

load_dotenv()


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "agridirect-secret")
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "agridirect-jwt-secret")
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = 86400
    app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", os.path.join(os.getcwd(), "uploads"))
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", "16777216"))
    app.config["SQLITE_DB_PATH"] = os.getenv("SQLITE_DB_PATH", os.path.join(os.getcwd(), "database", "agridirect.db"))

    CORS(app, resources={r"/api/*": {"origins": "*"}})
    JWTManager(app)

    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(products_bp, url_prefix="/api")
    app.register_blueprint(cart_bp, url_prefix="/api")
    app.register_blueprint(orders_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp, url_prefix="/api")

    @app.route("/")
    def root():
        return jsonify({"message": "AgriDirect API is running."})

    @app.route("/api/health")
    def health():
        return jsonify({
            "status": "ok",
            "service": "AgriDirect backend",
            "message": "Backend is running successfully."
        })

    init_db(app.config["SQLITE_DB_PATH"])
    return app
