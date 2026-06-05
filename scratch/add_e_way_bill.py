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
    cursor.execute("ALTER TABLE sales ADD COLUMN e_way_bill_no VARCHAR(100) NULL AFTER dispatched_through;")
    print("Added e_way_bill_no to sales table")
except Exception as e:
    print(f"Error adding e_way_bill_no to sales: {e}")

conn.commit()
conn.close()
