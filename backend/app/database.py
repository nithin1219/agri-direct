import os
import sqlite3


def get_db_connection(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = get_db_connection(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('customer', 'farmer', 'admin')) DEFAULT 'customer',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS farmer_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            farm_name TEXT NOT NULL,
            location TEXT NOT NULL,
            experience INTEGER NOT NULL DEFAULT 1,
            description TEXT,
            profile_image TEXT,
            approval_status TEXT NOT NULL CHECK(approval_status IN ('pending', 'approved', 'rejected')) DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            image TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farmer_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            price REAL NOT NULL,
            unit TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            image TEXT,
            organic_status TEXT CHECK(organic_status IN ('organic', 'non_organic')) DEFAULT 'organic',
            availability TEXT CHECK(availability IN ('available', 'unavailable')) DEFAULT 'available',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (farmer_id) REFERENCES users(id),
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );

        CREATE TABLE IF NOT EXISTS carts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cart_id) REFERENCES carts(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            total_amount REAL NOT NULL,
            delivery_address TEXT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            pincode TEXT NOT NULL,
            payment_method TEXT CHECK(payment_method IN ('cod', 'online')) DEFAULT 'cod',
            payment_status TEXT CHECK(payment_status IN ('pending', 'paid', 'failed')) DEFAULT 'pending',
            order_status TEXT CHECK(order_status IN ('placed', 'confirmed', 'preparing', 'ready_for_delivery', 'out_for_delivery', 'delivered', 'cancelled')) DEFAULT 'placed',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            farmer_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            total_price REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders(id),
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (farmer_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            comment TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE TABLE IF NOT EXISTS wishlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, product_id),
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()
    seed_sample_data(conn)
    conn.close()


def seed_sample_data(conn):
    category_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if category_count == 0:
        categories = [
            ("Vegetables", "Fresh seasonal vegetables from local farms.", ""),
            ("Fruits", "Naturally ripened fruits and orchard produce.", ""),
            ("Grains", "Rice, wheat, and other staple grains.", ""),
            ("Pulses", "Nutritious pulses and legumes.", ""),
            ("Organic", "Certified organic produce and staples.", ""),
            ("Spices", "Aromatic spices and flavour essentials.", "")
        ]
        conn.executemany(
            "INSERT INTO categories (name, description, image) VALUES (?, ?, ?)",
            categories,
        )

    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if user_count == 0:
        from werkzeug.security import generate_password_hash
        conn.execute(
            "INSERT INTO users (name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("Admin User", "admin@agridirect.com", "9876543210", generate_password_hash("Admin@123"), "admin"),
        )
        conn.execute(
            "INSERT INTO users (name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("Farmer Ramesh", "farmer@agridirect.com", "9123456780", generate_password_hash("Farmer@123"), "farmer"),
        )
        conn.execute(
            "INSERT INTO users (name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("Customer Priya", "customer@agridirect.com", "9000012345", generate_password_hash("Customer@123"), "customer"),
        )

        conn.execute(
            "INSERT INTO farmer_profiles (user_id, farm_name, location, experience, description, approval_status) VALUES (?, ?, ?, ?, ?, ?)",
            (2, "Green Valley Farms", "Hyderabad, Telangana", 8, "Growing fresh vegetables and organic produce for local families.", "approved"),
        )

    product_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if product_count == 0:
        conn.execute(
            "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 1, "Tomatoes", "Fresh red tomatoes grown in Telangana fields.", 72, "kg", 120, "", "organic", "available"),
        )
        conn.execute(
            "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 1, "Potatoes", "Clean and farm-fresh potatoes ideal for daily meals.", 38, "kg", 200, "", "non_organic", "available"),
        )
        conn.execute(
            "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 1, "Onions", "Premium onions harvested with care.", 30, "kg", 180, "", "non_organic", "available"),
        )
        conn.execute(
            "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 3, "Rice", "Local aromatic rice packed for family meals.", 52, "kg", 100, "", "organic", "available"),
        )
        conn.execute(
            "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 2, "Mangoes", "Sweet and juicy mangoes from the orchard.", 140, "dozen", 60, "", "organic", "available"),
        )
        conn.execute(
            "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 2, "Bananas", "Handpicked bananas from local farms.", 54, "dozen", 90, "", "organic", "available"),
        )
        conn.execute(
            "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 5, "Spinach", "Fresh spinach packed with nutrition.", 28, "bundle", 150, "", "organic", "available"),
        )
        conn.execute(
            "INSERT INTO products (farmer_id, category_id, name, description, price, unit, quantity, image, organic_status, availability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, 4, "Groundnuts", "Roasted and raw groundnuts harvested fresh.", 95, "kg", 70, "", "organic", "available"),
        )

    cart_count = conn.execute("SELECT COUNT(*) FROM carts").fetchone()[0]
    if cart_count == 0:
        conn.execute("INSERT INTO carts (user_id) VALUES (?)", (3,))
        cart_id = conn.execute("SELECT id FROM carts WHERE user_id = ?", (3,)).fetchone()[0]
        conn.execute(
            "INSERT INTO cart_items (cart_id, product_id, quantity) VALUES (?, ?, ?)",
            (cart_id, 1, 2),
        )
        conn.execute(
            "INSERT INTO cart_items (cart_id, product_id, quantity) VALUES (?, ?, ?)",
            (cart_id, 5, 1),
        )

    conn.commit()
