"""
schemas.py
نماذج التحقق من البيانات الداخلة والخارجة لكل endpoint، مقسّمة حسب نوع
المستخدم عشان كل دور ياخد بالظبط المعلومات المهمة بتاعته وقت التسجيل.
"""

from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------- أساسيات مشتركة ----------

class LocationIn(BaseModel):
    latitude: float
    longitude: float


class LoginRequest(BaseModel):
    identifier: str = Field(..., description="الإيميل أو رقم الموبايل")
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: int


def _require_contact(email: Optional[str], phone: Optional[str]):
    if not email and not phone:
        raise ValueError("لازم تدخل إيميل أو رقم موبايل على الأقل")


# ---------- تسجيل المريض ----------

class PatientRegister(BaseModel):
    full_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str = Field(..., min_length=6)
    date_of_birth: Optional[str] = None  # YYYY-MM-DD
    gender: Optional[str] = None  # male / female
    national_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    country: Optional[str] = None
    city: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def check_contact(cls, v, info):
        return v


# ---------- تسجيل الطبيب ----------

class DoctorRegister(BaseModel):
    full_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str = Field(..., min_length=6)
    specialty: str
    license_number: str
    clinic_name: Optional[str] = None
    clinic_address: Optional[str] = None
    consultation_fee: float = 0
    bio: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    country: Optional[str] = None
    city: Optional[str] = None


# ---------- تسجيل السكرتير (بيتم من حساب الطبيب) ----------

class SecretaryRegister(BaseModel):
    full_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str = Field(..., min_length=6)
    can_confirm_attendance: bool = True
    can_change_booking_status: bool = True
    can_manage_queue: bool = True


# ---------- تسجيل الصيدلية ----------

class PharmacyRegister(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str = Field(..., min_length=6)
    license_number: str
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    country: Optional[str] = None
    city: Optional[str] = None


# ---------- تسجيل المعمل ----------

class LabRegister(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    password: str = Field(..., min_length=6)
    license_number: str
    address: Optional[str] = None
    offers_home_visits: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    country: Optional[str] = None
    city: Optional[str] = None


# ---------- بحث عن دكاترة ----------

class DoctorScheduleDay(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6, description="0=الأحد ... 6=السبت")
    start_time: str = Field(..., description="HH:MM")
    end_time: str = Field(..., description="HH:MM")
    slot_minutes: int = Field(20, ge=5, le=180)


class DoctorScheduleIn(BaseModel):
    days: list[DoctorScheduleDay]


class AppointmentBookIn(BaseModel):
    doctor_id: int
    date: str = Field(..., description="YYYY-MM-DD")
    start_time: str = Field(..., description="HH:MM")
    notes: Optional[str] = None


class AppointmentStatusUpdate(BaseModel):
    status: str = Field(..., description="confirmed / completed / cancelled_by_doctor / no_show")
    reason: Optional[str] = None


class PrescriptionItemIn(BaseModel):
    medicine_name: str
    dosage: Optional[str] = None
    duration: Optional[str] = None
    notes: Optional[str] = None


class ElectronicPrescriptionIn(BaseModel):
    patient_id: int
    appointment_id: Optional[int] = None
    items: list[PrescriptionItemIn]
    notes: Optional[str] = None


class PhotoPrescriptionIn(BaseModel):
    photo_base64: str
    notes: Optional[str] = None


class PharmacyPriceIn(BaseModel):
    total_price: float = Field(..., ge=0)
    all_items_available: bool
    pharmacy_notes: Optional[str] = None


class SelectPharmacyIn(BaseModel):
    request_id: int


class LabTestItemIn(BaseModel):
    test_name: str
    notes: Optional[str] = None


class LabReferralIn(BaseModel):
    patient_id: int
    lab_id: int
    appointment_id: Optional[int] = None
    tests: list[LabTestItemIn]
    home_visit_requested: bool = False
    notes: Optional[str] = None


class LabSelfRequestIn(BaseModel):
    lab_id: int
    tests: list[LabTestItemIn]
    home_visit_requested: bool = False
    notes: Optional[str] = None


class ScheduleVisitIn(BaseModel):
    scheduled_visit_time: str = Field(..., description="YYYY-MM-DD HH:MM")
    service_fee: Optional[float] = Field(None, ge=0, description="رسوم الزيارة المنزلية")


class FulfillmentIn(BaseModel):
    status: str = Field(..., description="preparing / out_for_delivery / delivered")


class UploadResultIn(BaseModel):
    result_file_base64: str
    result_notes: Optional[str] = None


class EhrUploadIn(BaseModel):
    title: str
    file_base64: str
    notes: Optional[str] = None


class RatingIn(BaseModel):
    stars: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None


class PaymentInitiateIn(BaseModel):
    purpose: str = Field(..., description="appointment / lab_home_visit / prescription_order")
    reference_id: int
    method: str = Field(..., description="vodafone_cash / instapay / fawry")


class ProductIn(BaseModel):
    name: str
    price: float = Field(..., ge=0)
    description: Optional[str] = None
    image_base64: Optional[str] = None
    is_active: bool = True


class RetailOrderItemIn(BaseModel):
    product_id: int
    quantity: int = Field(1, ge=1, le=50)


class RetailOrderIn(BaseModel):
    pharmacy_id: int
    items: list[RetailOrderItemIn]
    notes: Optional[str] = None


class RetailOrderStatusIn(BaseModel):
    status: str = Field(..., description="preparing / out_for_delivery / delivered / cancelled")


class DoctorSearchResult(BaseModel):
    user_id: int
    full_name: str
    specialty: str
    clinic_name: Optional[str]
    clinic_address: Optional[str]
    consultation_fee: float
    rating: float
    rating_count: int
    city: Optional[str]
    country: Optional[str]
