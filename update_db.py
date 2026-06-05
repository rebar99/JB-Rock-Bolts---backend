import pymysql

conn = pymysql.connect(
    host='127.0.0.1',
    user='root',
    password='Deepu412003@',
    database='jbrockbolts_db',
    port=3306
)

cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE purchase_orders ADD COLUMN client_id INT NULL;")
    print("Added client_id to purchase_orders")
except Exception as e:
    print(f"Error adding client_id to purchase_orders: {e}")

try:
    cursor.execute("ALTER TABLE purchase_orders ADD COLUMN project_id INT NULL;")
    print("Added project_id to purchase_orders")
except Exception as e:
    print(f"Error adding project_id to purchase_orders: {e}")
    
try:
    cursor.execute("ALTER TABLE records ADD COLUMN client_id INT NULL;")
    print("Added client_id to records")
except Exception as e:
    print(f"Error adding client_id to records: {e}")

try:
    cursor.execute("ALTER TABLE purchase_orders ADD CONSTRAINT fk_purchase_orders_client_id FOREIGN KEY (client_id) REFERENCES clients(id);")
    print("Added fk_purchase_orders_client_id")
except Exception as e:
    print(f"Error adding fk_purchase_orders_client_id: {e}")

try:
    cursor.execute("ALTER TABLE purchase_orders ADD CONSTRAINT fk_purchase_orders_project_id FOREIGN KEY (project_id) REFERENCES projects(id);")
    print("Added fk_purchase_orders_project_id")
except Exception as e:
    print(f"Error adding fk_purchase_orders_project_id: {e}")

try:
    cursor.execute("ALTER TABLE records ADD CONSTRAINT fk_records_client_id FOREIGN KEY (client_id) REFERENCES clients(id);")
    print("Added fk_records_client_id")
except Exception as e:
    print(f"Error adding fk_records_client_id: {e}")

conn.commit()
conn.close()
