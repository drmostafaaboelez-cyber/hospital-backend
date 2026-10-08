"""
appointments.py
نظام الحجز الكامل:
  - الطبيب بيحدد جدوله الأسبوعي (أيام + ساعات العمل + مدة الكشف)
  - المريض بيشوف الفترات المتاحة فعليًا (مطروح منها المحجوز) ويحجز
  - الطبيب أو السكرتير (لو عنده صلاحية) بيأكد/يلغي/يقفل الحجز
  - إحصائيات يومية للطبيب
"""

from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import get_current_user, require_role
from database import add_notification, get_db
from schemas import AppointmentBookIn, AppointmentStatusUpdate, DoctorScheduleIn

router = APIRouter(prefix="/api", tags=["appointments"])


# ---------------------------------------------------------
# مساعد: يرجع doctor_id بتاع العيادة اللي المستخدم شغال فيها
# (لو طبيب -> نفسه، لو سكرتير -> الطبيب اللي تابع له)
# ---------------------------------------------------------

def _staff_context(user: dict) -> dict:
    if user["role"] == "doctor":
        return {"doctor_id": user["id"], "can_change_status": True, "can_confirm": True}

    if user["role"] == "secretary":
        with get_db() as conn:
            row = conn.execute(
                "SELECT doctor_id, can_confirm_attendance, can_change_booking_status "
                "FROM secretaries WHERE user_id = ?",
                (user["id"],),
            ).fetchone()
        if not row:
            raise HTTPException(404, "بيانات السكرتير غير مكتملة")
        return {
            "doctor_id": row["doctor_id"],
            "can_change_status": bool(row["can_change_booking_status"]),
            "can_confirm": bool(row["can_confirm_attendance"]),
        }

    raise HTTPException(403, "غير مسموح لهذا الدور")


