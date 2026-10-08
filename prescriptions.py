"""
prescriptions.py
دورة الروشتة كاملة:
  1) الطبيب يكتب روشتة إلكترونية لمريضه (أو المريض يرفع صورة روشتة ورقية)
  2) إرسالها لأقرب الصيدليات (حسب إحداثيات GPS) عشان تسعّرها وتأكد توفر الأدوية
  3) الصيدلية بترد بسعر إجمالي وتوفر الأصناف من عدمه
  4) المريض يشوف كل العروض ويختار الصيدلية اللي هيصرف منها

ملحوظة: التوصيل الفعلي (تعيين مندوب/تتبع) هيتبني في مرحلة لاحقة فوق نفس
جدول prescription_requests بمجرد ما المريض يختار صيدلية.
"""

import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_role
from database import add_notification, get_db
from schemas import (
    ElectronicPrescriptionIn,
    FulfillmentIn,
    PharmacyPriceIn,
    PhotoPrescriptionIn,
    SelectPharmacyIn,
)

router = APIRouter(prefix="/api", tags=["prescriptions"])


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _load_prescription_with_items(conn, prescription_id: int):
    presc = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (prescription_id,)).fetchone()
    if not presc:
        raise HTTPException(404, "الروشتة غير موجودة")
    items = conn.execute(
        "SELECT medicine_name, dosage, duration, notes FROM prescription_items WHERE prescription_id = ?",
        (prescription_id,),
    ).fetchall()
    result = dict(presc)
    result["items"] = [dict(i) for i in items]
    return result


# =========================================================
#             إنشاء روشتة إلكترونية (من الطبيب)
# =========================================================

@router.post("/prescriptions/electronic", status_code=201)
def create_electronic_prescription(payload: ElectronicPrescriptionIn, doctor=Depends(require_role(["doctor"]))):
    if not payload.items:
        raise HTTPException(400, "لازم صنف دوا واحد على الأقل")

    with get_db() as conn:
        patient = conn.execute(
            "SELECT user_id FROM patients WHERE user_id = ?", (payload.patient_id,)
        ).fetchone()
        if not patient:
            raise HTTPException(404, "المريض غير موجود")

        if payload.appointment_id is not None:
            appt = conn.execute(
                "SELECT id FROM appointments WHERE id = ? AND doctor_id = ? AND patient_id = ?",
                (payload.appointment_id, doctor["id"], payload.patient_id),
            ).fetchone()
            if not appt:
                raise HTTPException(400, "الحجز المذكور لا يخص هذا الطبيب/المريض")

        cur = conn.execute(
            """INSERT INTO prescriptions (patient_id, doctor_id, appointment_id, type, notes)
               VALUES (?, ?, ?, 'electronic', ?)""",
            (payload.patient_id, doctor["id"], payload.appointment_id, payload.notes),
        )
        prescription_id = cur.lastrowid

        for item in payload.items:
            conn.execute(
                """INSERT INTO prescription_items (prescription_id, medicine_name, dosage, duration, notes)
                   VALUES (?, ?, ?, ?, ?)""",
                (prescription_id, item.medicine_name, item.dosage, item.duration, item.notes),
            )

        doc = conn.execute("SELECT full_name FROM doctors WHERE user_id = ?", (doctor["id"],)).fetchone()
        add_notification(conn, payload.patient_id, "روشتة جديدة",
                         f"د. {doc['full_name']} كتب لك روشتة - تقدر تبعتها للصيدليات القريبة")

    return {"message": "تم إنشاء الروشتة", "prescription_id": prescription_id}


# =========================================================
#         رفع صورة روشتة ورقية (من المريض)
# =========================================================

