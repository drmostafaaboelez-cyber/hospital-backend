# نشر الباك اند كحاوية Docker - يشتغل على Railway/Render/Fly.io أو أي سيرفر بيدعم Docker
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# قاعدة البيانات هتتحفظ في /app/data عشان تفضل موجودة بعد كل نشر جديد
# (اعمل Volume في Railway/Fly.io وحطه على المسار ده)
ENV DB_DIR=/app/data
RUN mkdir -p /app/data

# المنصة بتدّيك رقم البورت في متغير PORT - لو مش موجود بنستخدم 8000 محليًا
ENV PORT=8000
EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