def _time_to_minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _minutes_to_time(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


# =========================================================
#              جدول الطبيب الأسبوعي (ساعات العمل)
# =========================================================

@router.put("/doctor/schedule")
def set_doctor_schedule(payload: DoctorScheduleIn, doctor=Depends(require_role(["doctor"]))):
    with get_db() as conn:
        conn.execute("DELETE FROM doctor_schedule WHERE doctor_id = ?", (doctor["id"],))
        for day in payload.days:
            if _time_to_minutes(day.end_time) <= _time_to_minutes(day.start_time):
                raise HTTPException(400, f"وقت النهاية لازم يكون بعد وقت البداية (يوم {day.day_of_week})")
            conn.execute(
                "INSERT INTO doctor_schedule (doctor_id, day_of_week, start_time, end_time, slot_minutes) "
                "VALUES (?, ?, ?, ?, ?)",
                (doctor["id"], day.day_of_week, day.start_time, day.end_time, day.slot_minutes),
            )
    return {"message": "تم تحديث الجدول", "days_count": len(payload.days)}


@router.get("/doctor/schedule")
def get_doctor_schedule(doctor=Depends(require_role(["doctor"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT day_of_week, start_time, end_time, slot_minutes "
            "FROM doctor_schedule WHERE doctor_id = ? ORDER BY day_of_week",
            (doctor["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


# =========================================================
#      الفترات المتاحة فعليًا لطبيب معين في يوم معين
# =========================================================

@router.get("/doctors/{doctor_id}/available-slots")
def available_slots(doctor_id: int, date: str = Query(..., description="YYYY-MM-DD")):
    try:
        target_date = date_cls.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "صيغة التاريخ غلط، المطلوب YYYY-MM-DD")

    # بايثون: Monday=0 ... Sunday=6  ->  نحوّلها لـ 0=الأحد ... 6=السبت
    day_of_week = (target_date.weekday() + 1) % 7

    with get_db() as conn:
        schedule = conn.execute(
            "SELECT start_time, end_time, slot_minutes FROM doctor_schedule "
            "WHERE doctor_id = ? AND day_of_week = ?",
            (doctor_id, day_of_week),
        ).fetchone()

        if not schedule:
            return {"date": date, "slots": []}

        booked_rows = conn.execute(
            "SELECT start_time FROM appointments "
            "WHERE doctor_id = ? AND appointment_date = ? "
            "AND status NOT IN ('cancelled_by_doctor','cancelled_by_patient')",
            (doctor_id, date),
        ).fetchall()
        booked_times = {r["start_time"] for r in booked_rows}

    slots = []
    cursor = _time_to_minutes(schedule["start_time"])
    end = _time_to_minutes(schedule["end_time"])
    step = schedule["slot_minutes"]

    now_check = target_date == date_cls.today()
    now_minutes = datetime.now().hour * 60 + datetime.now().minute

    while cursor + step <= end:
        slot_start = _minutes_to_time(cursor)
        if slot_start not in booked_times and not (now_check and cursor <= now_minutes):
            slots.append({"start_time": slot_start, "end_time": _minutes_to_time(cursor + step)})
        cursor += step

    return {"date": date, "slots": slots}


# =========================================================
#                        حجز موعد
# =========================================================

@router.post("/appointments/book", status_code=201)
def book_appointment(payload: AppointmentBookIn, patient=Depends(require_role(["patient"]))):
    try:
        target_date = date_cls.fromisoformat(payload.date)
    except ValueError:
        raise HTTPException(400, "صيغة التاريخ غلط، المطلوب YYYY-MM-DD")
    if target_date < date_cls.today():
        raise HTTPException(400, "معلش، مينفعش تحجز في تاريخ فات")

    with get_db() as conn:
        doctor = conn.execute(
            "SELECT consultation_fee FROM doctors WHERE user_id = ?", (payload.doctor_id,)
        ).fetchone()
        if not doctor:
            raise HTTPException(404, "الطبيب غير موجود")

        day_of_week = (target_date.weekday() + 1) % 7
        schedule = conn.execute(
            "SELECT start_time, end_time, slot_minutes FROM doctor_schedule "
            "WHERE doctor_id = ? AND day_of_week = ?",
            (payload.doctor_id, day_of_week),
        ).fetchone()
        if not schedule:
            raise HTTPException(400, "الطبيب مش شغال في اليوم ده")

        slot_start = _time_to_minutes(payload.start_time)
        slot_end_time = _minutes_to_time(slot_start + schedule["slot_minutes"])
        if not (_time_to_minutes(schedule["start_time"]) <= slot_start
                and slot_start + schedule["slot_minutes"] <= _time_to_minutes(schedule["end_time"])):
            raise HTTPException(400, "الميعاد ده مش ضمن ساعات عمل الطبيب")

        clash = conn.execute(
            "SELECT id FROM appointments WHERE doctor_id = ? AND appointment_date = ? "
            "AND start_time = ? AND status NOT IN ('cancelled_by_doctor','cancelled_by_patient')",
            (payload.doctor_id, payload.date, payload.start_time),
        ).fetchone()
        if clash:
            raise HTTPException(409, "الميعاد ده محجوز بالفعل، اختر ميعاد تاني")

        cur = conn.execute(
            """INSERT INTO appointments
               (doctor_id, patient_id, appointment_date, start_time, end_time, fee, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (payload.doctor_id, patient["id"], payload.date, payload.start_time,
             slot_end_time, doctor["consultation_fee"], payload.notes),
        )
        appointment_id = cur.lastrowid

    return {"message": "تم إرسال طلب الحجز، في انتظار تأكيد العيادة", "appointment_id": appointment_id}


# =========================================================
#                  حجوزات المريض نفسه
# =========================================================

@router.get("/appointments/mine")
def my_appointments(patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT a.*, d.full_name AS doctor_name, d.specialty
               FROM appointments a
               JOIN doctors d ON d.user_id = a.doctor_id
               WHERE a.patient_id = ?
               ORDER BY a.appointment_date DESC, a.start_time DESC""",
            (patient["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.put("/appointments/{appointment_id}/cancel")
def cancel_my_appointment(appointment_id: int, patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        appt = conn.execute(
            "SELECT * FROM appointments WHERE id = ? AND patient_id = ?",
            (appointment_id, patient["id"]),
        ).fetchone()
        if not appt:
            raise HTTPException(404, "الحجز غير موجود")
        if appt["status"] in ("completed", "cancelled_by_doctor", "cancelled_by_patient"):
            raise HTTPException(400, "مينفعش تلغي الحجز ده دلوقتي")

        conn.execute(
            "UPDATE appointments SET status = 'cancelled_by_patient' WHERE id = ?",
            (appointment_id,),
        )
    return {"message": "تم إلغاء الحجز"}


# =========================================================
#         حجوزات العيادة (للطبيب أو السكرتير بتاعه)
# =========================================================

@router.get("/doctor/appointments")
def clinic_appointments(date: Optional[str] = None, user=Depends(get_current_user)):
    ctx = _staff_context(user)
    query = """SELECT a.*, p.full_name AS patient_name
               FROM appointments a
               JOIN patients p ON p.user_id = a.patient_id
               WHERE a.doctor_id = ?"""
    params = [ctx["doctor_id"]]
    if date:
        query += " AND a.appointment_date = ?"
        params.append(date)
    query += " ORDER BY a.appointment_date, a.start_time"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


@router.put("/doctor/appointments/{appointment_id}/status")
def update_appointment_status(
    appointment_id: int, payload: AppointmentStatusUpdate, user=Depends(get_current_user)
):
    ctx = _staff_context(user)
    if not ctx["can_change_status"]:
        raise HTTPException(403, "مفيش صلاحية لتغيير حالة الحجوزات")

    valid_statuses = {"confirmed", "completed", "cancelled_by_doctor", "no_show"}
    if payload.status not in valid_statuses:
        raise HTTPException(400, f"الحالة لازم تكون واحدة من: {', '.join(valid_statuses)}")

    with get_db() as conn:
        appt = conn.execute(
            "SELECT * FROM appointments WHERE id = ? AND doctor_id = ?",
            (appointment_id, ctx["doctor_id"]),
        ).fetchone()
        if not appt:
            raise HTTPException(404, "الحجز غير موجود في عيادتك")

        conn.execute(
            "UPDATE appointments SET status = ?, cancel_reason = ? WHERE id = ?",
            (payload.status, payload.reason, appointment_id),
        )

        if payload.status in ("confirmed", "cancelled_by_doctor"):
            doc = conn.execute(
                "SELECT full_name FROM doctors WHERE user_id = ?", (ctx["doctor_id"],)
            ).fetchone()
            when = f"{appt['appointment_date']} الساعة {appt['start_time']}"
            if payload.status == "confirmed":
                add_notification(conn, appt["patient_id"], "تم تأكيد حجزك",
                                 f"د. {doc['full_name']} - {when}")
            else:
                add_notification(conn, appt["patient_id"], "تم إلغاء حجزك",
                                 f"د. {doc['full_name']} - {when}" + (f" - السبب: {payload.reason}" if payload.reason else ""))
    return {"message": "تم تحديث حالة الحجز"}


# =========================================================
#                  إحصائيات اليوم للطبيب
# =========================================================

@router.get("/doctor/stats/today")
def today_stats(user=Depends(get_current_user)):
    ctx = _staff_context(user)
    today = date_cls.today().isoformat()

    with get_db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c, COALESCE(SUM(fee), 0) AS total_fee "
            "FROM appointments WHERE doctor_id = ? AND appointment_date = ? "
            "GROUP BY status",
            (ctx["doctor_id"], today),
        ).fetchall()

    breakdown = {r["status"]: r["c"] for r in rows}
    revenue = sum(r["total_fee"] for r in rows if r["status"] == "completed")
    total = sum(r["c"] for r in rows)

    return {
        "date": today,
        "total_bookings": total,
        "breakdown": breakdown,
        "completed_revenue": revenue,
    }
