# imports - standard imports
import json
import os
import sqlite3
import logging
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

# Configure security logger for input validation
security_logger = logging.getLogger('security.validation')
if not security_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    security_logger.addHandler(handler)
    security_logger.setLevel(logging.WARNING)


class ValidationError(Exception):
    """Custom exception for validation failures with user-friendly messages."""
    
    def __init__(self, field_name, user_message, internal_message=None):
        self.field_name = field_name
        self.user_message = user_message
        self.internal_message = internal_message or user_message
        super().__init__(self.user_message)


def validate_quantity(value, field_name="quantity", allow_zero=True, max_value=2_147_483_647):
    """
    Validate and convert a quantity input to a safe integer.
    
    Addresses CWE-20 by ensuring numeric inputs are:
    1. Non-null and non-empty
    2. Convertible to an integer
    3. Within acceptable bounds (non-negative)
    4. Appropriate for the business context
    
    Args:
        value: The raw input value to validate (typically from request.form)
        field_name: Human-readable field name for error messages
        allow_zero: Whether zero is a valid value (default True)
        max_value: Maximum acceptable value (default: max 32-bit signed int)
    
    Returns:
        int: The validated quantity as a safe integer
    
    Raises:
        ValidationError: If validation fails
    """
    
    # Check 1: Handle None/null values
    if value is None:
        security_logger.warning(f"Validation failed for {field_name}: null value")
        raise ValidationError(
            field_name=field_name,
            user_message=f"{field_name.replace('_', ' ').title()} is required",
            internal_message=f"Null value received for {field_name}"
        )
    
    # Check 2: Handle empty strings and whitespace
    if isinstance(value, str):
        stripped_value = value.strip()
        if not stripped_value:
            security_logger.warning(f"Validation failed for {field_name}: empty string")
            raise ValidationError(
                field_name=field_name,
                user_message=f"{field_name.replace('_', ' ').title()} is required",
                internal_message=f"Empty string received for {field_name}"
            )
        value = stripped_value
    
    # Check 3: Type conversion with strict validation
    try:
        if isinstance(value, str):
            # Reject float-like strings (e.g., "10.5", "1e5")
            if '.' in value or 'e' in value.lower():
                security_logger.warning(f"Validation failed for {field_name}: float-like string '{value}'")
                raise ValidationError(
                    field_name=field_name,
                    user_message=f"{field_name.replace('_', ' ').title()} must be a whole number",
                    internal_message=f"Float-like string '{value}' received for {field_name}"
                )
            
            int_value = int(value)
        elif isinstance(value, float):
            # Accept floats that are whole numbers (e.g., 10.0)
            if value != int(value):
                security_logger.warning(f"Validation failed for {field_name}: non-integer float {value}")
                raise ValidationError(
                    field_name=field_name,
                    user_message=f"{field_name.replace('_', ' ').title()} must be a whole number",
                    internal_message=f"Non-integer float {value} received for {field_name}"
                )
            int_value = int(value)
        elif isinstance(value, int) and not isinstance(value, bool):
            int_value = value
        else:
            security_logger.warning(f"Validation failed for {field_name}: invalid type {type(value).__name__}")
            raise ValidationError(
                field_name=field_name,
                user_message=f"{field_name.replace('_', ' ').title()} must be a valid number",
                internal_message=f"Unexpected type {type(value).__name__} for {field_name}"
            )
    
    except ValueError:
        security_logger.warning(f"Validation failed for {field_name}: conversion failed for '{value}'")
        raise ValidationError(
            field_name=field_name,
            user_message=f"{field_name.replace('_', ' ').title()} must be a valid whole number",
            internal_message=f"Integer conversion failed for {field_name}: {repr(value)}"
        )
    
    # Check 4: Range validation
    if int_value < 0:
        security_logger.warning(f"Validation failed for {field_name}: negative value {int_value}")
        raise ValidationError(
            field_name=field_name,
            user_message=f"{field_name.replace('_', ' ').title()} cannot be negative",
            internal_message=f"Negative value {int_value} received for {field_name}"
        )
    
    if not allow_zero and int_value == 0:
        security_logger.warning(f"Validation failed for {field_name}: zero not allowed")
        raise ValidationError(
            field_name=field_name,
            user_message=f"{field_name.replace('_', ' ').title()} must be greater than zero",
            internal_message=f"Zero value received for {field_name} where zero not allowed"
        )
    
    if int_value > max_value:
        security_logger.warning(f"Validation failed for {field_name}: value {int_value} exceeds maximum")
        raise ValidationError(
            field_name=field_name,
            user_message=f"{field_name.replace('_', ' ').title()} exceeds maximum allowed value",
            internal_message=f"Value {int_value} exceeds maximum {max_value} for {field_name}"
        )
    
    # Success - log and return validated value
    security_logger.debug(f"Validation passed for {field_name}: {int_value}")
    return int_value


