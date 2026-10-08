"""
payments.py
دفع رسوم الحجز، رسوم الزيارة المنزلية للمعمل، أو قيمة طلب الصيدلية بعد
اختيارها - عن طريق أي من بوابات الدفع المحلية (gateways.py).
"""

from fastapi import APIRouter, Depends, HTTPException

from auth import require_role
from database import get_db
from gateways import GATEWAYS
from schemas import PaymentInitiateIn

router = APIRouter(prefix="/api/payments", tags=["payments"])


def _resolve_amount(conn, purpose: str, reference_id: int, patient_id: int) -> float:
    if purpose == "appointment":
        row = conn.execute(
            "SELECT fee, payment_status FROM appointments WHERE id = ? AND patient_id = ?",
            (reference_id, patient_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "الحجز غير موجود")
        if row["payment_status"] == "paid":
            raise HTTPException(400, "الحجز مدفوع بالفعل")
        return row["fee"]

    if purpose == "lab_home_visit":
        row = conn.execute(
            "SELECT service_fee, payment_status FROM lab_requests WHERE id = ? AND patient_id = ?",
            (reference_id, patient_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "طلب التحليل غير موجود")
        if row["payment_status"] == "paid":
            raise HTTPException(400, "تم الدفع بالفعل")
        if not row["service_fee"]:
            raise HTTPException(400, "لا توجد رسوم زيارة منزلية محددة لهذا الطلب")
        return row["service_fee"]

    if purpose == "prescription_order":
        row = conn.execute(
            """SELECT r.total_price, r.payment_status FROM prescription_requests r
               JOIN prescriptions p ON p.id = r.prescription_id
               WHERE r.id = ? AND r.status = 'selected' AND p.patient_id = ?""",
            (reference_id, patient_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "لازم تختار عرض صيدلية الأول قبل الدفع")
        if row["payment_status"] == "paid":
            raise HTTPException(400, "تم الدفع بالفعل")
        return row["total_price"]

    if purpose == "retail_order":
        row = conn.execute(
            "SELECT total_price, payment_status FROM retail_orders WHERE id = ? AND patient_id = ?",
            (reference_id, patient_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "الطلب غير موجود")
        if row["payment_status"] == "paid":
            raise HTTPException(400, "تم الدفع بالفعل")
        return row["total_price"]

    raise HTTPException(400, "غرض دفع غير معروف")


@router.post("/initiate", status_code=201)
def initiate_payment(payload: PaymentInitiateIn, patient=Depends(require_role(["patient"]))):
    if payload.method not in GATEWAYS:
        raise HTTPException(400, "وسيلة الدفع غير مدعومة")

    with get_db() as conn:
        amount = _resolve_amount(conn, payload.purpose, payload.reference_id, patient["id"])

        gateway = GATEWAYS[payload.method]
        try:
            result = gateway.initiate(amount, patient.get("phone"), f"{payload.purpose}:{payload.reference_id}")
        except NotImplementedError as e:
            raise HTTPException(503, f"وسيلة الدفع دي لسه مش متاحة فعليًا: {e}")

        cur = conn.execute(
            """INSERT INTO payments (patient_id, purpose, reference_id, amount, method, gateway_reference)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (patient["id"], payload.purpose, payload.reference_id, amount, payload.method,
             result["gateway_reference"]),
        )
        payment_id = cur.lastrowid

    return {"payment_id": payment_id, "amount": amount, **result}


@router.post("/{payment_id}/confirm")
def confirm_payment(payment_id: int, patient=Depends(require_role(["patient"]))):
    """
    ملحوظة مهمة: ده endpoint للتطوير/الاختبار اليدوي بس. في الإنتاج الفعلي
    التأكيد المفروض يجي من Webhook حقيقي من البوابة نفسها بعد التحقق من
    توقيعها، مش نداء مباشر من تطبيق المريض (عشان محدش يقدر يزوّر إنه دفع).
    """
    with get_db() as conn:
        payment = conn.execute(
            "SELECT * FROM payments WHERE id = ? AND patient_id = ?",
            (payment_id, patient["id"]),
        ).fetchone()
        if not payment:
            raise HTTPException(404, "عملية الدفع غير موجودة")
        if payment["status"] == "paid":
            return {"message": "العملية مدفوعة بالفعل"}

        gateway = GATEWAYS[payment["method"]]
        try:
            result_status = gateway.verify(payment["gateway_reference"])
        except NotImplementedError as e:
            raise HTTPException(503, f"التحقق من الدفع لسه مش متاح فعليًا: {e}")

        if result_status != "paid":
            raise HTTPException(400, "لسه الدفع ما اتأكدش من البوابة")

        conn.execute("UPDATE payments SET status = 'paid', paid_at = datetime('now') WHERE id = ?", (payment_id,))

        if payment["purpose"] == "appointment":
            conn.execute("UPDATE appointments SET payment_status = 'paid' WHERE id = ?", (payment["reference_id"],))
        elif payment["purpose"] == "lab_home_visit":
            conn.execute("UPDATE lab_requests SET payment_status = 'paid' WHERE id = ?", (payment["reference_id"],))
        elif payment["purpose"] == "prescription_order":
            conn.execute(
                "UPDATE prescription_requests SET payment_status = 'paid' WHERE id = ?",
                (payment["reference_id"],),
            )
        elif payment["purpose"] == "retail_order":
            conn.execute(
                "UPDATE retail_orders SET payment_status = 'paid' WHERE id = ?",
                (payment["reference_id"],),
            )

    return {"message": "تم تأكيد الدفع"}


@router.get("/mine")
def my_payments(patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM payments WHERE patient_id = ? ORDER BY created_at DESC",
            (patient["id"],),
        ).fetchall()
    return [dict(r) for r in rows]
