"""
ratings.py
تقييم المريض للطبيب بعد الكشف - مسموح مرة واحدة فقط لكل حجز، وبس لو
الحجز فعلاً بقى 'completed' (يعني الكشف حصل فعليًا، مش أي حجز عشوائي).
كل تقييم جديد بيحدّث متوسط تقييم الطبيب (doctors.rating) وعدد التقييمات
(doctors.rating_count) اللي بحث الدكاترة بيستخدمهم أصلاً.
"""

from fastapi import APIRouter, Depends, HTTPException

from auth import require_role
from database import get_db
from schemas import RatingIn

router = APIRouter(prefix="/api", tags=["ratings"])


def _recalculate_doctor_rating(conn, doctor_id: int):
    row = conn.execute(
        "SELECT AVG(stars) AS avg_stars, COUNT(*) AS cnt FROM ratings WHERE doctor_id = ?",
        (doctor_id,),
    ).fetchone()
    conn.execute(
        "UPDATE doctors SET rating = ?, rating_count = ? WHERE user_id = ?",
        (round(row["avg_stars"] or 0, 2), row["cnt"], doctor_id),
    )


@router.post("/appointments/{appointment_id}/rate", status_code=201)
def rate_appointment(appointment_id: int, payload: RatingIn, patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        appt = conn.execute(
            "SELECT * FROM appointments WHERE id = ? AND patient_id = ?",
            (appointment_id, patient["id"]),
        ).fetchone()
        if not appt:
            raise HTTPException(404, "الحجز غير موجود")
        if appt["status"] != "completed":
            raise HTTPException(400, "التقييم متاح بس بعد إتمام الكشف فعليًا")

        already = conn.execute(
            "SELECT id FROM ratings WHERE appointment_id = ?", (appointment_id,)
        ).fetchone()
        if already:
            raise HTTPException(409, "تم تقييم هذا الحجز من قبل")

        conn.execute(
            """INSERT INTO ratings (appointment_id, patient_id, doctor_id, stars, comment)
               VALUES (?, ?, ?, ?, ?)""",
            (appointment_id, patient["id"], appt["doctor_id"], payload.stars, payload.comment),
        )
        _recalculate_doctor_rating(conn, appt["doctor_id"])

    return {"message": "شكرًا لتقييمك"}


@router.get("/doctors/{doctor_id}/ratings")
def doctor_ratings(doctor_id: int):
    with get_db() as conn:
        doctor = conn.execute(
            "SELECT rating, rating_count FROM doctors WHERE user_id = ?", (doctor_id,)
        ).fetchone()
        if not doctor:
            raise HTTPException(404, "الطبيب غير موجود")

        rows = conn.execute(
            """SELECT r.stars, r.comment, r.created_at, p.full_name AS patient_name
               FROM ratings r JOIN patients p ON p.user_id = r.patient_id
               WHERE r.doctor_id = ? ORDER BY r.created_at DESC""",
            (doctor_id,),
        ).fetchall()

    return {
        "average_rating": doctor["rating"],
        "rating_count": doctor["rating_count"],
        "reviews": [dict(r) for r in rows],
    }
