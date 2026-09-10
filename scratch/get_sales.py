import pymysql
conn = pymysql.connect(host='localhost', user='root', password='Deepu412003@', db='jbrockbolts_db')
with conn.cursor() as c:
    c.execute("SELECT invoice_date, invoice_number FROM work_order_sales LIMIT 5")
    print("WO:", c.fetchall())
    c.execute("SELECT invoice_date, invoice_number FROM sales LIMIT 5")
    print("PO:", c.fetchall())
