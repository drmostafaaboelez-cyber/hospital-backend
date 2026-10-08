"""
main.py
نقطة تشغيل الباك اند. المرحلة دي بتغطي:
  1) نظام Register/Login موحّد لكل الأطراف (مريض/طبيب/سكرتير/صيدلية/معمل)
     مبني على RBAC حقيقي بالـ JWT (مش مجرد شكل - كل endpoint بيتحقق فعليًا
     من الدور المسموح له).
  2) تحديد الدولة/المدينة من إحداثيات GPS (Reverse Geocoding) وتخزينها.
  3) بحث الدكاترة مفلتر بالدولة/المدينة اللي المريض فيها + التخصص/السعر/التقييم.

تشغيل محلي:
    pip install -r requirements.txt
    uvicorn main:app --reload
"""

from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from appointments import router as appointments_router
from auth import create_access_token, hash_password, require_role, verify_password
from database import get_db, init_db
from ehr import router as ehr_router
from labs import router as labs_router
from notifications import router as notifications_router
from payments import router as payments_router
from prescriptions import router as prescriptions_router
from ratings import router as ratings_router
from reminders import send_appointment_reminders
from retail import router as retail_router
from schemas import (
    DoctorRegister,
    DoctorSearchResult,
    LabRegister,
    LoginRequest,
    PatientRegister,
    PharmacyRegister,
    SecretaryRegister,
    TokenResponse,
)

app = FastAPI(title="المنظومة الطبية - Backend Core")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ضيّقها لدومينك الحقيقي وقت الإنتاج
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup_event():
    init_db()

    # مجدول خلفي بسيط بيبعت تذكيرات المواعيد كل 15 دقيقة (تفاصيل الفترات
    # الزمنية في reminders.py). Import هنا جوا الدالة عشان لو المكتبة مش
    # متثبتة أصلاً الـ endpoints التانية تفضل شغالة عادي.
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()
        scheduler.add_job(send_appointment_reminders, "interval", minutes=15, id="appointment_reminders")
        scheduler.start()
        app.state.scheduler = scheduler
    except ImportError:
        print("[تحذير] مكتبة apscheduler مش متثبتة - تذكيرات المواعيد التلقائية مش هتشتغل. "
              "شغّل: pip install apscheduler")


app.include_router(appointments_router)
app.include_router(prescriptions_router)
app.include_router(labs_router)
app.include_router(ehr_router)
app.include_router(ratings_router)
app.include_router(payments_router)
app.include_router(notifications_router)
app.include_router(retail_router)


# =========================================================
#                    تحديد الموقع (GPS)
# =========================================================

@app.get("/api/location/detect")
async def detect_location(latitude: float = Query(...), longitude: float = Query(...)):
    """
    بياخد إحداثيات GPS ويرجّع الدولة/المدينة عن طريق Nominatim (OpenStreetMap)
    - خدمة مجانية وبدون مفتاح API، بنفس فلسفة استخدام OSRM في مشروع حواليك.
    """
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": latitude, "lon": longitude, "format": "jsonv2"}
    headers = {"User-Agent": "MedicalPlatformApp/1.0"}

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="تعذر تحديد الموقع حاليًا، حاول تاني",
        )

    address = data.get("address", {})
    country = address.get("country")
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("county")
    )

    return {"country": country, "city": city, "latitude": latitude, "longitude": longitude}


# =========================================================
#                       تسجيل الدخول
# =========================================================

@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, password_hash, role, is_active FROM users "
            "WHERE email = ? OR phone = ?",
            (payload.identifier, payload.identifier),
        ).fetchone()

    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                             detail="بيانات الدخول غير صحيحة")
    if not row["is_active"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                             detail="الحساب موقوف، تواصل مع الدعم")

    token = create_access_token(row["id"], row["role"])
    return TokenResponse(access_token=token, role=row["role"], user_id=row["id"])


def _create_base_user(conn, email, phone, password, role, country, city, latitude, longitude) -> int:
    if not email and not phone:
        raise HTTPException(status_code=400, detail="لازم إيميل أو رقم موبايل")

    existing = conn.execute(
        "SELECT id FROM users WHERE (email IS NOT NULL AND email = ?) "
        "OR (phone IS NOT NULL AND phone = ?)",
        (email, phone),
    ).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="الإيميل أو رقم الموبايل مسجل قبل كده")

    cur = conn.execute(
        "INSERT INTO users (email, phone, password_hash, role, country, city, latitude, longitude) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (email, phone, hash_password(password), role, country, city, latitude, longitude),
    )
    return cur.lastrowid


