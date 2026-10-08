"""
retail.py
المتجر الطبي: منتجات عناية شخصية/مستحضرات تبيعها الصيدلية بجانب الأدوية،
منفصلة عن دورة الروشتة (prescriptions.py) لأنها مش محتاجة تسعير أو تحقق
من توفر - المريض بيشوف السعر والمخزون المتاح مباشرة ويطلب.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_role
from database import add_notification, get_db
from schemas import ProductIn, RetailOrderIn, RetailOrderStatusIn

router = APIRouter(prefix="/api", tags=["retail"])


# =========================================================
#              منتجات الصيدلية (إدارة من الصيدلية)
# =========================================================

@router.get("/pharmacy/products")
def my_products(pharmacy=Depends(require_role(["pharmacy"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM pharmacy_products WHERE pharmacy_id = ? ORDER BY created_at DESC",
            (pharmacy["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.post("/pharmacy/products", status_code=201)
def create_product(payload: ProductIn, pharmacy=Depends(require_role(["pharmacy"]))):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO pharmacy_products (pharmacy_id, name, price, description, image_base64, is_active)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pharmacy["id"], payload.name, payload.price, payload.description,
             payload.image_base64, int(payload.is_active)),
        )
    return {"message": "تم إضافة المنتج", "product_id": cur.lastrowid}


@router.put("/pharmacy/products/{product_id}")
def update_product(product_id: int, payload: ProductIn, pharmacy=Depends(require_role(["pharmacy"]))):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM pharmacy_products WHERE id = ? AND pharmacy_id = ?",
            (product_id, pharmacy["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "المنتج غير موجود")
        conn.execute(
            """UPDATE pharmacy_products SET name = ?, price = ?, description = ?,
               image_base64 = ?, is_active = ? WHERE id = ?""",
            (payload.name, payload.price, payload.description, payload.image_base64,
             int(payload.is_active), product_id),
        )
    return {"message": "تم التحديث"}


@router.delete("/pharmacy/products/{product_id}")
def delete_product(product_id: int, pharmacy=Depends(require_role(["pharmacy"]))):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM pharmacy_products WHERE id = ? AND pharmacy_id = ?",
            (product_id, pharmacy["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "المنتج غير موجود")
        # لو المنتج ده اتباع قبل كده، منمسحوش عشان الطلبات القديمة تفضل شايفة اسمه -
        # بس نعطّله (is_active=0) بدل الحذف الفعلي.
        used = conn.execute(
            "SELECT id FROM retail_order_items WHERE product_id = ? LIMIT 1", (product_id,)
        ).fetchone()
        if used:
            conn.execute("UPDATE pharmacy_products SET is_active = 0 WHERE id = ?", (product_id,))
            return {"message": "المنتج ده اتباع قبل كده، تم إخفاؤه من المتجر بدل حذفه نهائيًا"}
        conn.execute("DELETE FROM pharmacy_products WHERE id = ?", (product_id,))
    return {"message": "تم حذف المنتج"}


# =========================================================
#         تصفح منتجات صيدلية معينة (عام - للمريض)
# =========================================================

@router.get("/pharmacies/search")
def search_pharmacies(country: Optional[str] = None, city: Optional[str] = None):
    query = """SELECT p.user_id, p.name, p.address, u.city, u.country
               FROM pharmacies p JOIN users u ON u.id = p.user_id
               WHERE u.is_active = 1"""
    params = []
    if country:
        query += " AND u.country = ?"
        params.append(country)
    if city:
        query += " AND u.city = ?"
        params.append(city)
    query += " ORDER BY p.name"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/pharmacies/{pharmacy_id}/products")
def browse_products(pharmacy_id: int):
    with get_db() as conn:
        pharmacy = conn.execute(
            "SELECT name FROM pharmacies WHERE user_id = ?", (pharmacy_id,)
        ).fetchone()
        if not pharmacy:
            raise HTTPException(404, "الصيدلية غير موجودة")
        rows = conn.execute(
            "SELECT id, name, price, description, image_base64 FROM pharmacy_products "
            "WHERE pharmacy_id = ? AND is_active = 1 ORDER BY name",
            (pharmacy_id,),
        ).fetchall()
    return {"pharmacy_name": pharmacy["name"], "products": [dict(r) for r in rows]}


# =========================================================
#                  طلب المتجر (من المريض)
# =========================================================

@router.post("/retail-orders", status_code=201)
def place_retail_order(payload: RetailOrderIn, patient=Depends(require_role(["patient"]))):
    if not payload.items:
        raise HTTPException(400, "السلة فاضية")

    with get_db() as conn:
        pharmacy = conn.execute(
            "SELECT name FROM pharmacies WHERE user_id = ?", (payload.pharmacy_id,)
        ).fetchone()
        if not pharmacy:
            raise HTTPException(404, "الصيدلية غير موجودة")

        total = 0.0
        resolved = []
        for item in payload.items:
            product = conn.execute(
                "SELECT id, name, price FROM pharmacy_products "
                "WHERE id = ? AND pharmacy_id = ? AND is_active = 1",
                (item.product_id, payload.pharmacy_id),
            ).fetchone()
            if not product:
                raise HTTPException(400, f"منتج غير متاح (id={item.product_id})")
            total += product["price"] * item.quantity
            resolved.append((product, item.quantity))

        cur = conn.execute(
            """INSERT INTO retail_orders (patient_id, pharmacy_id, total_price, notes)
               VALUES (?, ?, ?, ?)""",
            (patient["id"], payload.pharmacy_id, total, payload.notes),
        )
        order_id = cur.lastrowid
        for product, qty in resolved:
            conn.execute(
                """INSERT INTO retail_order_items (order_id, product_id, product_name, unit_price, quantity)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_id, product["id"], product["name"], product["price"], qty),
            )

        add_notification(conn, payload.pharmacy_id, "طلب متجر جديد",
                         f"طلب جديد بقيمة {total:g} جنيه")

    return {"message": "تم إرسال الطلب", "order_id": order_id, "total_price": total}


