"""Safe replies used when a guard replaces the model's draft, in the customer's language
(issues log ISS-19). Unknown languages fall back to English and the agent is told."""
from __future__ import annotations

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "holding": "Thank you for contacting Valetax support. We have received your message and passed it "
                   "to the responsible team, who will review it and get back to you. We are not able to "
                   "confirm an outcome in this message.",
        "decline": "Thank you for your question. Valetax support cannot provide trading advice, signals, "
                   "price forecasts or recommendations on leverage or strategy; trading decisions are yours "
                   "to make. We are happy to help with deposits, withdrawals, account opening and verification.",
        "abstain": "Thank you for your question. Let us check this for you; a member of our support team "
                   "will follow up with the exact details shortly.",
    },
    "ru": {
        "holding": "Спасибо за обращение в поддержку Valetax. Мы получили ваше сообщение и передали его "
                   "ответственной команде, которая рассмотрит его и свяжется с вами. В этом сообщении мы не "
                   "можем подтвердить результат рассмотрения.",
        "decline": "Спасибо за вопрос. Поддержка Valetax не даёт торговых советов, сигналов, прогнозов цен и "
                   "рекомендаций по кредитному плечу или стратегии: торговые решения принимаете вы. Мы с "
                   "радостью поможем с пополнением, выводом средств, открытием счёта и верификацией.",
        "abstain": "Спасибо за вопрос. Мы уточним информацию; сотрудник поддержки скоро ответит вам с "
                   "точными деталями.",
    },
    "es": {
        "holding": "Gracias por contactar con el soporte de Valetax. Hemos recibido su mensaje y lo hemos "
                   "trasladado al equipo responsable, que lo revisará y se pondrá en contacto con usted. En "
                   "este mensaje no podemos confirmar un resultado.",
        "decline": "Gracias por su pregunta. El soporte de Valetax no puede ofrecer asesoramiento de trading, "
                   "señales, previsiones de precios ni recomendaciones de apalancamiento o estrategia; las "
                   "decisiones de trading son suyas. Con gusto le ayudamos con depósitos, retiros, apertura "
                   "de cuenta y verificación.",
        "abstain": "Gracias por su pregunta. Vamos a comprobarlo; un miembro de nuestro equipo de soporte le "
                   "responderá en breve con los detalles exactos.",
    },
    "pt": {
        "holding": "Obrigado por contactar o suporte da Valetax. Recebemos a sua mensagem e encaminhámo-la "
                   "para a equipa responsável, que a irá analisar e responder-lhe. Nesta mensagem não podemos "
                   "confirmar um resultado.",
        "decline": "Obrigado pela sua pergunta. O suporte da Valetax não pode dar aconselhamento de trading, "
                   "sinais, previsões de preços nem recomendações de alavancagem ou estratégia; as decisões "
                   "de trading são suas. Teremos todo o gosto em ajudar com depósitos, levantamentos, "
                   "abertura de conta e verificação.",
        "abstain": "Obrigado pela sua pergunta. Vamos verificar; um membro da nossa equipa de suporte "
                   "responderá em breve com os detalhes exatos.",
    },
    "id": {
        "holding": "Terima kasih telah menghubungi dukungan Valetax. Pesan Anda telah kami terima dan "
                   "teruskan ke tim terkait, yang akan meninjaunya dan menghubungi Anda kembali. Kami belum "
                   "dapat mengonfirmasi hasilnya dalam pesan ini.",
        "decline": "Terima kasih atas pertanyaan Anda. Dukungan Valetax tidak dapat memberikan saran trading, "
                   "sinyal, prediksi harga, atau rekomendasi leverage maupun strategi; keputusan trading "
                   "sepenuhnya milik Anda. Kami siap membantu terkait deposit, penarikan, pembukaan akun, "
                   "dan verifikasi.",
        "abstain": "Terima kasih atas pertanyaan Anda. Kami akan memeriksanya; tim dukungan kami akan segera "
                   "menghubungi Anda dengan detail yang tepat.",
    },
    "ar": {
        "holding": "شكرًا لتواصلك مع دعم Valetax. لقد استلمنا رسالتك وأحلناها إلى الفريق المختص، الذي سيراجعها "
                   "ويتواصل معك. لا يمكننا تأكيد أي نتيجة في هذه الرسالة.",
        "decline": "شكرًا لسؤالك. لا يقدّم دعم Valetax نصائح تداول أو إشارات أو توقعات للأسعار أو توصيات بشأن "
                   "الرافعة المالية أو الاستراتيجية؛ قرارات التداول تعود إليك. يسعدنا مساعدتك في الإيداع والسحب "
                   "وفتح الحساب والتحقق.",
        "abstain": "شكرًا لسؤالك. سنتحقق من ذلك، وسيتواصل معك أحد أعضاء فريق الدعم قريبًا بالتفاصيل الدقيقة.",
    },
    "vi": {
        "holding": "Cảm ơn bạn đã liên hệ bộ phận hỗ trợ Valetax. Chúng tôi đã nhận được tin nhắn và chuyển đến "
                   "bộ phận phụ trách để xem xét và phản hồi bạn. Chúng tôi chưa thể xác nhận kết quả trong tin "
                   "nhắn này.",
        "decline": "Cảm ơn câu hỏi của bạn. Bộ phận hỗ trợ Valetax không đưa ra lời khuyên giao dịch, tín hiệu, "
                   "dự báo giá hay khuyến nghị về đòn bẩy hoặc chiến lược; quyết định giao dịch là của bạn. "
                   "Chúng tôi sẵn sàng hỗ trợ về nạp tiền, rút tiền, mở tài khoản và xác minh.",
        "abstain": "Cảm ơn câu hỏi của bạn. Chúng tôi sẽ kiểm tra và nhân viên hỗ trợ sẽ sớm phản hồi bạn với "
                   "thông tin chính xác.",
    },
}


def get(kind: str, language: str) -> tuple[str, bool]:
    """Return (text, localized). kind: holding | decline | abstain."""
    lang = (language or "en").lower()[:2]
    if lang in TEMPLATES:
        return TEMPLATES[lang][kind], True
    return TEMPLATES["en"][kind], False


def all_texts() -> set[str]:
    return {t for lang in TEMPLATES.values() for t in lang.values()}
