import os
from flask import Flask, jsonify, abort
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

engine = create_engine(
    os.environ["DATABASE_URL"],
    pool_size=5,
    max_overflow=2,
    pool_timeout=10,
)

def row_to_dict(row):
    d = dict(row._mapping)
    if d.get("order_total") is not None:
        d["order_total"] = float(d["order_total"])
    if d.get("created_at") is not None:
        d["created_at"] = d["created_at"].isoformat()
    return d

@app.route("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return jsonify(status="ok", db="connected")
    except Exception as e:
        return jsonify(status="error", db=str(e)), 500

@app.route("/orders/by-email/<email>")
def orders_by_email(email):
    with engine.connect() as conn:
        rows = conn.execute(
            text("""SELECT id, customer_id, order_total, status, created_at
                     FROM orders WHERE customer_email = :email"""),
            {"email": email},
        ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.route("/orders/<int:order_id>")
def order_by_id(order_id):
    with engine.connect() as conn:
        row = conn.execute(
            text("""SELECT id, customer_id, customer_email, order_total, status, created_at
                     FROM orders WHERE id = :id"""),
            {"id": order_id},
        ).fetchone()
    if row is None:
        abort(404)
    return jsonify(row_to_dict(row))

@app.route("/reports/leaky-summary")
def leaky_summary():
    # FIXED: connection is now guaranteed to be returned to the pool via
    # the `with` block, even if an exception happens mid-query.
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM orders")).scalar()
    return jsonify(order_count=count)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