# =========================================================
#                    تسجيل المريض
# =========================================================

@app.post("/api/auth/register/patient", response_model=TokenResponse, status_code=201)
def register_patient(payload: PatientRegister):
    with get_db() as conn:
        user_id = _create_base_user(
            conn, payload.email, payload.phone, payload.password, "patient",
            payload.country, payload.city, payload.latitude, payload.longitude,
        )
        conn.execute(
            "INSERT INTO patients (user_id, full_name, date_of_birth, gender, national_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, payload.full_name, payload.date_of_birth, payload.gender, payload.national_id),
        )

    token = create_access_token(user_id, "patient")
    return TokenResponse(access_token=token, role="patient", user_id=user_id)


# =========================================================
#                    تسجيل الطبيب
# =========================================================

@app.post("/api/auth/register/doctor", response_model=TokenResponse, status_code=201)
def register_doctor(payload: DoctorRegister):
    with get_db() as conn:
        user_id = _create_base_user(
            conn, payload.email, payload.phone, payload.password, "doctor",
            payload.country, payload.city, payload.latitude, payload.longitude,
        )
        conn.execute(
            """INSERT INTO doctors
               (user_id, full_name, specialty, license_number, clinic_name,
                clinic_address, consultation_fee, bio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, payload.full_name, payload.specialty, payload.license_number,
             payload.clinic_name, payload.clinic_address, payload.consultation_fee, payload.bio),
        )

    token = create_access_token(user_id, "doctor")
    return TokenResponse(access_token=token, role="doctor", user_id=user_id)


# =========================================================
#     تسجيل السكرتير - بيتعمل من حساب الطبيب صاحب العيادة
# =========================================================

@app.post("/api/auth/register/secretary", response_model=TokenResponse, status_code=201)
def register_secretary(payload: SecretaryRegister, doctor=Depends(require_role(["doctor"]))):
    with get_db() as conn:
        user_id = _create_base_user(
            conn, payload.email, payload.phone, payload.password, "secretary",
            None, None, None, None,
        )
        conn.execute(
            """INSERT INTO secretaries
               (user_id, doctor_id, full_name, can_confirm_attendance,
                can_change_booking_status, can_manage_queue)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, doctor["id"], payload.full_name,
             int(payload.can_confirm_attendance),
             int(payload.can_change_booking_status),
             int(payload.can_manage_queue)),
        )

    token = create_access_token(user_id, "secretary")
    return TokenResponse(access_token=token, role="secretary", user_id=user_id)


# =========================================================
#                    تسجيل الصيدلية
# =========================================================

@app.post("/api/auth/register/pharmacy", response_model=TokenResponse, status_code=201)
def register_pharmacy(payload: PharmacyRegister):
    with get_db() as conn:
        user_id = _create_base_user(
            conn, payload.email, payload.phone, payload.password, "pharmacy",
            payload.country, payload.city, payload.latitude, payload.longitude,
        )
        conn.execute(
            "INSERT INTO pharmacies (user_id, name, license_number, address) "
            "VALUES (?, ?, ?, ?)",
            (user_id, payload.name, payload.license_number, payload.address),
        )

    token = create_access_token(user_id, "pharmacy")
    return TokenResponse(access_token=token, role="pharmacy", user_id=user_id)


# =========================================================
#                      تسجيل المعمل
# =========================================================

@app.post("/api/auth/register/lab", response_model=TokenResponse, status_code=201)
def register_lab(payload: LabRegister):
    with get_db() as conn:
        user_id = _create_base_user(
            conn, payload.email, payload.phone, payload.password, "lab",
            payload.country, payload.city, payload.latitude, payload.longitude,
        )
        conn.execute(
            "INSERT INTO labs (user_id, name, license_number, address, offers_home_visits) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, payload.name, payload.license_number, payload.address,
             int(payload.offers_home_visits)),
        )

    token = create_access_token(user_id, "lab")
    return TokenResponse(access_token=token, role="lab", user_id=user_id)