@router.post("/prescriptions/photo", status_code=201)
def create_photo_prescription(payload: PhotoPrescriptionIn, patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO prescriptions (patient_id, type, photo_base64, notes)
               VALUES (?, 'photo', ?, ?)""",
            (patient["id"], payload.photo_base64, payload.notes),
        )
        prescription_id = cur.lastrowid

    return {"message": "تم رفع الروشتة", "prescription_id": prescription_id}


# =========================================================
#          إرسال الروشتة لأقرب الصيدليات (فان-أوت)
# =========================================================

@router.post("/prescriptions/{prescription_id}/send-to-pharmacies")
def send_to_pharmacies(
    prescription_id: int,
    radius_km: float = Query(10, ge=1, le=100),
    user=Depends(get_current_user),
):
    with get_db() as conn:
        presc = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (prescription_id,)).fetchone()
        if not presc:
            raise HTTPException(404, "الروشتة غير موجودة")

        # يسمح فقط لصاحب الروشتة (المريض) أو الطبيب اللي كتبها
        is_owner_patient = user["role"] == "patient" and user["id"] == presc["patient_id"]
        is_prescribing_doctor = user["role"] == "doctor" and user["id"] == presc["doctor_id"]
        if not (is_owner_patient or is_prescribing_doctor):
            raise HTTPException(403, "غير مسموح")

        patient_loc = conn.execute(
            "SELECT latitude, longitude FROM users WHERE id = ?", (presc["patient_id"],)
        ).fetchone()
        if not patient_loc or patient_loc["latitude"] is None or patient_loc["longitude"] is None:
            raise HTTPException(400, "محتاجين موقع المريض (GPS) الأول عشان نلاقي أقرب صيدليات")

        pharmacies = conn.execute(
            """SELECT u.id, u.latitude, u.longitude FROM users u
               JOIN pharmacies p ON p.user_id = u.id
               WHERE u.role = 'pharmacy' AND u.is_active = 1
               AND u.latitude IS NOT NULL AND u.longitude IS NOT NULL"""
        ).fetchall()

        sent_count = 0
        for ph in pharmacies:
            distance = _haversine_km(patient_loc["latitude"], patient_loc["longitude"],
                                      ph["latitude"], ph["longitude"])
            if distance <= radius_km:
                already = conn.execute(
                    "SELECT id FROM prescription_requests WHERE prescription_id = ? AND pharmacy_id = ?",
                    (prescription_id, ph["id"]),
                ).fetchone()
                if already:
                    continue
                conn.execute(
                    """INSERT INTO prescription_requests (prescription_id, pharmacy_id, distance_km)
                       VALUES (?, ?, ?)""",
                    (prescription_id, ph["id"], round(distance, 2)),
                )
                sent_count += 1

        if sent_count > 0:
            conn.execute(
                "UPDATE prescriptions SET status = 'sent_to_pharmacies' WHERE id = ?",
                (prescription_id,),
            )

    if sent_count == 0:
        return {"message": f"مفيش صيدليات مسجلة في نطاق {radius_km} كم حاليًا", "sent_count": 0}
    return {"message": "تم إرسال الروشتة للصيدليات القريبة", "sent_count": sent_count}


# =========================================================
#                  روشتات المريض / الطبيب
# =========================================================

@router.get("/prescriptions/mine")
def my_prescriptions(patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM prescriptions WHERE patient_id = ? ORDER BY created_at DESC",
            (patient["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/prescriptions/by-doctor")
def prescriptions_written_by_me(doctor=Depends(require_role(["doctor"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM prescriptions WHERE doctor_id = ? ORDER BY created_at DESC",
            (doctor["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/prescriptions/{prescription_id}")
def prescription_detail(prescription_id: int, user=Depends(get_current_user)):
    with get_db() as conn:
        result = _load_prescription_with_items(conn, prescription_id)
        if user["role"] == "patient" and user["id"] != result["patient_id"]:
            raise HTTPException(403, "غير مسموح")
        if user["role"] == "doctor" and user["id"] != result["doctor_id"]:
            raise HTTPException(403, "غير مسموح")
    return result


# =========================================================
#           عروض الصيدليات على روشتة معينة (للمريض)
# =========================================================

@router.get("/prescriptions/{prescription_id}/offers")
def prescription_offers(prescription_id: int, patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        presc = conn.execute(
            "SELECT id FROM prescriptions WHERE id = ? AND patient_id = ?",
            (prescription_id, patient["id"]),
        ).fetchone()
        if not presc:
            raise HTTPException(404, "الروشتة غير موجودة")

        rows = conn.execute(
            """SELECT r.*, ph.name AS pharmacy_name
               FROM prescription_requests r
               JOIN pharmacies ph ON ph.user_id = r.pharmacy_id
               WHERE r.prescription_id = ?
               ORDER BY (r.status = 'priced') DESC, r.total_price ASC""",
            (prescription_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/prescriptions/{prescription_id}/select-pharmacy")
def select_pharmacy(prescription_id: int, payload: SelectPharmacyIn, patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        presc = conn.execute(
            "SELECT id FROM prescriptions WHERE id = ? AND patient_id = ?",
            (prescription_id, patient["id"]),
        ).fetchone()
        if not presc:
            raise HTTPException(404, "الروشتة غير موجودة")

        chosen = conn.execute(
            "SELECT id FROM prescription_requests WHERE id = ? AND prescription_id = ? AND status = 'priced'",
            (payload.request_id, prescription_id),
        ).fetchone()
        if not chosen:
            raise HTTPException(400, "العرض ده مش متاح للاختيار")

        conn.execute(
            "UPDATE prescription_requests SET status = 'selected', selected_at = datetime('now') WHERE id = ?",
            (payload.request_id,),
        )
        conn.execute(
            """UPDATE prescription_requests SET status = 'not_selected'
               WHERE prescription_id = ? AND id != ? AND status = 'priced'""",
            (prescription_id, payload.request_id),
        )
        conn.execute(
            "UPDATE prescriptions SET status = 'confirmed' WHERE id = ?",
            (prescription_id,),
        )

    return {"message": "تم اختيار الصيدلية، هيتم تجهيز طلبك"}


# =========================================================
#         جانب الصيدلية: استقبال وتسعير الطلبات
# =========================================================

@router.get("/pharmacy/prescription-requests")
def pharmacy_requests(status: Optional[str] = None, pharmacy=Depends(require_role(["pharmacy"]))):
    # اسم المريض بيظهر للصيدلية بس بعد ما المريض يختارها (خصوصية)
    query = """SELECT r.id, r.prescription_id, r.distance_km, r.status, r.total_price,
                      r.all_items_available, r.pharmacy_notes, r.responded_at, r.created_at,
                      r.payment_status, r.fulfillment_status, r.selected_at,
                      pr.type AS prescription_type, pr.notes AS prescription_notes,
                      CASE WHEN r.status = 'selected' THEN pt.full_name END AS patient_name
               FROM prescription_requests r
               JOIN prescriptions pr ON pr.id = r.prescription_id
               JOIN patients pt ON pt.user_id = pr.patient_id
               WHERE r.pharmacy_id = ?"""
    params = [pharmacy["id"]]
    if status:
        query += " AND r.status = ?"
        params.append(status)
    query += " ORDER BY r.created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            row = dict(r)
            if row["prescription_type"] == "electronic":
                items = conn.execute(
                    "SELECT medicine_name, dosage, duration, notes FROM prescription_items "
                    "WHERE prescription_id = ?",
                    (row["prescription_id"],),
                ).fetchall()
                row["items"] = [dict(i) for i in items]
            results.append(row)
    return results


@router.get("/pharmacy/prescription-requests/{request_id}")
def pharmacy_request_detail(request_id: int, pharmacy=Depends(require_role(["pharmacy"]))):
    """تفاصيل طلب واحد: الأصناف أو صورة الروشتة، وبيانات المريض للتوصيل بعد الاختيار فقط."""
    with get_db() as conn:
        req = conn.execute(
            "SELECT * FROM prescription_requests WHERE id = ? AND pharmacy_id = ?",
            (request_id, pharmacy["id"]),
        ).fetchone()
        if not req:
            raise HTTPException(404, "الطلب غير موجود")

        presc = conn.execute("SELECT * FROM prescriptions WHERE id = ?", (req["prescription_id"],)).fetchone()
        result = dict(req)
        result["prescription_type"] = presc["type"]
        result["prescription_notes"] = presc["notes"]
        if presc["type"] == "photo":
            result["photo_base64"] = presc["photo_base64"]
        else:
            items = conn.execute(
                "SELECT medicine_name, dosage, duration, notes FROM prescription_items WHERE prescription_id = ?",
                (presc["id"],),
            ).fetchall()
            result["items"] = [dict(i) for i in items]

        if req["status"] == "selected":
            patient = conn.execute(
                """SELECT pt.full_name, u.phone, u.city, u.latitude, u.longitude
                   FROM patients pt JOIN users u ON u.id = pt.user_id WHERE pt.user_id = ?""",
                (presc["patient_id"],),
            ).fetchone()
            result["patient"] = dict(patient) if patient else None
    return result


@router.put("/pharmacy/prescription-requests/{request_id}/fulfillment")
def update_fulfillment(request_id: int, payload: FulfillmentIn, pharmacy=Depends(require_role(["pharmacy"]))):
    if payload.status not in ("preparing", "out_for_delivery", "delivered"):
        raise HTTPException(400, "حالة غير صالحة")
    with get_db() as conn:
        req = conn.execute(
            "SELECT id FROM prescription_requests WHERE id = ? AND pharmacy_id = ? AND status = 'selected'",
            (request_id, pharmacy["id"]),
        ).fetchone()
        if not req:
            raise HTTPException(404, "الطلب غير موجود أو لم يتم اختياره بعد")
        conn.execute(
            "UPDATE prescription_requests SET fulfillment_status = ? WHERE id = ?",
            (payload.status, request_id),
        )
        info = conn.execute(
            """SELECT pr.patient_id, ph.name FROM prescription_requests r
               JOIN prescriptions pr ON pr.id = r.prescription_id
               JOIN pharmacies ph ON ph.user_id = r.pharmacy_id WHERE r.id = ?""",
            (request_id,),
        ).fetchone()
        labels = {"preparing": "طلبك قيد التجهيز", "out_for_delivery": "طلبك خرج للتوصيل", "delivered": "تم تسليم طلبك"}
        add_notification(conn, info["patient_id"], labels[payload.status], info["name"])
    return {"message": "تم تحديث حالة الطلب"}


@router.get("/pharmacy/settlements")
def pharmacy_settlements(days: int = Query(7, ge=1, le=365), pharmacy=Depends(require_role(["pharmacy"]))):
    """تقرير مبيعات الصيدلية (الطلبات المؤكدة) آخر N يوم، مقسم حسب اليوم ووسيلة الدفع الإلكتروني."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT r.id, r.total_price, r.payment_status,
                      date(COALESCE(r.selected_at, r.responded_at, r.created_at)) AS day,
                      p.method AS method
               FROM prescription_requests r
               LEFT JOIN payments p ON p.purpose = 'prescription_order'
                    AND p.reference_id = r.id AND p.status = 'paid'
               WHERE r.pharmacy_id = ? AND r.status = 'selected'
               AND date(COALESCE(r.selected_at, r.responded_at, r.created_at)) >= date('now', ?)
               ORDER BY day DESC""",
            (pharmacy["id"], f"-{days - 1} days"),
        ).fetchall()

    total = paid = 0.0
    by_method, by_day = {}, {}
    for r in rows:
        amount = r["total_price"] or 0
        total += amount
        d = by_day.setdefault(r["day"], {"day": r["day"], "orders": 0, "total": 0.0, "paid": 0.0})
        d["orders"] += 1
        d["total"] += amount
        if r["payment_status"] == "paid":
            paid += amount
            d["paid"] += amount
            m = r["method"] or "unknown"
            by_method[m] = by_method.get(m, 0.0) + amount

    return {
        "days": days,
        "orders_count": len(rows),
        "total_sales": total,
        "paid_electronically": paid,
        "not_paid_electronically": total - paid,
        "by_method": by_method,
        "by_day": sorted(by_day.values(), key=lambda x: x["day"], reverse=True),
    }


@router.put("/pharmacy/prescription-requests/{request_id}/price")
def price_prescription_request(request_id: int, payload: PharmacyPriceIn, pharmacy=Depends(require_role(["pharmacy"]))):
    with get_db() as conn:
        req = conn.execute(
            "SELECT id FROM prescription_requests WHERE id = ? AND pharmacy_id = ?",
            (request_id, pharmacy["id"]),
        ).fetchone()
        if not req:
            raise HTTPException(404, "الطلب غير موجود")

        conn.execute(
            """UPDATE prescription_requests
               SET status = 'priced', total_price = ?, all_items_available = ?,
                   pharmacy_notes = ?, responded_at = datetime('now')
               WHERE id = ?""",
            (payload.total_price, int(payload.all_items_available), payload.pharmacy_notes, request_id),
        )
        info = conn.execute(
            """SELECT pr.patient_id, ph.name FROM prescription_requests r
               JOIN prescriptions pr ON pr.id = r.prescription_id
               JOIN pharmacies ph ON ph.user_id = r.pharmacy_id WHERE r.id = ?""",
            (request_id,),
        ).fetchone()
        add_notification(conn, info["patient_id"], "وصلك عرض سعر",
                         f"{info['name']}: {payload.total_price:g} جنيه - افتح الروشتة عشان تختار")
    return {"message": "تم إرسال السعر للمريض"}


@router.put("/pharmacy/prescription-requests/{request_id}/decline")
def decline_prescription_request(request_id: int, pharmacy=Depends(require_role(["pharmacy"]))):
    with get_db() as conn:
        req = conn.execute(
            "SELECT id FROM prescription_requests WHERE id = ? AND pharmacy_id = ?",
            (request_id, pharmacy["id"]),
        ).fetchone()
        if not req:
            raise HTTPException(404, "الطلب غير موجود")

        conn.execute(
            "UPDATE prescription_requests SET status = 'declined', responded_at = datetime('now') WHERE id = ?",
            (request_id,),
        )
    return {"message": "تم رفض الطلب"}
