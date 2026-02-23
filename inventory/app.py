# imports - standard imports
import json
import os
import sqlite3
import logging
from collections import defaultdict
from pathlib import Path

# imports - third party imports
from flask import Flask, redirect, render_template, request, flash

# imports - local imports
from validators import (
    ValidationError,
    validate_product_id,
    validate_location_id, 
    validate_quantity,
    get_form_int,
    get_query_int
)

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
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if os.environ.get("FLASK_DEBUG") == "1":
    app.config.update(TEMPLATES_AUTO_RELOAD=True)
    DATABASE_NAME = _DATABASE_PATH.resolve()
else:
    DATABASE_NAME = os.environ.get("DATABASE_NAME") or _DATABASE_PATH.resolve()


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(ValidationError)
def handle_validation_error(error):
    """
    Global error handler for validation errors.
    Ensures consistent error handling across all routes.
    """
    flash(error.message, 'error')
    logger.warning(f"Validation error: {error.field} - {error.message}")
    # Redirect to a safe page or back to referrer
    return redirect(request.referrer or VIEWS["Summary"])


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
                prod_name = request.form.get("prod_name", "").strip()
                prod_quantity = validate_quantity(
                    request.form.get("prod_quantity"),
                    allow_none=False,
                    allow_zero=True  # Allow zero initial quantity
                )

                # Validate product name (string validation)
                if not prod_name or prod_name in EMPTY_SYMBOLS:
                    raise ValidationError('prod_name', 'Product name is required')
                if len(prod_name) > 100:
                    raise ValidationError('prod_name', 'Product name must be 100 characters or less')

                # Insert product with validated data
                conn.execute(
                    "INSERT INTO products (prod_name, prod_quantity) VALUES (?, ?)",
                    (prod_name, prod_quantity),
                )
                
                flash(f'Product "{prod_name}" added successfully with quantity {prod_quantity}!', 'success')
                logger.info(f"Product created: {prod_name}, quantity: {prod_quantity}")
                return redirect(VIEWS["Stock"])
                
            except ValidationError as e:
                # ValidationError will be handled by global error handler
                raise
            except Exception as e:
                conn.rollback()
                logger.error(f"Error creating product: {e}")
                flash('An error occurred while adding the product.', 'error')
                return redirect(VIEWS["Stock"])

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
    """
    Process warehouse inventory update with proper input validation.
    
    FIXED: Now validates quantity and other inputs before database operations.
    """
    # Validate quantity input first
    quantity = validate_quantity(
        request.form.get("quantity"),
        allow_none=False,
        allow_zero=False  # Must transfer at least 1 item
    )
    
    # Get other form data
    prod_name = request.form.get("prod_name", "").strip()
    from_loc = request.form.get("from_loc", "").strip() 
    to_loc = request.form.get("to_loc", "").strip()
    
    # Validate product name
    if not prod_name or prod_name in EMPTY_SYMBOLS:
        raise ValidationError('prod_name', 'Product name is required')
    
    # Validate that at least one location is provided
    if (not from_loc or from_loc in EMPTY_SYMBOLS) and (not to_loc or to_loc in EMPTY_SYMBOLS):
        raise ValidationError('location', 'At least one location (from or to) must be specified')
    
    # Define secure whitelisted values for SQL query construction
    ALLOWED_COLUMNS = {"to_loc_id", "from_loc_id"}
    ALLOWED_OPERATIONS = {"+", "-"}
    
    update_unallocated_quantity = False

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
                    flash('Warehouse data updated successfully!', 'success')
                    return redirect(VIEWS["Logistics"])
                except ValidationError as e:
                    # ValidationError will be handled by global error handler
                    raise
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Error updating warehouse: {e}")
                    flash('An error occurred while updating warehouse data.', 'error')
                    return redirect(VIEWS["Logistics"])


