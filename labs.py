"""
labs.py
دورة التحليل الطبي:
  1) الطبيب يحوّل مريضه لمعمل معين بأنواع تحاليل محددة (أو المريض يطلب مباشرة
     بدون تحويل طبيب)
  2) لو المريض طالب زيارة منزلية، المعمل بيحدد ميعاد الزيارة
  3) المعمل يرفع نتيجة التحليل (PDF/صورة) - ده بيحصل عليه تلقائيًا في الملف
     الطبي للمريض (EHR) وبيبقى متاح للطبيب المحوِّل يشوفه
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_role
from database import add_notification, get_db
from schemas import LabReferralIn, LabSelfRequestIn, ScheduleVisitIn, UploadResultIn

router = APIRouter(prefix="/api", tags=["labs"])


def _insert_items(conn, lab_request_id: int, tests):
    for t in tests:
        conn.execute(
            "INSERT INTO lab_request_items (lab_request_id, test_name, notes) VALUES (?, ?, ?)",
            (lab_request_id, t.test_name, t.notes),
        )


# =========================================================
#      قائمة عامة بالمعامل (عشان الطبيب/المريض يختار معمل)
# =========================================================

@router.get("/labs/search")
def search_labs(country: Optional[str] = None, city: Optional[str] = None):
    query = """SELECT l.user_id, l.name, l.address, l.offers_home_visits, u.city, u.country
               FROM labs l JOIN users u ON u.id = l.user_id WHERE u.is_active = 1"""
    params = []
    if country:
        query += " AND u.country = ?"
        params.append(country)
    if city:
        query += " AND u.city = ?"
        params.append(city)
    query += " ORDER BY l.name"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# =========================================================
#           تحويل من الطبيب (referral) لمعمل معين
# =========================================================

@router.post("/lab-referrals", status_code=201)
def create_lab_referral(payload: LabReferralIn, doctor=Depends(require_role(["doctor"]))):
    if not payload.tests:
        raise HTTPException(400, "لازم تحليل واحد على الأقل")

    with get_db() as conn:
        patient = conn.execute(
            "SELECT user_id FROM patients WHERE user_id = ?", (payload.patient_id,)
        ).fetchone()
        if not patient:
            raise HTTPException(404, "المريض غير موجود")

        lab = conn.execute("SELECT user_id FROM labs WHERE user_id = ?", (payload.lab_id,)).fetchone()
        if not lab:
            raise HTTPException(404, "المعمل غير موجود")

        if payload.appointment_id is not None:
            appt = conn.execute(
                "SELECT id FROM appointments WHERE id = ? AND doctor_id = ? AND patient_id = ?",
                (payload.appointment_id, doctor["id"], payload.patient_id),
            ).fetchone()
            if not appt:
                raise HTTPException(400, "الحجز المذكور لا يخص هذا الطبيب/المريض")

        cur = conn.execute(
            """INSERT INTO lab_requests
               (patient_id, doctor_id, lab_id, appointment_id, home_visit_requested, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (payload.patient_id, doctor["id"], payload.lab_id, payload.appointment_id,
             int(payload.home_visit_requested), payload.notes),
        )
        lab_request_id = cur.lastrowid
        _insert_items(conn, lab_request_id, payload.tests)

        doc = conn.execute("SELECT full_name FROM doctors WHERE user_id = ?", (doctor["id"],)).fetchone()
        lab_name = conn.execute("SELECT name FROM labs WHERE user_id = ?", (payload.lab_id,)).fetchone()["name"]
        add_notification(conn, payload.patient_id, "تحويل لتحليل",
                         f"د. {doc['full_name']} حوّلك لـ {lab_name} - تابع الطلب من تحاليلي")

    return {"message": "تم إرسال التحويل للمعمل", "lab_request_id": lab_request_id}


# =========================================================
#             طلب تحليل مباشر من المريض (بدون تحويل)
# =========================================================