@router.get("/retail-orders/mine")
def my_retail_orders(patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT ro.*, p.name AS pharmacy_name FROM retail_orders ro
               JOIN pharmacies p ON p.user_id = ro.pharmacy_id
               WHERE ro.patient_id = ? ORDER BY ro.created_at DESC""",
            (patient["id"],),
        ).fetchall()
        results = []
        for r in rows:
            row = dict(r)
            items = conn.execute(
                "SELECT product_name, unit_price, quantity FROM retail_order_items WHERE order_id = ?",
                (row["id"],),
            ).fetchall()
            row["items"] = [dict(i) for i in items]
            results.append(row)
    return results


@router.put("/retail-orders/{order_id}/cancel")
def cancel_retail_order(order_id: int, patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        row = conn.execute(
            "SELECT status FROM retail_orders WHERE id = ? AND patient_id = ?",
            (order_id, patient["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "الطلب غير موجود")
        if row["status"] != "placed":
            raise HTTPException(400, "الطلب بدأ تجهيزه بالفعل، اتصل بالصيدلية")
        conn.execute("UPDATE retail_orders SET status = 'cancelled' WHERE id = ?", (order_id,))
    return {"message": "تم إلغاء الطلب"}


# =========================================================
#               إدارة طلبات المتجر (من الصيدلية)
# =========================================================

@router.get("/pharmacy/retail-orders")
def pharmacy_retail_orders(status: Optional[str] = None, pharmacy=Depends(require_role(["pharmacy"]))):
    query = """SELECT ro.*, pt.full_name AS patient_name, u.phone AS patient_phone,
                      u.city AS patient_city, u.latitude AS patient_lat, u.longitude AS patient_lng
               FROM retail_orders ro
               JOIN patients pt ON pt.user_id = ro.patient_id
               JOIN users u ON u.id = ro.patient_id
               WHERE ro.pharmacy_id = ?"""
    params = [pharmacy["id"]]
    if status:
        query += " AND ro.status = ?"
        params.append(status)
    query += " ORDER BY ro.created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            row = dict(r)
            items = conn.execute(
                "SELECT product_name, unit_price, quantity FROM retail_order_items WHERE order_id = ?",
                (row["id"],),
            ).fetchall()
            row["items"] = [dict(i) for i in items]
            results.append(row)
    return results


@router.put("/pharmacy/retail-orders/{order_id}/status")
def update_retail_order_status(order_id: int, payload: RetailOrderStatusIn, pharmacy=Depends(require_role(["pharmacy"]))):
    valid = {"preparing", "out_for_delivery", "delivered", "cancelled"}
    if payload.status not in valid:
        raise HTTPException(400, f"الحالة لازم تكون واحدة من: {', '.join(valid)}")

    with get_db() as conn:
        row = conn.execute(
            "SELECT patient_id FROM retail_orders WHERE id = ? AND pharmacy_id = ?",
            (order_id, pharmacy["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "الطلب غير موجود")

        conn.execute("UPDATE retail_orders SET status = ? WHERE id = ?", (payload.status, order_id))

        labels = {"preparing": "طلب المتجر قيد التجهيز", "out_for_delivery": "طلب المتجر خرج للتوصيل",
                  "delivered": "تم تسليم طلب المتجر", "cancelled": "تم إلغاء طلب المتجر"}
        add_notification(conn, row["patient_id"], labels[payload.status], "")

    return {"message": "تم التحديث"}
