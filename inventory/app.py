# imports - standard imports
import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

# imports - third party imports
from flask import Flask, redirect, render_template, request, flash

DATABASE_NAME = "inventory.sqlite"
_DATABASE_PATH = Path(__file__).parent.parent / DATABASE_NAME
VIEWS = {
    "Summary": "/",
    "Stock": "/product",
    "Warehouses": "/location",
    "Logistics": "/movement",
}
EMPTY_SYMBOLS = {"", " ", None}

# Input validation constants
MAX_NAME_LENGTH = 100
MIN_NAME_LENGTH = 1
MAX_QUANTITY = 999999
MIN_QUANTITY = 1
NAME_PATTERN = re.compile(r'^[a-zA-Z0-9\s\-_.,()]+$')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')


class ValidationError(Exception):
    """Custom exception for input validation errors"""
    pass


def validate_name(name, field_name="Name"):
    """Validate product and location names"""
    if not name or name in EMPTY_SYMBOLS:
        raise ValidationError(f"{field_name} is required")
    
    # Convert to string and strip whitespace
    name = str(name).strip()
    
    if len(name) < MIN_NAME_LENGTH:
        raise ValidationError(f"{field_name} must be at least {MIN_NAME_LENGTH} character(s)")
    
    if len(name) > MAX_NAME_LENGTH:
        raise ValidationError(f"{field_name} must not exceed {MAX_NAME_LENGTH} characters")
    
    if not NAME_PATTERN.match(name):
        raise ValidationError(f"{field_name} contains invalid characters. Only letters, numbers, spaces, hyphens, underscores, periods, commas, and parentheses are allowed")
    
    return name


def validate_quantity(quantity, field_name="Quantity"):
    """Validate quantity inputs"""
    if not quantity or quantity in EMPTY_SYMBOLS:
        raise ValidationError(f"{field_name} is required")
    
    # Convert to string first, then attempt integer conversion
    try:
        quantity_int = int(str(quantity).strip())
    except (ValueError, TypeError):
        raise ValidationError(f"{field_name} must be a valid number")
    
    if quantity_int < MIN_QUANTITY:
        raise ValidationError(f"{field_name} must be at least {MIN_QUANTITY}")
    
    if quantity_int > MAX_QUANTITY:
        raise ValidationError(f"{field_name} must not exceed {MAX_QUANTITY}")
    
    return quantity_int


def validate_id(id_value, field_name="ID"):
    """Validate ID inputs"""
    if not id_value or id_value in EMPTY_SYMBOLS:
        raise ValidationError(f"{field_name} is required")
    
    try:
        id_int = int(str(id_value).strip())
    except (ValueError, TypeError):
        raise ValidationError(f"{field_name} must be a valid number")
    
    if id_int < 1:
        raise ValidationError(f"{field_name} must be positive")
    
    return id_int

if os.environ.get("FLASK_DEBUG") == "1":
    app.config.update(TEMPLATES_AUTO_RELOAD=True)
    DATABASE_NAME = _DATABASE_PATH.resolve()
else:
    DATABASE_NAME = os.environ.get("DATABASE_NAME") or _DATABASE_PATH.resolve()


def init_database():
    PRODUCTS = (
        "products("
        "prod_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "prod_name TEXT UNIQUE NOT NULL, "
        "prod_quantity INTEGER NOT NULL, "
        "unallocated_quantity INTEGER)"
    )
    LOCATIONS = "location(loc_id INTEGER PRIMARY KEY AUTOINCREMENT, loc_name TEXT UNIQUE NOT NULL)"
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
        for table_definition in [PRODUCTS, LOCATIONS, LOGISTICS]:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table_definition}")
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS default_prod_qty_to_unalloc_qty "
            "AFTER INSERT ON products FOR EACH ROW WHEN NEW.unallocated_quantity IS NULL "
            "BEGIN UPDATE products SET unallocated_quantity = NEW.prod_quantity WHERE rowid = NEW.rowid; END"
        )


app.init_db = init_database


