"""
notifications.py
إشعارات عامة لأي دور (تذكير مواعيد، تنبيهات مستقبلية أخرى). التذكيرات
نفسها بتتولّد من reminders.py، والراوتر ده بس لعرضها/تحديد قراءتها.
"""

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from database import get_db

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/mine")
def my_notifications(user=Depends(get_current_user)):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/unread-count")
def unread_count(user=Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0",
            (user["id"],),
        ).fetchone()
    return {"unread_count": row["c"]}


@router.put("/{notification_id}/read")
def mark_notification_read(notification_id: int, user=Depends(get_current_user)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM notifications WHERE id = ? AND user_id = ?",
            (notification_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "الإشعار غير موجود")
        conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))
    return {"message": "تم"}