# =========================================================
#                 بيانات المستخدم الحالي (اختبار RBAC)
# =========================================================

@app.get("/api/auth/me")
def me(user=Depends(require_role(["patient", "doctor", "secretary", "pharmacy", "lab"]))):
    return user


@app.get("/api/pharmacy/me")
def my_pharmacy_profile(pharmacy=Depends(require_role(["pharmacy"]))):
    with get_db() as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.phone, u.country, u.city, ph.name, ph.address, ph.license_number
               FROM users u JOIN pharmacies ph ON ph.user_id = u.id WHERE u.id = ?""",
            (pharmacy["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "الملف غير موجود")
    return dict(row)


@app.get("/api/lab/me")
def my_lab_profile(lab=Depends(require_role(["lab"]))):
    with get_db() as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.phone, u.country, u.city, l.name, l.address,
                      l.license_number, l.offers_home_visits
               FROM users u JOIN labs l ON l.user_id = u.id WHERE u.id = ?""",
            (lab["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "الملف غير موجود")
    return dict(row)


@app.get("/api/secretaries/me")
def my_secretary_profile(user=Depends(require_role(["secretary"]))):
    with get_db() as conn:
        row = conn.execute(
            """SELECT s.full_name, s.doctor_id, s.can_confirm_attendance,
                      s.can_change_booking_status, s.can_manage_queue,
                      d.full_name AS doctor_name, d.clinic_name, d.specialty
               FROM secretaries s JOIN doctors d ON d.user_id = s.doctor_id
               WHERE s.user_id = ?""",
            (user["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "الملف غير موجود")
    return dict(row)


@app.get("/api/doctor/secretaries")
def list_secretaries(doctor=Depends(require_role(["doctor"]))):
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.user_id, s.full_name, s.can_confirm_attendance,
                      s.can_change_booking_status, s.can_manage_queue,
                      u.phone, u.email, u.is_active
               FROM secretaries s JOIN users u ON u.id = s.user_id
               WHERE s.doctor_id = ? ORDER BY s.full_name""",
            (doctor["id"],),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/patients/me")
def my_patient_profile(patient=Depends(require_role(["patient"]))):
    with get_db() as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.phone, u.country, u.city, u.latitude, u.longitude,
                      p.full_name, p.date_of_birth, p.gender
               FROM users u JOIN patients p ON p.user_id = u.id WHERE u.id = ?""",
            (patient["id"],),
        ).fetchone()
    if not row:
        raise HTTPException(404, "الملف غير موجود")
    return dict(row)


# =========================================================
#         بحث الدكاترة (متاح للمريض بعد تحديد موقعه)
# =========================================================

@app.get("/api/doctors/{doctor_id}", response_model=DoctorSearchResult)
def get_doctor(doctor_id: int):
    with get_db() as conn:
        row = conn.execute(
            """SELECT d.user_id, d.full_name, d.specialty, d.clinic_name, d.clinic_address,
                      d.consultation_fee, d.rating, d.rating_count, u.city, u.country
               FROM doctors d JOIN users u ON u.id = d.user_id
               WHERE d.user_id = ? AND u.is_active = 1""",
            (doctor_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="الطبيب غير موجود")
    return dict(row)


@app.get("/api/doctors/search", response_model=List[DoctorSearchResult])
def search_doctors(
    country: Optional[str] = None,
    city: Optional[str] = None,
    specialty: Optional[str] = None,
    max_price: Optional[float] = None,
    min_rating: Optional[float] = None,
):
    query = """
        SELECT d.user_id, d.full_name, d.specialty, d.clinic_name, d.clinic_address,
               d.consultation_fee, d.rating, d.rating_count, u.city, u.country
        FROM doctors d
        JOIN users u ON u.id = d.user_id
        WHERE u.is_active = 1
    """
    params = []

    if country:
        query += " AND u.country = ?"
        params.append(country)
    if city:
        query += " AND u.city = ?"
        params.append(city)
    if specialty:
        query += " AND d.specialty LIKE ?"
        params.append(f"%{specialty}%")
    if max_price is not None:
        query += " AND d.consultation_fee <= ?"
        params.append(max_price)
    if min_rating is not None:
        query += " AND d.rating >= ?"
        params.append(min_rating)

    query += " ORDER BY d.rating DESC"

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]
