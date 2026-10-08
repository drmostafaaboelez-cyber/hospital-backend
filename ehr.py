"""
ehr.py
الملف الطبي الموحد للمريض:
  - نتائج التحاليل بتتضاف تلقائيًا (من labs.py وقت رفع النتيجة)
  - المريض ممكن يرفع بنفسه صور تحاليل/أشعة/روشتات قديمة
  - الطبيب يقدر يطّلع على ملف مريضه، لكن بس لو فيه علاقة فعلية بينهم
    (حجز سابق أو حالي) - حماية لخصوصية المريض
"""

from fastapi import APIRouter, Depends, HTTPException

from auth import require_role
from database import get_db
from schemas import EhrUploadIn

router = APIRouter(prefix="/api/ehr", tags=["ehr"])


@router.post("/upload", status_code=201)
def upload_ehr_record(payload: EhrUploadIn, patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO ehr_records (patient_id, source, title, file_base64, notes)
               VALUES (?, 'patient_upload', ?, ?, ?)""",
            (patient["id"], payload.title, payload.file_base64, payload.notes),
        )
    return {"message": "تم الرفع للملف الطبي", "record_id": cur.lastrowid}


@router.get("/mine")
def my_ehr(patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, source, reference_id, title, notes, created_at "
            "FROM ehr_records WHERE patient_id = ? ORDER BY created_at DESC",
            (patient["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/mine/{record_id}")
def my_ehr_record_detail(record_id: int, patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM ehr_records WHERE id = ? AND patient_id = ?",
            (record_id, patient["id"]),
        ).fetchone()
    if not row:
        raise HTTPException(404, "السجل غير موجود")
    return dict(row)


@router.get("/patient/{patient_id}")
def patient_ehr_for_doctor(patient_id: int, doctor=Depends(require_role(["doctor"]))):
    with get_db() as conn:
        has_relation = conn.execute(
            "SELECT id FROM appointments WHERE doctor_id = ? AND patient_id = ? LIMIT 1",
            (doctor["id"], patient_id),
        ).fetchone()
        if not has_relation:
            raise HTTPException(403, "مفيش علاقة (حجز) بينك وبين هذا المريض")

        rows = conn.execute(
            "SELECT id, source, reference_id, title, notes, created_at "
            "FROM ehr_records WHERE patient_id = ? ORDER BY created_at DESC",
            (patient_id,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/patient/{patient_id}/{record_id}")
def patient_ehr_record_detail_for_doctor(patient_id: int, record_id: int, doctor=Depends(require_role(["doctor"]))):
    with get_db() as conn:
        has_relation = conn.execute(
            "SELECT id FROM appointments WHERE doctor_id = ? AND patient_id = ? LIMIT 1",
            (doctor["id"], patient_id),
        ).fetchone()
        if not has_relation:
            raise HTTPException(403, "مفيش علاقة (حجز) بينك وبين هذا المريض")

        row = conn.execute(
            "SELECT * FROM ehr_records WHERE id = ? AND patient_id = ?",
            (record_id, patient_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "السجل غير موجود")
    return dict(row)
