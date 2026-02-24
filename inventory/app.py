# imports - standard imports
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from functools import wraps
import secrets
import re
from datetime import timedelta

# imports - third party imports
from flask import Flask, redirect, render_template, request, session, flash, url_for, abort, g
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_NAME = "inventory.sqlite"
_DATABASE_PATH = Path(__file__).parent.parent / DATABASE_NAME
VIEWS = {
    "Summary": "/",
    "Stock": "/product",
    "Warehouses": "/location",
    "Logistics": "/movement",
}
EMPTY_SYMBOLS = {"", " ", None}

app = Flask(__name__)

# Security Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32))
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') != 'development'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

if os.environ.get("FLASK_DEBUG") == "1":
    app.config.update(TEMPLATES_AUTO_RELOAD=True)
    DATABASE_NAME = _DATABASE_PATH.resolve()
else:
    DATABASE_NAME = os.environ.get("DATABASE_NAME") or _DATABASE_PATH.resolve()


def init_database():
    # Users table for authentication
    USERS = (
        "users("
        "user_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT UNIQUE NOT NULL, "
        "password_hash TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "is_active BOOLEAN DEFAULT 1)"
    )
    
    # Products table with ownership
    PRODUCTS = (
        "products("
        "prod_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "prod_name TEXT UNIQUE NOT NULL, "
        "prod_quantity INTEGER NOT NULL, "
        "unallocated_quantity INTEGER, "
        "owner_id INTEGER NOT NULL, "
        "FOREIGN KEY(owner_id) REFERENCES users(user_id))"
    )
    
    # Locations table with ownership
    LOCATIONS = (
        "location("
        "loc_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "loc_name TEXT UNIQUE NOT NULL, "
        "owner_id INTEGER NOT NULL, "
        "FOREIGN KEY(owner_id) REFERENCES users(user_id))"
    )
    
    LOGISTICS = (
        "logistics("
        "trans_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "prod_id INTEGER NOT NULL, "
        "from_loc_id INTEGER NULL, "
        "to_loc_id INTEGER NULL, "
        "prod_quantity INTEGER NOT NULL, "
        "trans_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "FOREIGN KEY(prod_id) REFERENCES products(prod_id), "
        "FOREIGN KEY(from_loc_id) REFERENCES location(loc_id), "
        "FOREIGN KEY(to_loc_id) REFERENCES location(loc_id))"
    )

    with sqlite3.connect(DATABASE_NAME) as conn:
        cursor = conn.cursor()
        
        # Check if we need to migrate existing database
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        users_table_exists = cursor.fetchone() is not None
        
        if not users_table_exists:
            # Fresh database - create all tables
            for table_definition in [USERS, PRODUCTS, LOCATIONS, LOGISTICS]:
                conn.execute(f"CREATE TABLE IF NOT EXISTS {table_definition}")
            
            # Create indexes for performance and security
            conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_owner ON products(owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_location_owner ON location(owner_id)")
            
            # Create default admin user for fresh installations
            admin_password = 'admin123!'  # Change this immediately after first login
            password_hash = generate_password_hash(admin_password)
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
                    ('admin', password_hash)
                )
                print(f"Created default admin user - Password: {admin_password}")
                print("*** CHANGE THIS PASSWORD IMMEDIATELY AFTER FIRST LOGIN ***")
            except sqlite3.IntegrityError:
                pass  # Admin user already exists
        else:
            # Existing database - handle migration
            migrate_existing_database(conn)
        
        # Create trigger for products
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS default_prod_qty_to_unalloc_qty "
            "AFTER INSERT ON products FOR EACH ROW WHEN NEW.unallocated_quantity IS NULL "
            "BEGIN UPDATE products SET unallocated_quantity = NEW.prod_quantity WHERE rowid = NEW.rowid; END"
        )

