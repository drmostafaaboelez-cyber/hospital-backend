"""
reminders.py
تذكير المريض بموعده قبلها بـ 24 ساعة وقبلها بساعتين. الدالة دي بتتنده
دوريًا (كل 15 دقيقة مثلاً) من مجدول خلفي (APScheduler) في main.py، مش
endpoint بيتنده من العميل - المريض بيستقبل التذكير كإشعار عادي.

نطاقات الوقت (23-24 ساعة / 1.5-2 ساعة) متعمد تكون أعرض من فترة تكرار
المجدول (15 دقيقة) عشان نضمن إن كل موعد ياخد تذكيره مرة واحدة بالظبط
حتى لو المجدول اتأخر شوية، من غير ما يتكرر (بنسجل وقت الإرسال في
reminder_24h_sent_at / reminder_2h_sent_at ومنبعتش تاني لو اتبعت قبل كده).
"""

from datetime import datetime

from database import get_db


def _create_notification(conn, patient_id: int, title: str, body: str):
    conn.execute(
        "INSERT INTO notifications (user_id, title, body) VALUES (?, ?, ?)",
        (patient_id, title, body),
    )


def send_appointment_reminders():
    now = datetime.now()

    with get_db() as conn:
        appts = conn.execute(
            """SELECT a.*, d.full_name AS doctor_name
               FROM appointments a
               JOIN doctors d ON d.user_id = a.doctor_id
               WHERE a.status = 'confirmed'
               AND a.reminder_2h_sent_at IS NULL"""
        ).fetchall()

        sent = {"24h": 0, "2h": 0}

        for a in appts:
            try:
                appt_dt = datetime.strptime(f"{a['appointment_date']} {a['start_time']}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue

            hours_left = (appt_dt - now).total_seconds() / 3600.0

            if 23 <= hours_left <= 24 and not a["reminder_24h_sent_at"]:
                _create_notification(
                    conn, a["patient_id"], "تذكير بموعدك بكرة",
                    f"عندك كشف مع د. {a['doctor_name']} يوم {a['appointment_date']} الساعة {a['start_time']}",
                )
                conn.execute(
                    "UPDATE appointments SET reminder_24h_sent_at = datetime('now') WHERE id = ?", (a["id"],)
                )
                sent["24h"] += 1

            if 1.5 <= hours_left <= 2 and not a["reminder_2h_sent_at"]:
                _create_notification(
                    conn, a["patient_id"], "تذكير: موعدك بعد ساعتين",
                    f"عندك كشف مع د. {a['doctor_name']} النهاردة الساعة {a['start_time']}",
                )
                conn.execute(
                    "UPDATE appointments SET reminder_2h_sent_at = datetime('now') WHERE id = ?", (a["id"],)
                )
                sent["2h"] += 1

    return sent
