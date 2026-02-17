# imports - standard imports
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

# imports - third party imports
from flask import Flask, redirect, render_template, request

DATABASE_NAME = "inventory.sqlite"
_DATABASE_PATH = Path(__file__).parent.parent / DATABASE_NAME
VIEWS = {
    "Summary": "/",
    "Stock": "/product",
    "Warehouses": "/location",
    "Logistics": "/movement",
}
EMPTY_SYMBOLS = {"", " ", None}

# Validation constants for numeric fields
MAX_QUANTITY = 2_147_483_647  # SQLite INTEGER max value


def validate_positive_integer(value: str, field_name: str = "quantity", max_value: int = MAX_QUANTITY) -> int:
    """
    Validate that a string value represents a positive integer within acceptable range.
    
    Args:
        value: The string value to validate
        field_name: Name of the field for error messages
        max_value: Maximum allowed value (default: SQLite INTEGER max)
    
    Returns:
        The validated integer value
    
    Raises:
        ValueError: If the value is not a valid positive integer within range
    """
    if value is None or str(value).strip() == "":
        raise ValueError(f"{field_name} is required")
    
    value_str = str(value).strip()
    
    # Check for valid integer format (no decimals, no special characters)
    if not value_str.lstrip('-').isdigit():
        raise ValueError(f"{field_name} must be a valid integer")
    
    try:
        int_value = int(value_str)
    except (ValueError, OverflowError):
        raise ValueError(f"{field_name} must be a valid integer")
    
    if int_value <= 0:
        raise ValueError(f"{field_name} must be a positive integer greater than zero")
    
    if int_value > max_value:
        raise ValueError(f"{field_name} exceeds maximum allowed value of {max_value}")
    
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
    error_message = None
    with sqlite3.connect(DATABASE_NAME) as conn:
        if request.method == "POST":
            prod_name = request.form.get("prod_name", "")
            quantity_str = request.form.get("prod_quantity", "")
            
            if prod_name not in EMPTY_SYMBOLS and quantity_str not in EMPTY_SYMBOLS:
                try:
                    validated_quantity = validate_positive_integer(quantity_str, "Product quantity")
                    conn.execute(
                        "INSERT INTO products (prod_name, prod_quantity) VALUES (?, ?)",
                        (prod_name, validated_quantity),
                    )
                    return redirect(VIEWS["Stock"])
                except ValueError as e:
                    error_message = str(e)

        products = conn.execute("SELECT * FROM products").fetchall()

    return render_template(
        "product.jinja",
        link=VIEWS,
        products=products,
        title="Stock",
        error=error_message,
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
    """
    Update warehouse data based on logistics movement.
    
    Raises:
        ValueError: If quantity validation fails
    """
    # Define secure whitelisted values for SQL query construction
    ALLOWED_COLUMNS = {"to_loc_id", "from_loc_id"}
    ALLOWED_OPERATIONS = {"+", "-"}
    
    update_unallocated_quantity = False
    prod_name = request.form.get("prod_name", "")
    from_loc = request.form.get("from_loc", "")
    to_loc = request.form.get("to_loc", "")
    quantity_str = request.form.get("quantity", "")
    
    # Validate quantity is a positive integer
    quantity = validate_positive_integer(quantity_str, "Movement quantity")

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
                except ValueError as e:
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
                        error=str(e),
                    )


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
                prod_id = request.form.get("prod_id", "")
                prod_name = request.form.get("prod_name", "")
                prod_quantity_str = request.form.get("prod_quantity", "")

                if prod_name:
                    conn.execute(
                        "UPDATE products SET prod_name = ? WHERE prod_id = ?",
                        (prod_name, prod_id),
                    )
                if prod_quantity_str and prod_quantity_str not in EMPTY_SYMBOLS:
                    try:
                        validated_quantity = validate_positive_integer(prod_quantity_str, "Product quantity")
                        old_prod_quantity = conn.execute(
                            "SELECT prod_quantity FROM products WHERE prod_id = ?", (prod_id,)
                        ).fetchone()[0]
                        conn.execute(
                            "UPDATE products SET prod_quantity = ?, unallocated_quantity =  unallocated_quantity + ? - ? WHERE prod_id = ?",
                            (validated_quantity, validated_quantity, old_prod_quantity, prod_id),
                        )
                    except ValueError:
                        # Invalid quantity provided, skip the quantity update
                        pass

                return redirect(VIEWS["Stock"])

            case _:
                return redirect(VIEWS["Summary"])


with app.app_context():
    app.init_db()