@router.post("/lab-requests/self", status_code=201)
def create_self_lab_request(payload: LabSelfRequestIn, patient=Depends(require_role(["patient"]))):
    if not payload.tests:
        raise HTTPException(400, "لازم تحليل واحد على الأقل")

    with get_db() as conn:
        lab = conn.execute("SELECT user_id FROM labs WHERE user_id = ?", (payload.lab_id,)).fetchone()
        if not lab:
            raise HTTPException(404, "المعمل غير موجود")

        cur = conn.execute(
            """INSERT INTO lab_requests (patient_id, lab_id, home_visit_requested, notes)
               VALUES (?, ?, ?, ?)""",
            (patient["id"], payload.lab_id, int(payload.home_visit_requested), payload.notes),
        )
        lab_request_id = cur.lastrowid
        _insert_items(conn, lab_request_id, payload.tests)

    return {"message": "تم إرسال طلب التحليل للمعمل", "lab_request_id": lab_request_id}


# =========================================================
#                    طلبات المريض نفسه
# =========================================================

@router.get("/lab-requests/mine")
def my_lab_requests(patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT lr.id, lr.lab_id, lr.doctor_id, lr.status, lr.home_visit_requested,
                      lr.scheduled_visit_time, lr.service_fee, lr.payment_status, lr.notes,
                      lr.result_notes, lr.created_at,
                      (lr.result_file_base64 IS NOT NULL) AS has_result,
                      l.name AS lab_name, d.full_name AS doctor_name
               FROM lab_requests lr
               JOIN labs l ON l.user_id = lr.lab_id
               LEFT JOIN doctors d ON d.user_id = lr.doctor_id
               WHERE lr.patient_id = ? ORDER BY lr.created_at DESC""",
            (patient["id"],),
        ).fetchall()
        results = []
        for r in rows:
            row = dict(r)
            items = conn.execute(
                "SELECT test_name, notes FROM lab_request_items WHERE lab_request_id = ?", (row["id"],)
            ).fetchall()
            row["tests"] = [dict(i) for i in items]
            results.append(row)
    return results


# =========================================================
#              جانب المعمل: استقبال وتنفيذ الطلبات
# =========================================================

@router.get("/lab/requests")
def lab_requests_list(
    status: Optional[str] = None,
    home_visit_only: bool = False,
    lab=Depends(require_role(["lab"])),
):
    query = """SELECT lr.id, lr.patient_id, lr.doctor_id, lr.status, lr.home_visit_requested,
                      lr.scheduled_visit_time, lr.result_notes, lr.notes, lr.service_fee,
                      lr.payment_status, lr.created_at,
                      (lr.result_file_base64 IS NOT NULL) AS has_result,
                      p.full_name AS patient_name, d.full_name AS referring_doctor_name,
                      u.phone AS patient_phone, u.city AS patient_city,
                      u.latitude AS patient_lat, u.longitude AS patient_lng
               FROM lab_requests lr
               JOIN patients p ON p.user_id = lr.patient_id
               JOIN users u ON u.id = lr.patient_id
               LEFT JOIN doctors d ON d.user_id = lr.doctor_id
               WHERE lr.lab_id = ?"""
    params = [lab["id"]]
    if status:
        query += " AND lr.status = ?"
        params.append(status)
    if home_visit_only:
        query += " AND lr.home_visit_requested = 1"
    query += " ORDER BY lr.created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            row = dict(r)
            items = conn.execute(
                "SELECT test_name, notes FROM lab_request_items WHERE lab_request_id = ?",
                (row["id"],),
            ).fetchall()
            row["tests"] = [dict(i) for i in items]
            results.append(row)
    return results


@router.put("/lab/requests/{request_id}/schedule-visit")
def schedule_home_visit(request_id: int, payload: ScheduleVisitIn, lab=Depends(require_role(["lab"]))):
    with get_db() as conn:
        req = conn.execute(
            "SELECT id, patient_id, home_visit_requested FROM lab_requests WHERE id = ? AND lab_id = ?",
            (request_id, lab["id"]),
        ).fetchone()
        if not req:
            raise HTTPException(404, "الطلب غير موجود")
        if not req["home_visit_requested"]:
            raise HTTPException(400, "المريض لم يطلب زيارة منزلية لهذا الطلب")

        conn.execute(
            "UPDATE lab_requests SET status = 'home_visit_scheduled', scheduled_visit_time = ?, "
            "service_fee = COALESCE(?, service_fee) WHERE id = ?",
            (payload.scheduled_visit_time, payload.service_fee, request_id),
        )
        fee = conn.execute("SELECT service_fee FROM lab_requests WHERE id = ?", (request_id,)).fetchone()["service_fee"]
        add_notification(conn, req["patient_id"], "تم تحديد ميعاد الزيارة المنزلية",
                         f"الميعاد: {payload.scheduled_visit_time}" + (f" - الرسوم {fee:g} جنيه، تقدر تدفعها من تحاليلي" if fee else ""))
    return {"message": "تم تحديد ميعاد الزيارة المنزلية"}


@router.get("/lab/referral-stats")
def lab_referral_stats(lab=Depends(require_role(["lab"]))):
    """تتبع الحالات المحوّلة من كل طبيب/عيادة - أساس حساب الخصومات والترتيبات المالية."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT d.user_id AS doctor_id, d.full_name AS doctor_name, d.specialty,
                      d.clinic_name, COUNT(*) AS total_referrals,
                      SUM(CASE WHEN lr.status = 'completed' THEN 1 ELSE 0 END) AS completed,
                      MAX(lr.created_at) AS last_referral_at
               FROM lab_requests lr
               JOIN doctors d ON d.user_id = lr.doctor_id
               WHERE lr.lab_id = ?
               GROUP BY d.user_id ORDER BY total_referrals DESC""",
            (lab["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/lab/requests/{request_id}/upload-result")
def upload_result(request_id: int, payload: UploadResultIn, lab=Depends(require_role(["lab"]))):
    with get_db() as conn:
        req = conn.execute(
            "SELECT * FROM lab_requests WHERE id = ? AND lab_id = ?",
            (request_id, lab["id"]),
        ).fetchone()
        if not req:
            raise HTTPException(404, "الطلب غير موجود")
        if req["status"] == "cancelled":
            raise HTTPException(400, "الطلب ده ملغي")

        conn.execute(
            """UPDATE lab_requests SET status = 'completed',
               result_file_base64 = ?, result_notes = ? WHERE id = ?""",
            (payload.result_file_base64, payload.result_notes, request_id),
        )

        test_names = conn.execute(
            "SELECT test_name FROM lab_request_items WHERE lab_request_id = ?", (request_id,)
        ).fetchall()
        title = "نتيجة تحليل: " + "، ".join(t["test_name"] for t in test_names)

        # يظهر تلقائيًا في الملف الطبي للمريض (EHR)
        conn.execute(
            """INSERT INTO ehr_records (patient_id, source, reference_id, title, file_base64, notes)
               VALUES (?, 'lab_result', ?, ?, ?, ?)""",
            (req["patient_id"], request_id, title, payload.result_file_base64, payload.result_notes),
        )
        add_notification(conn, req["patient_id"], "نتيجة تحليلك جاهزة", "تقدر تشوفها من ملفك الطبي")

    return {"message": "تم رفع النتيجة وتحديث الملف الطبي للمريض"}


# =========================================================
#      متابعة الطبيب لنتائج التحاليل اللي حوّلها بنفسه
# =========================================================

@router.get("/doctor/lab-referrals")
def doctor_lab_referrals(unseen_only: bool = False, doctor=Depends(require_role(["doctor"]))):
    query = """SELECT lr.*, p.full_name AS patient_name, l.name AS lab_name
               FROM lab_requests lr
               JOIN patients p ON p.user_id = lr.patient_id
               JOIN labs l ON l.user_id = lr.lab_id
               WHERE lr.doctor_id = ?"""
    params = [doctor["id"]]
    if unseen_only:
        query += " AND lr.status = 'completed' AND lr.doctor_viewed_at IS NULL"
    query += " ORDER BY lr.created_at DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.put("/doctor/lab-referrals/{request_id}/mark-viewed")
def mark_referral_viewed(request_id: int, doctor=Depends(require_role(["doctor"]))):
    with get_db() as conn:
        req = conn.execute(
            "SELECT id FROM lab_requests WHERE id = ? AND doctor_id = ?",
            (request_id, doctor["id"]),
        ).fetchone()
        if not req:
            raise HTTPException(404, "الطلب غير موجود")
        conn.execute(
            "UPDATE lab_requests SET doctor_viewed_at = datetime('now') WHERE id = ?",
            (request_id,),
        )
    return {"message": "تم"}
