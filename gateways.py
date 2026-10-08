"""
gateways.py
طبقة موحّدة لبوابات الدفع المحلية. كل بوابة كلاس منفصل بنفس الواجهة
(initiate/verify) عشان الكود اللي بيستخدمها متعرفش تفاصيل كل بوابة.

مهم جدًا: مفيش بيانات اعتماد تاجر حقيقية (API keys) هنا، ومفيش اتصال
إنترنت في بيئة التطوير دي أصلاً عشان أقدر أكلم أي بوابة فعليًا أو أختبرها.
عشان كده كل بوابة شغالة بـ "وضع المحاكاة" (Mock Mode) افتراضيًا: بترجع
مرجع عملية وهمي وتعليمات نصية بدل ما تكلم سيرفر حقيقي، عشان تقدر تبني
وتختبر باقي المنظومة (الحجز/الروشتة/التحليل) من غيرها.

لما يبقى عندك حساب تاجر حقيقي مع أي بوابة:
  1) حط الـ API keys بتاعتها في متغيرات البيئة المذكورة تحت كل كلاس.
  2) استبدل جسم initiate/verify بالنداء الحقيقي لسيرفر البوابة (المكان
     محدد بـ TODO في كل كلاس).
  3) في الإنتاج، التأكيد (verify) المفروض يجي أساسًا من Webhook حقيقي
     من البوابة نفسها بعد التحقق من التوقيع، مش من نداء يدوي من العميل
     (endpoint التأكيد الحالي في payments.py معمول للتطوير/الاختبار بس).
"""

import os
import uuid
from typing import Optional


class BaseGateway:
    name = "base"

    def initiate(self, amount: float, patient_phone: Optional[str], note: str) -> dict:
        raise NotImplementedError

    def verify(self, gateway_reference: str) -> str:
        """يرجع 'paid' أو 'pending' أو 'failed'."""
        raise NotImplementedError

    def _is_mock_ref(self, ref: str) -> bool:
        return ref.startswith("MOCK-")


class VodafoneCashGateway(BaseGateway):
    name = "vodafone_cash"
    MERCHANT_NUMBER = os.environ.get("VF_CASH_MERCHANT_NUMBER", "")
    API_KEY = os.environ.get("VF_CASH_API_KEY", "")

    def initiate(self, amount, patient_phone, note):
        if not self.API_KEY:
            ref = f"MOCK-VFCASH-{uuid.uuid4().hex[:10].upper()}"
            return {
                "gateway_reference": ref,
                "mock": True,
                "instructions": (
                    f"حوّل {amount} جنيه من محفظة فودافون كاش لرقم التاجر "
                    f"(محتاج يتظبط) واكتب الكود {ref} في خانة الملاحظات"
                ),
            }
        # TODO: نداء حقيقي لـ Vodafone Cash Merchant API هنا لما MERCHANT_NUMBER/API_KEY يتظبطوا
        raise NotImplementedError("تكامل فودافون كاش الحقيقي لسه مش متظبط")

    def verify(self, gateway_reference):
        if self._is_mock_ref(gateway_reference):
            return "paid"  # في وضع المحاكاة بس - أي مرجع MOCK بيتعتبر مدفوع
        # TODO: استعلام حقيقي عن حالة العملية من Vodafone Cash هنا
        raise NotImplementedError("التحقق الحقيقي من فودافون كاش لسه مش متظبط")


class InstaPayGateway(BaseGateway):
    name = "instapay"
    IPN_ENDPOINT = os.environ.get("INSTAPAY_IPN_ENDPOINT", "")
    API_KEY = os.environ.get("INSTAPAY_API_KEY", "")

    def initiate(self, amount, patient_phone, note):
        if not self.API_KEY:
            ref = f"MOCK-INSTAPAY-{uuid.uuid4().hex[:10].upper()}"
            return {
                "gateway_reference": ref,
                "mock": True,
                "instructions": (
                    f"حوّل {amount} جنيه InstaPay على حساب التاجر (محتاج يتظبط) "
                    f"واكتب الكود {ref} في الرسالة"
                ),
            }
        # TODO: نداء حقيقي لـ InstaPay IPN/API هنا لما IPN_ENDPOINT/API_KEY يتظبطوا
        raise NotImplementedError("تكامل InstaPay الحقيقي لسه مش متظبط")

    def verify(self, gateway_reference):
        if self._is_mock_ref(gateway_reference):
            return "paid"
        # TODO: استعلام حقيقي عن حالة العملية من InstaPay هنا
        raise NotImplementedError("التحقق الحقيقي من InstaPay لسه مش متظبط")


class FawryGateway(BaseGateway):
    name = "fawry"
    MERCHANT_CODE = os.environ.get("FAWRY_MERCHANT_CODE", "")
    SECURITY_KEY = os.environ.get("FAWRY_SECURITY_KEY", "")

    def initiate(self, amount, patient_phone, note):
        if not self.SECURITY_KEY:
            ref = f"MOCK-FAWRY-{uuid.uuid4().hex[:10].upper()}"
            return {
                "gateway_reference": ref,
                "mock": True,
                "instructions": f"كود فوري: {ref} - ادفع {amount} جنيه من أي منفذ فوري أو تطبيق فوري",
            }
        # TODO: نداء حقيقي لـ FawryPay API هنا لما MERCHANT_CODE/SECURITY_KEY يتظبطوا
        raise NotImplementedError("تكامل فوري الحقيقي لسه مش متظبط")

    def verify(self, gateway_reference):
        if self._is_mock_ref(gateway_reference):
            return "paid"
        # TODO: استعلام حقيقي عن حالة العملية من فوري هنا (أو استقبالها عن طريق Webhook)
        raise NotImplementedError("التحقق الحقيقي من فوري لسه مش متظبط")


GATEWAYS = {
    "vodafone_cash": VodafoneCashGateway(),
    "instapay": InstaPayGateway(),
    "fawry": FawryGateway(),
}