def migrate_existing_database(conn):
    """Migrate existing database to add user authentication."""
    cursor = conn.cursor()
    
    # Check if users table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if cursor.fetchone() is None:
        # Create users table
        cursor.execute("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        """)
        
        # Create default admin user
        admin_password = 'admin123!'
        password_hash = generate_password_hash(admin_password)
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ('admin', password_hash)
        )
        admin_user_id = cursor.lastrowid
        print(f"Created admin user - Password: {admin_password}")
        print("*** CHANGE THIS PASSWORD IMMEDIATELY ***")
        
        # Add owner_id columns to existing tables
        try:
            cursor.execute("ALTER TABLE products ADD COLUMN owner_id INTEGER")
            cursor.execute("UPDATE products SET owner_id = ?", (admin_user_id,))
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        try:
            cursor.execute("ALTER TABLE location ADD COLUMN owner_id INTEGER")  
            cursor.execute("UPDATE location SET owner_id = ?", (admin_user_id,))
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_products_owner ON products(owner_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_location_owner ON location(owner_id)")


app.init_db = init_database

# ============================================================================
# AUTHENTICATION AND AUTHORIZATION SYSTEM
# ============================================================================

def get_current_user():
    """Get the currently authenticated user."""
    if hasattr(g, '_current_user'):
        return g._current_user
    
    user_id = session.get('user_id')
    if user_id is None:
        g._current_user = None
        return None
    
    with sqlite3.connect(DATABASE_NAME) as conn:
        user = conn.execute(
            "SELECT user_id, username FROM users WHERE user_id = ? AND is_active = 1",
            (user_id,)
        ).fetchone()
        
        if user:
            g._current_user = {'user_id': user[0], 'username': user[1]}
        else:
            g._current_user = None
            session.clear()  # Invalid session
        
        return g._current_user

def login_required(f):
    """Decorator to require authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if get_current_user() is None:
            flash('Please log in to access this page.', 'warning')
            session['next_url'] = request.url
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_owned_resource_or_404(table, id_column, resource_id):
    """
    Get a resource only if it belongs to the current user.
    Returns 404 for both non-existent and unauthorized resources.
    This prevents enumeration attacks.
    """
    user = get_current_user()
    if not user:
        abort(404)
    
    with sqlite3.connect(DATABASE_NAME) as conn:
        resource = conn.execute(
            f"SELECT * FROM {table} WHERE {id_column} = ? AND owner_id = ?",
            (resource_id, user['user_id'])
        ).fetchone()
        
        if resource is None:
            abort(404)  # Use 404, not 403, to prevent enumeration
        
        return resource

def generate_csrf_token():
    """Generate CSRF token for forms."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']

def validate_csrf_token(token):
    """Validate CSRF token."""
    return token and session.get('csrf_token') == token

def csrf_protect(f):
    """Decorator for CSRF protection."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ['POST', 'PUT', 'DELETE']:
            token = request.form.get('csrf_token')
            if not validate_csrf_token(token):
                abort(403)
        return f(*args, **kwargs)
    return decorated_function

@app.context_processor
def inject_globals():
    """Inject common variables into templates."""
    return {
        'current_user': get_current_user(),
        'csrf_token': generate_csrf_token
    }

@app.before_request
def before_request():
    """Run before each request."""
    g.user = get_current_user()

# ============================================================================
# AUTHENTICATION ROUTES
# ============================================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handle user login."""
    if get_current_user():
        return redirect(url_for('summary'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        if not username or not password:
            flash('Please enter both username and password.', 'error')
            return render_template('auth/login.html')
        
        with sqlite3.connect(DATABASE_NAME) as conn:
            user = conn.execute(
                "SELECT user_id, username, password_hash FROM users WHERE username = ? AND is_active = 1",
                (username,)
            ).fetchone()
        
        if user and check_password_hash(user[2], password):
            # Clear any existing session to prevent fixation
            session.clear()
            session['user_id'] = user[0]
            session['username'] = user[1]
            session.permanent = True
            
            flash('Welcome back!', 'success')
            next_url = session.pop('next_url', None)
            return redirect(next_url or url_for('summary'))
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('auth/login.html')

@app.route('/logout')
def logout():
    """Handle user logout."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Handle user registration."""
    if get_current_user():
        return redirect(url_for('summary'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validation
        if len(username) < 3:
            flash('Username must be at least 3 characters.', 'error')
            return render_template('auth/register.html')
        
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('auth/register.html')
        
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('auth/register.html')
        
        # Check if username exists
        with sqlite3.connect(DATABASE_NAME) as conn:
            existing = conn.execute(
                "SELECT user_id FROM users WHERE username = ?", (username,)
            ).fetchone()
            
            if existing:
                flash('Username already taken.', 'error')
                return render_template('auth/register.html')
            
            # Create new user
            password_hash = generate_password_hash(password)
            try:
                conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, password_hash)
                )
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Username already taken.', 'error')
    
    return render_template('auth/register.html')


@app.route("/", methods=["GET"])
@login_required
def summary():
    user = get_current_user()
    with sqlite3.connect(DATABASE_NAME) as conn:
        # Only show user's own data
        warehouse = conn.execute(
            "SELECT * FROM location WHERE owner_id = ?", (user['user_id'],)
        ).fetchall()
        products = conn.execute(
            "SELECT * FROM products WHERE owner_id = ?", (user['user_id'],)
        ).fetchall()
        q_data = conn.execute(
            "SELECT prod_name, unallocated_quantity, prod_quantity FROM products WHERE owner_id = ?",
            (user['user_id'],)
        ).fetchall()

    return render_template(
        "index.jinja",
        link=VIEWS,
        title="Summary",
        warehouses=warehouse,
        products=products,
        summary=q_data,
    )


@app.route("/product", methods=["POST", "GET"])
@login_required
@csrf_protect
def product():
    user = get_current_user()
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            prod_name, quantity = request.form["prod_name"], request.form["prod_quantity"]
            transaction_allowed = prod_name not in EMPTY_SYMBOLS and quantity not in EMPTY_SYMBOLS

            if transaction_allowed:
                # Add product with current user as owner
                conn.execute(
                    "INSERT INTO products (prod_name, prod_quantity, owner_id) VALUES (?, ?, ?)",
                    (prod_name, quantity, user['user_id']),
                )
                return redirect(VIEWS["Stock"])

        # Only show user's products
        products = conn.execute(
            "SELECT * FROM products WHERE owner_id = ?", (user['user_id'],)
        ).fetchall()

    return render_template(
        "product.jinja",
        link=VIEWS,
        products=products,
        title="Stock",
    )


@app.route("/location", methods=["POST", "GET"])
@login_required
@csrf_protect
def location():
    user = get_current_user()
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            warehouse_name = request.form["warehouse_name"]

            if warehouse_name not in EMPTY_SYMBOLS:
                # Add location with current user as owner
                conn.execute(
                    "INSERT INTO location (loc_name, owner_id) VALUES (?, ?)", 
                    (warehouse_name, user['user_id'])
                )
                return redirect(VIEWS["Warehouses"])

        # Only show user's locations
        warehouse_data = conn.execute(
            "SELECT * FROM location WHERE owner_id = ?", (user['user_id'],)
        ).fetchall()

    return render_template(
        "location.jinja",
        link=VIEWS,
        warehouses=warehouse_data,
        title="Warehouses",
    )


def get_warehouse_data(
    conn: sqlite3.Connection, products: list[tuple], locations: list[tuple], user_id: int
) -> list[tuple]:
    log_summary = []
    for p_id in [x[0] for x in products]:
        # Verify product belongs to user
        temp_prod_name = conn.execute(
            "SELECT prod_name FROM products WHERE prod_id = ? AND owner_id = ?", (p_id, user_id)
        ).fetchone()
        
        if not temp_prod_name:
            continue

        for l_id in [x[0] for x in locations]:
            # Verify location belongs to user
            temp_loc_name = conn.execute(
                "SELECT loc_name FROM location WHERE loc_id = ? AND owner_id = ?", (l_id, user_id)
            ).fetchone()
            
            if not temp_loc_name:
                continue
                
            sum_to_loc = conn.execute(
                "SELECT SUM(log.prod_quantity) FROM logistics log "
                "JOIN products p ON log.prod_id = p.prod_id "
                "WHERE log.prod_id = ? AND log.to_loc_id = ? AND p.owner_id = ?",
                (p_id, l_id, user_id),
            ).fetchone()
            sum_from_loc = conn.execute(
                "SELECT SUM(log.prod_quantity) FROM logistics log "
                "JOIN products p ON log.prod_id = p.prod_id "
                "WHERE log.prod_id = ? AND log.from_loc_id = ? AND p.owner_id = ?",
                (p_id, l_id, user_id),
            ).fetchone()
            log_summary += [
                temp_prod_name + temp_loc_name + ((sum_to_loc[0] or 0) - (sum_from_loc[0] or 0),)
            ]

    return log_summary


def update_warehouse_data(conn: sqlite3.Connection, user_id: int):
    # Define secure whitelisted values for SQL query construction
    ALLOWED_COLUMNS = {"to_loc_id", "from_loc_id"}
    ALLOWED_OPERATIONS = {"+", "-"}
    
    update_unallocated_quantity = False
    prod_name, from_loc, to_loc, quantity = (
        request.form["prod_name"],
        request.form["from_loc"],
        request.form["to_loc"],
        request.form["quantity"],
    )

    # Verify user owns the product
    owned_product = conn.execute(
        "SELECT prod_id FROM products WHERE prod_name = ? AND owner_id = ?",
        (prod_name, user_id)
    ).fetchone()
    
    if not owned_product:
        flash('Product not found or access denied.', 'error')
        return

    # if no 'from loc' is given, that means the product is being shipped to a warehouse (init condition)
    if from_loc in EMPTY_SYMBOLS:
        column_name = "to_loc_id"
        operation = "-"
        location_name = to_loc
        update_unallocated_quantity = True
        
        # Verify user owns the destination location
        owned_location = conn.execute(
            "SELECT loc_id FROM location WHERE loc_name = ? AND owner_id = ?",
            (to_loc, user_id)
        ).fetchone()
        
        if not owned_location:
            flash('Location not found or access denied.', 'error')
            return

    # To Location wasn't specified, will be unallocated
    elif to_loc in EMPTY_SYMBOLS:
        column_name = "from_loc_id"
        operation = "+"
        location_name = from_loc
        update_unallocated_quantity = True
        
        # Verify user owns the source location
        owned_location = conn.execute(
            "SELECT loc_id FROM location WHERE loc_name = ? AND owner_id = ?",
            (from_loc, user_id)
        ).fetchone()
        
        if not owned_location:
            flash('Location not found or access denied.', 'error')
            return

    # if 'from loc' and 'to_loc' given the product is being shipped between warehouses
    else:
        # Verify user owns both locations
        from_location = conn.execute(
            "SELECT loc_id FROM location WHERE loc_name = ? AND owner_id = ?",
            (from_loc, user_id)
        ).fetchone()
        
        to_location = conn.execute(
            "SELECT loc_id FROM location WHERE loc_name = ? AND owner_id = ?",
            (to_loc, user_id)
        ).fetchone()
        
        if not from_location or not to_location:
            flash('Location not found or access denied.', 'error')
            return
        
        conn.execute(
            "INSERT INTO logistics (prod_id, from_loc_id, to_loc_id, prod_quantity) "
            "SELECT "
            "(SELECT prod_id FROM products WHERE prod_name = ? AND owner_id = ?) as prod_id, "
            "(SELECT loc_id FROM location WHERE loc_name = ? AND owner_id = ?) as from_loc_id, "
            "(SELECT loc_id FROM location WHERE loc_name = ? AND owner_id = ?) as to_loc_id, "
            "? as prod_quantity",
            (prod_name, user_id, from_loc, user_id, to_loc, user_id, quantity),
        )

    if update_unallocated_quantity:
        # Validate that column_name and operation are from our secure whitelist
        if column_name not in ALLOWED_COLUMNS:
            raise ValueError(f"Invalid column name: {column_name}")
        if operation not in ALLOWED_OPERATIONS:
            raise ValueError(f"Invalid operation: {operation}")
        
        # Use separate queries for each allowed column to avoid f-string interpolation
        if column_name == "to_loc_id":
            conn.execute(
                "INSERT INTO logistics (prod_id, to_loc_id, prod_quantity) "
                "SELECT p.prod_id, l.loc_id, ? FROM products p, location l "
                "WHERE p.prod_name = ? AND p.owner_id = ? AND l.loc_name = ? AND l.owner_id = ?",
                (quantity, prod_name, user_id, location_name, user_id),
            )
        elif column_name == "from_loc_id":
            conn.execute(
                "INSERT INTO logistics (prod_id, from_loc_id, prod_quantity) "
                "SELECT p.prod_id, l.loc_id, ? FROM products p, location l "
                "WHERE p.prod_name = ? AND p.owner_id = ? AND l.loc_name = ? AND l.owner_id = ?",
                (quantity, prod_name, user_id, location_name, user_id),
            )
        
        # Use separate queries for each allowed operation to avoid f-string interpolation
        if operation == "-":
            conn.execute(
                "UPDATE products SET unallocated_quantity = unallocated_quantity - ? "
                "WHERE prod_name = ? AND owner_id = ?",
                (quantity, prod_name, user_id),
            )
        elif operation == "+":
            conn.execute(
                "UPDATE products SET unallocated_quantity = unallocated_quantity + ? "
                "WHERE prod_name = ? AND owner_id = ?",
                (quantity, prod_name, user_id),
            )


def get_warehouse_map(log_summary: list):
    # summary data --> in format:
    # {'Asus Zenfone 2': {'Mahalakshmi': 50, 'Gorhe': 50},
    # 'Prada watch': {'Malad': 50, 'Mahalakshmi': 115}, 'Apple iPhone': {'Airoli': 75}}
    item_location_qty_map = defaultdict(dict)
    for row in log_summary:
        if row[1] in item_location_qty_map[row[0]]:
            item_location_qty_map[row[0]][row[1]] += row[2]
        else:
            item_location_qty_map[row[0]][row[1]] = row[2]
    return json.dumps(item_location_qty_map)


@app.route("/movement", methods=["POST", "GET"])
@login_required
@csrf_protect
def movement():
    user = get_current_user()
    match request.method:
        case "GET":
            with sqlite3.connect(DATABASE_NAME) as conn:
                # Only show user's logistics data
                logistics_data = conn.execute(
                    "SELECT l.* FROM logistics l "
                    "JOIN products p ON l.prod_id = p.prod_id "
                    "WHERE p.owner_id = ?", (user['user_id'],)
                ).fetchall()
                
                products = conn.execute(
                    "SELECT prod_id, prod_name, unallocated_quantity FROM products WHERE owner_id = ?",
                    (user['user_id'],)
                ).fetchall()
                
                locations = conn.execute(
                    "SELECT loc_id, loc_name FROM location WHERE owner_id = ?", 
                    (user['user_id'],)
                ).fetchall()
                
                warehouse_summary = get_warehouse_data(conn, products, locations, user['user_id'])
                item_location_qty_map = get_warehouse_map(warehouse_summary)
                
                return render_template(
                    "movement.jinja",
                    title="Logistics",
                    link=VIEWS,
                    products=products,
                    locations=locations,
                    allocated=item_location_qty_map,
                    logistics=logistics_data,
                    summary=warehouse_summary,
                )

        case "POST":
            with sqlite3.connect(DATABASE_NAME) as conn:
                update_warehouse_data(conn, user['user_id'])
                return redirect(VIEWS["Logistics"])


@app.route("/delete", methods=["POST"])
@login_required
@csrf_protect
def delete():
    """
    SECURE DELETE: Only allows POST requests with CSRF protection.
    Verifies ownership before allowing deletion.
    """
    delete_record_type = request.form.get("type")
    user = get_current_user()

    with sqlite3.connect(DATABASE_NAME) as conn:
        match delete_record_type:
            case "product":
                product_id = request.form.get("prod_id")
                if product_id:
                    # CRITICAL: Verify ownership before deletion
                    owned_product = get_owned_resource_or_404("products", "prod_id", product_id)
                    if owned_product:
                        conn.execute("DELETE FROM products WHERE prod_id = ? AND owner_id = ?", 
                                   (product_id, user['user_id']))
                        flash('Product deleted successfully.', 'success')
                return redirect(VIEWS["Stock"])

            case "location":
                location_id = request.form.get("loc_id")
                if location_id:
                    # CRITICAL: Verify ownership before deletion
                    owned_location = get_owned_resource_or_404("location", "loc_id", location_id)
                    if owned_location:
                        # Check for products still at this location (user's products only)
                        products_at_location = conn.execute(
                            "SELECT COUNT(*) FROM logistics l "
                            "JOIN products p ON l.prod_id = p.prod_id "
                            "WHERE (l.to_loc_id = ? OR l.from_loc_id = ?) AND p.owner_id = ?",
                            (location_id, location_id, user['user_id'])
                        ).fetchone()[0]
                        
                        if products_at_location > 0:
                            flash('Cannot delete location with products. Move products first.', 'error')
                            return redirect(VIEWS["Warehouses"])
                        
                        # Handle product quantity reallocation for user's products only
                        in_place = dict(
                            conn.execute(
                                "SELECT l.prod_id, SUM(l.prod_quantity) FROM logistics l "
                                "JOIN products p ON l.prod_id = p.prod_id "
                                "WHERE l.to_loc_id = ? AND p.owner_id = ? GROUP BY l.prod_id",
                                (location_id, user['user_id']),
                            ).fetchall()
                        )
                        out_place = dict(
                            conn.execute(
                                "SELECT l.prod_id, SUM(l.prod_quantity) FROM logistics l "
                                "JOIN products p ON l.prod_id = p.prod_id "
                                "WHERE l.from_loc_id = ? AND p.owner_id = ? GROUP BY l.prod_id",
                                (location_id, user['user_id']),
                            ).fetchall()
                        )

                        displaced_qty = in_place.copy()
                        for x in in_place:
                            if x in out_place:
                                displaced_qty[x] = displaced_qty[x] - out_place[x]

                        for products_ in displaced_qty:
                            conn.execute(
                                "UPDATE products SET unallocated_quantity = unallocated_quantity + ? "
                                "WHERE prod_id = ? AND owner_id = ?",
                                (displaced_qty[products_], products_, user['user_id']),
                            )
                        
                        conn.execute("DELETE FROM location WHERE loc_id = ? AND owner_id = ?", 
                                   (location_id, user['user_id']))
                        flash('Location deleted successfully.', 'success')
                return redirect(VIEWS["Warehouses"])

            case _:
                return redirect(VIEWS["Summary"])


@app.route("/edit", methods=["POST"])
@login_required
@csrf_protect
def edit():
    """
    SECURE EDIT: Verifies ownership before allowing modifications.
    """
    edit_record_type = request.args.get("type")
    user = get_current_user()

    with sqlite3.connect(DATABASE_NAME) as conn:
        match edit_record_type:
            case "location":
                loc_id, loc_name = request.form["loc_id"], request.form["loc_name"]
                if loc_name and loc_id:
                    # CRITICAL: Verify ownership before editing
                    owned_location = get_owned_resource_or_404("location", "loc_id", loc_id)
                    if owned_location:
                        conn.execute(
                            "UPDATE location SET loc_name = ? WHERE loc_id = ? AND owner_id = ?", 
                            (loc_name, loc_id, user['user_id'])
                        )
                        flash('Location updated successfully.', 'success')
                return redirect(VIEWS["Warehouses"])

            case "product":
                prod_id, prod_name, prod_quantity = (
                    request.form["prod_id"],
                    request.form["prod_name"],
                    request.form["prod_quantity"],
                )

                if prod_id:
                    # CRITICAL: Verify ownership before editing
                    owned_product = get_owned_resource_or_404("products", "prod_id", prod_id)
                    if owned_product:
                        if prod_name:
                            conn.execute(
                                "UPDATE products SET prod_name = ? WHERE prod_id = ? AND owner_id = ?",
                                (prod_name, prod_id, user['user_id']),
                            )
                        if prod_quantity:
                            # Only update if user owns the product
                            old_prod_quantity = conn.execute(
                                "SELECT prod_quantity FROM products WHERE prod_id = ? AND owner_id = ?", 
                                (prod_id, user['user_id'])
                            ).fetchone()
                            
                            if old_prod_quantity:
                                conn.execute(
                                    "UPDATE products SET prod_quantity = ?, unallocated_quantity = unallocated_quantity + ? - ? "
                                    "WHERE prod_id = ? AND owner_id = ?",
                                    (prod_quantity, prod_quantity, old_prod_quantity[0], prod_id, user['user_id']),
                                )
                        flash('Product updated successfully.', 'success')
                return redirect(VIEWS["Stock"])

            case _:
                return redirect(VIEWS["Summary"])


with app.app_context():
    app.init_db()