app = Flask(__name__)

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
            prod_name = request.form.get("prod_name", "").strip()
            
            # Validate product name
            if not prod_name or prod_name in EMPTY_SYMBOLS:
                flash("Product name is required", "error")
                products = conn.execute("SELECT * FROM products").fetchall()
                return render_template(
                    "product.jinja",
                    link=VIEWS,
                    products=products,
                    title="Stock",
                )
            
            try:
                # SECURITY FIX: Validate quantity input before database insertion
                # Allow zero for new products (can represent pre-listing items)
                quantity = validate_quantity(
                    request.form.get("prod_quantity"), 
                    field_name="product_quantity", 
                    allow_zero=True
                )
                
                # Safe to insert validated data into database
                conn.execute(
                    "INSERT INTO products (prod_name, prod_quantity) VALUES (?, ?)",
                    (prod_name, quantity),
                )
                return redirect(VIEWS["Stock"])
                
            except ValidationError as e:
                # Log security event and show user-friendly error
                security_logger.warning(
                    f"Validation error in product creation: {e.internal_message} | "
                    f"IP: {request.remote_addr} | User-Agent: {request.headers.get('User-Agent', 'Unknown')}"
                )
                flash(e.user_message, "error")

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
            warehouse_name = request.form["warehouse_name"]

            if warehouse_name not in EMPTY_SYMBOLS:
                conn.execute("INSERT INTO location (loc_name) VALUES (?)", (warehouse_name,))
                return redirect(VIEWS["Warehouses"])

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
    prod_name = request.form.get("prod_name", "").strip()
    from_loc = request.form.get("from_loc", "").strip()  
    to_loc = request.form.get("to_loc", "").strip()
    
    # SECURITY FIX: Validate quantity input before warehouse operations
    # Movement quantities must be positive (can't move zero or negative items)
    try:
        quantity = validate_quantity(
            request.form.get("quantity"),
            field_name="movement_quantity", 
            allow_zero=False  # Logistics movements must be positive
        )
    except ValidationError as e:
        # Log and re-raise validation error to be handled by calling function
        security_logger.warning(
            f"Validation error in warehouse operation: {e.internal_message} | "
            f"IP: {request.remote_addr} | Product: {prod_name}"
        )
        raise

    # if no 'from loc' is given, that means the product is being shipped to a warehouse (init condition)
    if from_loc in EMPTY_SYMBOLS:
        column_name = "to_loc_id"
        operation = "-"
        location_name = to_loc
        update_unallocated_quantity = True

    # To Location wasn't specified, will be unallocated
    elif to_loc in EMPTY_SYMBOLS:
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
                    # Handle validation error from warehouse operations
                    flash(e.user_message, "error")
                    return redirect(VIEWS["Logistics"])


@app.route("/delete")
def delete():
    delete_record_type = request.args.get("type")

    with sqlite3.connect(DATABASE_NAME) as conn:
        match delete_record_type:
            case "product":
                product_id = request.args.get("prod_id")
                if product_id:
                    conn.execute("DELETE FROM products WHERE prod_id = ?", product_id)
                return redirect(VIEWS["Stock"])

            case "location":
                location_id = request.args.get("loc_id")
                if location_id:
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
                    conn.execute("DELETE FROM location WHERE loc_id = ?", location_id)
                return redirect(VIEWS["Warehouses"])

            case _:
                return redirect(VIEWS["Summary"])


@app.route("/edit", methods=["POST"])
def edit():
    edit_record_type = request.args.get("type")

    with sqlite3.connect(DATABASE_NAME) as conn:
        match edit_record_type:
            case "location":
                loc_id, loc_name = request.form["loc_id"], request.form["loc_name"]
                if loc_name:
                    conn.execute(
                        "UPDATE location SET loc_name = ? WHERE loc_id = ?", (loc_name, loc_id)
                    )
                return redirect(VIEWS["Warehouses"])

            case "product":
                prod_id = request.form.get("prod_id")
                prod_name = request.form.get("prod_name", "").strip()
                
                # Validate product ID
                if not prod_id:
                    flash("Product ID is required", "error")
                    return redirect(VIEWS["Stock"])

                try:
                    # Update product name if provided
                    if prod_name:
                        conn.execute(
                            "UPDATE products SET prod_name = ? WHERE prod_id = ?",
                            (prod_name, prod_id),
                        )
                    
                    # SECURITY FIX: Validate quantity input before database update
                    # Allow zero for product updates (can represent sold-out stock)
                    prod_quantity_raw = request.form.get("prod_quantity")
                    if prod_quantity_raw:  # Only validate if quantity is being updated
                        prod_quantity = validate_quantity(
                            prod_quantity_raw,
                            field_name="product_quantity",
                            allow_zero=True  # Stock can be updated to zero
                        )
                        
                        # Update quantity with validated input
                        old_prod_quantity = conn.execute(
                            "SELECT prod_quantity FROM products WHERE prod_id = ?", (prod_id,)
                        ).fetchone()[0]
                        conn.execute(
                            "UPDATE products SET prod_quantity = ?, unallocated_quantity = unallocated_quantity + ? - ? WHERE prod_id = ?",
                            (prod_quantity, prod_quantity, old_prod_quantity, prod_id),
                        )

                    return redirect(VIEWS["Stock"])
                    
                except ValidationError as e:
                    # Log security event and show user-friendly error
                    security_logger.warning(
                        f"Validation error in product edit: {e.internal_message} | "
                        f"IP: {request.remote_addr} | Product ID: {prod_id}"
                    )
                    flash(e.user_message, "error")
                    return redirect(VIEWS["Stock"])

            case _:
                return redirect(VIEWS["Summary"])


with app.app_context():
    app.init_db()
