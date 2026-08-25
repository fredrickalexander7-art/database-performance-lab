import os, random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from psycopg2.extras import execute_values
from faker import Faker

load_dotenv()
engine = create_engine(os.environ["DATABASE_URL"])
fake = Faker()

N_CUSTOMERS = 10_000
N_ORDERS = 500_000
BATCH = 10_000

DDL = """
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    customer_email TEXT NOT NULL,
    order_total NUMERIC(10,2) NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now()
);
"""

STATUSES = ["pending", "paid", "shipped", "delivered", "refunded"]

def main():
    with engine.begin() as conn:
        print("Creating schema...")
        conn.execute(text(DDL))

    raw = engine.raw_connection()
    try:
        cur = raw.cursor()

        print(f"Seeding {N_CUSTOMERS} customers...")
        customers = [
            (fake.name(), f"{fake.user_name()}{i}@{fake.free_email_domain()}")
            for i in range(N_CUSTOMERS)
        ]
        execute_values(cur, "INSERT INTO customers (name, email) VALUES %s", customers, page_size=1000)
        raw.commit()

        cur.execute("SELECT id, email FROM customers")
        customer_rows = cur.fetchall()

        print(f"Seeding {N_ORDERS} orders...")
        batch = []
        inserted = 0
        for i in range(N_ORDERS):
            cust_id, cust_email = random.choice(customer_rows)
            batch.append((
                cust_id,
                cust_email,
                round(random.uniform(5, 500), 2),
                random.choice(STATUSES),
                datetime.now() - timedelta(days=random.randint(0, 730)),
            ))
            if len(batch) == BATCH:
                execute_values(
                    cur,
                    "INSERT INTO orders (customer_id, customer_email, order_total, status, created_at) VALUES %s",
                    batch, page_size=1000,
                )
                raw.commit()
                inserted += len(batch)
                print(f"  {inserted}/{N_ORDERS}")
                batch = []
        if batch:
            execute_values(
                cur,
                "INSERT INTO orders (customer_id, customer_email, order_total, status, created_at) VALUES %s",
                batch, page_size=1000,
            )
            raw.commit()

        cur.execute("SELECT count(*) FROM customers")
        c = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM orders")
        o = cur.fetchone()[0]
        print(f"Done. customers={c} orders={o}")
    finally:
        raw.close()

if __name__ == "__main__":
    main()