@app.route("/delete")
def delete():
    """
    Delete a product or location with proper input validation.
    
    FIXED: Added validation for prod_id and loc_id query parameters.
    
    Security Note: In production, DELETE operations should use POST/DELETE methods
    and include CSRF protection.
    """
    delete_record_type = request.args.get("type")

    with sqlite3.connect(DATABASE_NAME) as conn:
        try:
            match delete_record_type:
                case "product":
                    product_id = validate_product_id(
                        request.args.get("prod_id"),
                        allow_none=True
                    )
                    
                    if product_id is None:
                        raise ValidationError('prod_id', 'Product ID is required for deletion')
                    
                    # Verify product exists before deletion
                    existing_product = conn.execute(
                        "SELECT prod_name FROM products WHERE prod_id = ?", 
                        (product_id,)
                    ).fetchone()
                    
                    if not existing_product:
                        raise ValidationError('prod_id', f'Product with ID {product_id} not found')
                    
                    # Check for dependencies (logistics records)
                    logistics_count = conn.execute(
                        "SELECT COUNT(*) FROM logistics WHERE prod_id = ?",
                        (product_id,)
                    ).fetchone()[0]
                    
                    if logistics_count > 0:
                        raise ValidationError(
                            'prod_id',
                            f'Cannot delete product. It has {logistics_count} logistics record(s). Remove logistics records first.'
                        )
                    
                    conn.execute("DELETE FROM products WHERE prod_id = ?", (product_id,))
                    flash(f'Product "{existing_product[0]}" deleted successfully!', 'success')
                    logger.info(f"Product deleted: ID {product_id}")
                    return redirect(VIEWS["Stock"])

                case "location":
                    location_id = validate_location_id(
                        request.args.get("loc_id"),
                        allow_none=True
                    )
                    
                    if location_id is None:
                        raise ValidationError('loc_id', 'Location ID is required for deletion')
                    
                    # Verify location exists
                    existing_location = conn.execute(
                        "SELECT loc_name FROM location WHERE loc_id = ?",
                        (location_id,)
                    ).fetchone()
                    
                    if not existing_location:
                        raise ValidationError('loc_id', f'Location with ID {location_id} not found')
                    
                    # Process inventory displacement with validated location_id
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

                    for product_id in displaced_qty:
                        if displaced_qty[product_id] > 0:  # Only update if there's actually displaced quantity
                            conn.execute(
                                "UPDATE products SET unallocated_quantity = unallocated_quantity + ? WHERE prod_id = ?",
                                (displaced_qty[product_id], product_id),
                            )
                    
                    conn.execute("DELETE FROM location WHERE loc_id = ?", (location_id,))
                    flash(f'Location "{existing_location[0]}" deleted successfully!', 'success')
                    logger.info(f"Location deleted: ID {location_id}")
                    return redirect(VIEWS["Warehouses"])

                case _:
                    flash('Invalid delete operation type.', 'error')
                    return redirect(VIEWS["Summary"])
                    
        except ValidationError as e:
            # ValidationError will be handled by global error handler
            raise
        except Exception as e:
            conn.rollback()
            logger.error(f"Error during deletion: {e}")
            flash('An error occurred during deletion.', 'error')
            return redirect(VIEWS["Summary"])


@app.route("/edit", methods=["POST"])
def edit():
    """
    Edit inventory record with proper input validation.
    
    FIXED: Added validation for loc_id, prod_id, and prod_quantity.
    """
    edit_record_type = request.args.get("type")

    with sqlite3.connect(DATABASE_NAME) as conn:
        try:
            match edit_record_type:
                case "location":
                    loc_id = validate_location_id(request.form.get("loc_id"))
                    loc_name = request.form.get("loc_name", "").strip()
                    
                    if not loc_name or loc_name in EMPTY_SYMBOLS:
                        raise ValidationError('loc_name', 'Location name is required')
                    if len(loc_name) > 100:
                        raise ValidationError('loc_name', 'Location name must be 100 characters or less')
                    
                    # Verify location exists before updating
                    existing_location = conn.execute(
                        "SELECT loc_name FROM location WHERE loc_id = ?",
                        (loc_id,)
                    ).fetchone()
                    
                    if not existing_location:
                        raise ValidationError('loc_id', f'Location with ID {loc_id} not found')
                    
                    conn.execute(
                        "UPDATE location SET loc_name = ? WHERE loc_id = ?", 
                        (loc_name, loc_id)
                    )
                    
                    flash(f'Location updated successfully!', 'success')
                    logger.info(f"Location updated: ID {loc_id}, new name: {loc_name}")
                    return redirect(VIEWS["Warehouses"])

                case "product":
                    prod_id = validate_product_id(request.form.get("prod_id"))
                    prod_name = request.form.get("prod_name", "").strip()
                    prod_quantity_raw = request.form.get("prod_quantity", "").strip()
                    
                    # Verify product exists before updating
                    existing_product = conn.execute(
                        "SELECT prod_name, prod_quantity FROM products WHERE prod_id = ?",
                        (prod_id,)
                    ).fetchone()
                    
                    if not existing_product:
                        raise ValidationError('prod_id', f'Product with ID {prod_id} not found')
                    
                    # Update product name if provided
                    if prod_name and prod_name not in EMPTY_SYMBOLS:
                        if len(prod_name) > 100:
                            raise ValidationError('prod_name', 'Product name must be 100 characters or less')
                        
                        conn.execute(
                            "UPDATE products SET prod_name = ? WHERE prod_id = ?",
                            (prod_name, prod_id),
                        )
                    
                    # Update product quantity if provided
                    if prod_quantity_raw and prod_quantity_raw not in EMPTY_SYMBOLS:
                        prod_quantity = validate_quantity(
                            prod_quantity_raw,
                            allow_zero=True  # Allow zero quantity
                        )
                        
                        old_prod_quantity = existing_product[1]  # From the query above
                        quantity_difference = prod_quantity - old_prod_quantity
                        
                        conn.execute(
                            "UPDATE products SET prod_quantity = ?, unallocated_quantity = unallocated_quantity + ? WHERE prod_id = ?",
                            (prod_quantity, quantity_difference, prod_id),
                        )
                    
                    flash(f'Product updated successfully!', 'success')
                    logger.info(f"Product updated: ID {prod_id}")
                    return redirect(VIEWS["Stock"])

                case _:
                    flash('Invalid edit operation type.', 'error')
                    return redirect(VIEWS["Summary"])
                    
        except ValidationError as e:
            # ValidationError will be handled by global error handler
            raise
        except Exception as e:
            conn.rollback()
            logger.error(f"Error during edit: {e}")
            flash('An error occurred during the edit operation.', 'error')
            return redirect(request.referrer or VIEWS["Summary"])


with app.app_context():
    app.init_db()
