"""
database.py
طبقة الاتصال بقاعدة البيانات (SQLite) وإنشاء الجداول الأساسية للمنظومة الطبية.
كل الأدوار (مريض / طبيب / سكرتير / صيدلية / معمل) بيبقى ليها صف في جدول users
المشترك، وبعدين صف تفصيلي في جدول الدور بتاعها (patients / doctors / ...).
"""

import os
import sqlite3
from contextlib import contextmanager

# لو الاستضافة بتدّيك مجلد تخزين ثابت (Volume)، حط مساره في متغير بيئة DB_DIR
# عشان قاعدة البيانات متتمسحش مع كل نشر جديد. من غيره بتتخزن جنب الكود عادي.
DB_DIR = os.environ.get("DB_DIR", ".")
DB_PATH = os.path.join(DB_DIR, "medical_platform.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _safe_alter(cur, sql: str):
    """يضيف عمود جديد لجدول موجود من غير ما يبوّظ قاعدة بيانات شغّالة بالفعل
    (لو العمود موجود قبل كده، بيتجاهل الخطأ بهدوء)."""
    try:
        cur.execute(sql)
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def add_notification(conn, user_id: int, title: str, body: str = None):
    """إشعار داخل التطبيق لأي مستخدم (بيتعرض من /api/notifications/mine)."""
    conn.execute(
        "INSERT INTO notifications (user_id, title, body) VALUES (?, ?, ?)",
        (user_id, title, body),
    )


def init_db():
    with get_db() as conn:
        cur = conn.cursor()

        # الجدول المشترك لكل المستخدمين مهما كان دورهم
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                phone TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN
                    ('patient','doctor','secretary','pharmacy','lab')),
                country TEXT,
                city TEXT,
                latitude REAL,
                longitude REAL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # بيانات المريض
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS patients (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                full_name TEXT NOT NULL,
                date_of_birth TEXT,
                gender TEXT CHECK(gender IN ('male','female') ),
                national_id TEXT
            )
            """
        )

        # بيانات الطبيب
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS doctors (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                full_name TEXT NOT NULL,
                specialty TEXT NOT NULL,
                license_number TEXT NOT NULL,
                clinic_name TEXT,
                clinic_address TEXT,
                consultation_fee REAL NOT NULL DEFAULT 0,
                bio TEXT,
                rating REAL NOT NULL DEFAULT 0,
                rating_count INTEGER NOT NULL DEFAULT 0,
                is_verified INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # حساب السكرتارية - تابع لطبيب معين وبصلاحيات محدودة
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS secretaries (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                doctor_id INTEGER NOT NULL REFERENCES doctors(user_id) ON DELETE CASCADE,
                full_name TEXT NOT NULL,
                can_confirm_attendance INTEGER NOT NULL DEFAULT 1,
                can_change_booking_status INTEGER NOT NULL DEFAULT 1,
                can_manage_queue INTEGER NOT NULL DEFAULT 1
            )
            """
        )

        # بيانات الصيدلية
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pharmacies (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                license_number TEXT NOT NULL,
                address TEXT,
                is_verified INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # بيانات المعمل
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS labs (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                license_number TEXT NOT NULL,
                address TEXT,
                offers_home_visits INTEGER NOT NULL DEFAULT 0,
                is_verified INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # الجدول الزمني الأسبوعي للطبيب (ساعات العمل لكل يوم)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS doctor_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_id INTEGER NOT NULL REFERENCES doctors(user_id) ON DELETE CASCADE,
                day_of_week INTEGER NOT NULL CHECK(day_of_week BETWEEN 0 AND 6),
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                slot_minutes INTEGER NOT NULL DEFAULT 20,
                UNIQUE(doctor_id, day_of_week)
            )
            """
        )

        # الحجوزات (Appointments)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doctor_id INTEGER NOT NULL REFERENCES doctors(user_id) ON DELETE CASCADE,
                patient_id INTEGER NOT NULL REFERENCES patients(user_id) ON DELETE CASCADE,
                appointment_date TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
                    ('pending','confirmed','completed',
                     'cancelled_by_doctor','cancelled_by_patient','no_show')),
                fee REAL NOT NULL DEFAULT 0,
                notes TEXT,
                cancel_reason TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # الروشتة الإلكترونية (من الطبيب) أو المصورة (من المريض)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL REFERENCES patients(user_id) ON DELETE CASCADE,
                doctor_id INTEGER REFERENCES doctors(user_id) ON DELETE SET NULL,
                appointment_id INTEGER REFERENCES appointments(id) ON DELETE SET NULL,
                type TEXT NOT NULL CHECK(type IN ('electronic','photo')),
                photo_base64 TEXT,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN
                    ('draft','sent_to_pharmacies','confirmed','cancelled')),
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # بنود الروشتة الإلكترونية (اسم الدواء / الجرعة / المدة)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prescription_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prescription_id INTEGER NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
                medicine_name TEXT NOT NULL,
                dosage TEXT,
                duration TEXT,
                notes TEXT
            )
            """
        )

        # الروشتة بعد إرسالها لكل صيدلية قريبة - كل صف هو عرض من صيدلية واحدة
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prescription_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prescription_id INTEGER NOT NULL REFERENCES prescriptions(id) ON DELETE CASCADE,
                pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(user_id) ON DELETE CASCADE,
                distance_km REAL,
                status TEXT NOT NULL DEFAULT 'pending_price' CHECK(status IN
                    ('pending_price','priced','declined','selected','not_selected')),
                total_price REAL,
                all_items_available INTEGER,
                pharmacy_notes TEXT,
                responded_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(prescription_id, pharmacy_id)
            )
            """
        )

        # طلبات التحاليل (تحويل من طبيب أو طلب مباشر من المريض)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lab_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL REFERENCES patients(user_id) ON DELETE CASCADE,
                doctor_id INTEGER REFERENCES doctors(user_id) ON DELETE SET NULL,
                lab_id INTEGER NOT NULL REFERENCES labs(user_id) ON DELETE CASCADE,
                appointment_id INTEGER REFERENCES appointments(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN
                    ('requested','home_visit_scheduled','completed','cancelled')),
                home_visit_requested INTEGER NOT NULL DEFAULT 0,
                scheduled_visit_time TEXT,
                result_file_base64 TEXT,
                result_notes TEXT,
                notes TEXT,
                doctor_viewed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lab_request_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lab_request_id INTEGER NOT NULL REFERENCES lab_requests(id) ON DELETE CASCADE,
                test_name TEXT NOT NULL,
                notes TEXT
            )
            """
        )

        # الملف الطبي الموحد للمريض (EHR) - نتائج تحاليل تلقائيًا + رفع المريض بنفسه
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ehr_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL REFERENCES patients(user_id) ON DELETE CASCADE,
                source TEXT NOT NULL CHECK(source IN ('lab_result','patient_upload')),
                reference_id INTEGER,
                title TEXT NOT NULL,
                file_base64 TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # تقييمات المرضى للأطباء - تقييم واحد لكل حجز مكتمل
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                appointment_id INTEGER NOT NULL UNIQUE REFERENCES appointments(id) ON DELETE CASCADE,
                patient_id INTEGER NOT NULL REFERENCES patients(user_id) ON DELETE CASCADE,
                doctor_id INTEGER NOT NULL REFERENCES doctors(user_id) ON DELETE CASCADE,
                stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
                comment TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # المدفوعات (بوابات فودافون كاش / InstaPay / فوري)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL REFERENCES patients(user_id) ON DELETE CASCADE,
                purpose TEXT NOT NULL CHECK(purpose IN
                    ('appointment','lab_home_visit','prescription_order','retail_order')),
                reference_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL CHECK(method IN ('vodafone_cash','instapay','fawry')),
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
                    ('pending','paid','failed','refunded')),
                gateway_reference TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                paid_at TEXT
            )
            """
        )

        # الإشعارات (تذكير المواعيد وغيرها) - عامة لأي دور
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                body TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # أعمدة إضافية لدعم الدفع والتذكيرات (Migration آمنة)
        _safe_alter(cur, "ALTER TABLE appointments ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'unpaid'")
        _safe_alter(cur, "ALTER TABLE appointments ADD COLUMN reminder_24h_sent_at TEXT")
        _safe_alter(cur, "ALTER TABLE appointments ADD COLUMN reminder_2h_sent_at TEXT")
        _safe_alter(cur, "ALTER TABLE lab_requests ADD COLUMN service_fee REAL NOT NULL DEFAULT 0")
        _safe_alter(cur, "ALTER TABLE lab_requests ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'unpaid'")
        _safe_alter(cur, "ALTER TABLE prescription_requests ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'unpaid'")
        _safe_alter(cur, "ALTER TABLE prescription_requests ADD COLUMN fulfillment_status TEXT NOT NULL DEFAULT 'none'")
        _safe_alter(cur, "ALTER TABLE prescription_requests ADD COLUMN selected_at TEXT")

        # منتجات المتجر الطبي للصيدلية (عناية شخصية/مستحضرات - مش أدوية بروشتة)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pharmacy_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(user_id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                description TEXT,
                image_base64 TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        # طلبات المتجر (منفصلة عن طلبات الروشتة)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS retail_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL REFERENCES patients(user_id) ON DELETE CASCADE,
                pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(user_id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'placed' CHECK(status IN
                    ('placed','preparing','out_for_delivery','delivered','cancelled')),
                total_price REAL NOT NULL DEFAULT 0,
                payment_status TEXT NOT NULL DEFAULT 'unpaid',
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS retail_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES retail_orders(id) ON DELETE CASCADE,
                product_id INTEGER REFERENCES pharmacy_products(id) ON DELETE SET NULL,
                product_name TEXT NOT NULL,
                unit_price REAL NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1
            )
            """
        )

        # ترقية جدول payments القديم لو كان لسه من غير 'retail_order' ضمن CHECK
        # (SQLite مبيدعمش تعديل CHECK بـ ALTER، فلو الجدول قديم بنعيد بناءه بأمان)
        old_schema = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='payments'"
        ).fetchone()
        if old_schema and "retail_order" not in (old_schema["sql"] or ""):
            cur.execute("ALTER TABLE payments RENAME TO payments_old")
            cur.execute(
                """
                CREATE TABLE payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER NOT NULL REFERENCES patients(user_id) ON DELETE CASCADE,
                    purpose TEXT NOT NULL CHECK(purpose IN
                        ('appointment','lab_home_visit','prescription_order','retail_order')),
                    reference_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    method TEXT NOT NULL CHECK(method IN ('vodafone_cash','instapay','fawry')),
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
                        ('pending','paid','failed','refunded')),
                    gateway_reference TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    paid_at TEXT
                )
                """
            )
            cur.execute(
                """INSERT INTO payments (id, patient_id, purpose, reference_id, amount, method,
                                          status, gateway_reference, created_at, paid_at)
                   SELECT id, patient_id, purpose, reference_id, amount, method,
                          status, gateway_reference, created_at, paid_at FROM payments_old"""
            )
            cur.execute("DROP TABLE payments_old")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_country_city ON users(country, city)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_doctors_specialty ON doctors(specialty)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_appts_doctor_date ON appointments(doctor_id, appointment_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_appts_patient ON appointments(patient_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_presc_patient ON prescriptions(patient_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_presc_req_pharmacy ON prescription_requests(pharmacy_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_presc_req_prescription ON prescription_requests(prescription_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_lab_req_lab ON lab_requests(lab_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_lab_req_patient ON lab_requests(patient_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_lab_req_doctor ON lab_requests(doctor_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ehr_patient ON ehr_records(patient_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ratings_doctor ON ratings(doctor_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_patient ON payments(patient_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_products_pharmacy ON pharmacy_products(pharmacy_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_retail_orders_pharmacy ON retail_orders(pharmacy_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_retail_orders_patient ON retail_orders(patient_id)")

    print(f"[DB] تم تجهيز قاعدة البيانات في {DB_PATH}")