@app.route("/", methods=["GET"])
def summary():
    with sqlite3.connect(DATABASE_NAME) as conn:
        warehouse = conn.execute("SELECT * FROM location").fetchall()
        products = conn.execute("SELECT * FROM products").fetchall()
        q_data = conn.execute(
            "SELECT prod_name, unallocated_quantity, prod_quantity FROM products"
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
def product():
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            try:
                # Validate inputs
                prod_name = validate_name(request.form.get("prod_name"), "Product name")
                quantity = validate_quantity(request.form.get("prod_quantity"), "Product quantity")
                
                conn.execute(
                    "INSERT INTO products (prod_name, prod_quantity) VALUES (?, ?)",
                    (prod_name, quantity),
                )
                return redirect(VIEWS["Stock"])
                
            except ValidationError as e:
                flash(str(e), 'error')
            except sqlite3.IntegrityError:
                flash("Product name already exists", 'error')

        products = conn.execute("SELECT * FROM products").fetchall()

    return render_template(
        "product.jinja",
        link=VIEWS,
        products=products,
        title="Stock",
    )


@app.route("/location", methods=["POST", "GET"])
def location():
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            try:
                # Validate input
                warehouse_name = validate_name(request.form.get("warehouse_name"), "Warehouse name")
                
                conn.execute("INSERT INTO location (loc_name) VALUES (?)", (warehouse_name,))
                return redirect(VIEWS["Warehouses"])
                
            except ValidationError as e:
                flash(str(e), 'error')
            except sqlite3.IntegrityError:
                flash("Warehouse name already exists", 'error')

        warehouse_data = conn.execute("SELECT * FROM location").fetchall()

    return render_template(
        "location.jinja",
        link=VIEWS,
        warehouses=warehouse_data,
        title="Warehouses",
    )


def get_warehouse_data(
    conn: sqlite3.Connection, products: list[tuple], locations: list[tuple]
) -> list[tuple]:
    log_summary = []
    for p_id in [x[0] for x in products]:
        temp_prod_name = conn.execute(
            "SELECT prod_name FROM products WHERE prod_id = ?", (p_id,)
        ).fetchone()

        for l_id in [x[0] for x in locations]:
            temp_loc_name = conn.execute(
                "SELECT loc_name FROM location WHERE loc_id = ?", (l_id,)
            ).fetchone()
            sum_to_loc = conn.execute(
                "SELECT SUM(log.prod_quantity) FROM logistics log WHERE log.prod_id = ? AND log.to_loc_id = ?",
                (p_id, l_id),
            ).fetchone()
            sum_from_loc = conn.execute(
                "SELECT SUM(log.prod_quantity) FROM logistics log WHERE log.prod_id = ? AND log.from_loc_id = ?",
                (p_id, l_id),
            ).fetchone()
            log_summary += [
                temp_prod_name + temp_loc_name + ((sum_to_loc[0] or 0) - (sum_from_loc[0] or 0),)
            ]

    return log_summary


def update_warehouse_data(conn: sqlite3.Connection):
    # Define secure whitelisted values for SQL query construction
    ALLOWED_COLUMNS = {"to_loc_id", "from_loc_id"}
    ALLOWED_OPERATIONS = {"+", "-"}
    
    update_unallocated_quantity = False
    
    # Validate inputs
    prod_name = validate_name(request.form.get("prod_name"), "Product name")
    quantity = validate_quantity(request.form.get("quantity"), "Quantity")
    
    # Validate location names (can be empty for unallocated items)
    from_loc = request.form.get("from_loc", "").strip()
    to_loc = request.form.get("to_loc", "").strip()
    
    if from_loc and from_loc not in EMPTY_SYMBOLS:
        from_loc = validate_name(from_loc, "From location")
    else:
        from_loc = None
        
    if to_loc and to_loc not in EMPTY_SYMBOLS:
        to_loc = validate_name(to_loc, "To location")
    else:
        to_loc = None

    # if no 'from loc' is given, that means the product is being shipped to a warehouse (init condition)
    if from_loc is None:
        column_name = "to_loc_id"
        operation = "-"
        location_name = to_loc
        update_unallocated_quantity = True

    # To Location wasn't specified, will be unallocated
    elif to_loc is None:
        column_name = "from_loc_id"
        operation = "+"
        location_name = from_loc
        update_unallocated_quantity = True

    # if 'from loc' and 'to_loc' given the product is being shipped between warehouses
    else:
        conn.execute(
            "INSERT INTO logistics (prod_id, from_loc_id, to_loc_id, prod_quantity) "
            "SELECT "
            "(SELECT prod_id FROM products WHERE prod_name = ?) as prod_id, "
            "(SELECT loc_id FROM location WHERE loc_name = ?) as from_loc_id, "
            "(SELECT loc_id FROM location WHERE loc_name = ?) as to_loc_id, "
            "(SELECT ? as prod_quantity) as prod_quantity",
            (prod_name, from_loc, to_loc, quantity),
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
                "SELECT products.prod_id, location.loc_id, ? FROM products, location "
                "WHERE products.prod_name = ? AND location.loc_name = ?",
                (quantity, prod_name, location_name),
            )
        elif column_name == "from_loc_id":
            conn.execute(
                "INSERT INTO logistics (prod_id, from_loc_id, prod_quantity) "
                "SELECT products.prod_id, location.loc_id, ? FROM products, location "
                "WHERE products.prod_name = ? AND location.loc_name = ?",
                (quantity, prod_name, location_name),
            )
        
        # Use separate queries for each allowed operation to avoid f-string interpolation
        if operation == "-":
            conn.execute(
                "UPDATE products SET unallocated_quantity = unallocated_quantity - ? WHERE prod_name = ?",
                (quantity, prod_name),
            )
        elif operation == "+":
            conn.execute(
                "UPDATE products SET unallocated_quantity = unallocated_quantity + ? WHERE prod_name = ?",
                (quantity, prod_name),
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
def movement():
    match request.method:
        case "GET":
            with sqlite3.connect(DATABASE_NAME) as conn:
                logistics_data = conn.execute("SELECT * FROM logistics").fetchall()
                products = conn.execute(
                    "SELECT prod_id, prod_name, unallocated_quantity FROM products"
                ).fetchall()
                locations = conn.execute("SELECT loc_id, loc_name FROM location").fetchall()
                warehouse_summary = get_warehouse_data(conn, products, locations)
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
                try:
                    update_warehouse_data(conn)
                    return redirect(VIEWS["Logistics"])
                except ValidationError as e:
                    flash(str(e), 'error')
                    return redirect(VIEWS["Logistics"])


@app.route("/delete")
def delete():
    delete_record_type = request.args.get("type")

    with sqlite3.connect(DATABASE_NAME) as conn:
        try:
            match delete_record_type:
                case "product":
                    product_id = validate_id(request.args.get("prod_id"), "Product ID")
                    conn.execute("DELETE FROM products WHERE prod_id = ?", (product_id,))
                    return redirect(VIEWS["Stock"])

                case "location":
                    location_id = validate_id(request.args.get("loc_id"), "Location ID")
                    in_place = dict(
                        conn.execute(
                            "SELECT prod_id, SUM(prod_quantity) FROM logistics WHERE to_loc_id = ? GROUP BY prod_id",
                            (location_id,),
                        ).fetchall()
                    )
                    out_place = dict(
                        conn.execute(
                            "SELECT prod_id, SUM(prod_quantity) FROM logistics WHERE from_loc_id = ? GROUP BY prod_id",
                            (location_id,),
                        ).fetchall()
                    )

                    displaced_qty = in_place.copy()
                    for x in in_place:
                        if x in out_place:
                            displaced_qty[x] = displaced_qty[x] - out_place[x]

                    for products_ in displaced_qty:
                        conn.execute(
                            "UPDATE products SET unallocated_quantity = unallocated_quantity + ? WHERE prod_id = ?",
                            (displaced_qty[products_], products_),
                        )
                    conn.execute("DELETE FROM location WHERE loc_id = ?", (location_id,))
                    return redirect(VIEWS["Warehouses"])

                case _:
                    return redirect(VIEWS["Summary"])
        
        except ValidationError as e:
            flash(str(e), 'error')
            return redirect(VIEWS["Summary"])


@app.route("/edit", methods=["POST"])
def edit():
    edit_record_type = request.args.get("type")

    with sqlite3.connect(DATABASE_NAME) as conn:
        try:
            match edit_record_type:
                case "location":
                    loc_id = validate_id(request.form.get("loc_id"), "Location ID")
                    loc_name = validate_name(request.form.get("loc_name"), "Location name")
                    
                    conn.execute(
                        "UPDATE location SET loc_name = ? WHERE loc_id = ?", (loc_name, loc_id)
                    )
                    return redirect(VIEWS["Warehouses"])

                case "product":
                    prod_id = validate_id(request.form.get("prod_id"), "Product ID")
                    
                    prod_name_input = request.form.get("prod_name")
                    prod_quantity_input = request.form.get("prod_quantity")
                    
                    if prod_name_input:
                        prod_name = validate_name(prod_name_input, "Product name")
                        conn.execute(
                            "UPDATE products SET prod_name = ? WHERE prod_id = ?",
                            (prod_name, prod_id),
                        )
                    
                    if prod_quantity_input:
                        prod_quantity = validate_quantity(prod_quantity_input, "Product quantity")
                        old_prod_quantity = conn.execute(
                            "SELECT prod_quantity FROM products WHERE prod_id = ?", (prod_id,)
                        ).fetchone()[0]
                        conn.execute(
                            "UPDATE products SET prod_quantity = ?, unallocated_quantity =  unallocated_quantity + ? - ? WHERE prod_id = ?",
                            (prod_quantity, prod_quantity, old_prod_quantity, prod_id),
                        )

                    return redirect(VIEWS["Stock"])

                case _:
                    return redirect(VIEWS["Summary"])
        
        except ValidationError as e:
            flash(str(e), 'error')
            # Redirect based on the edit type
            if edit_record_type == "location":
                return redirect(VIEWS["Warehouses"])
            elif edit_record_type == "product":
                return redirect(VIEWS["Stock"])
            else:
                return redirect(VIEWS["Summary"])
        except sqlite3.IntegrityError:
            flash("Name already exists", 'error')
            # Redirect based on the edit type
            if edit_record_type == "location":
                return redirect(VIEWS["Warehouses"])
            elif edit_record_type == "product":
                return redirect(VIEWS["Stock"])
            else:
                return redirect(VIEWS["Summary"])


with app.app_context():
    app.init_db()
