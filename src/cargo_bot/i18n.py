from typing import Literal


Language = Literal["uz", "ru"]
DEFAULT_LANGUAGE: Language = "uz"
SUPPORTED_LANGUAGES = frozenset({"uz", "ru"})
INITIAL_LANGUAGE_PROMPT = "Tilni tanlang / Выберите язык:"

LANGUAGE_BUTTONS = {
    "uz": "🇺🇿 O‘zbek tili",
    "ru": "🇷🇺 Русский язык",
}

TEXTS: dict[Language, dict[str, str]] = {
    "uz": {
        "language_prompt": "Tilni tanlang:",
        "private_only": "Maxfiylik uchun ro‘yxatdan o‘tish shaxsiy chatda bajarilishi kerak.",
        "privacy_notice": (
            "Ro‘yxatdan o‘tish uchun telefon raqamingiz, pasport raqamingiz va "
            "pasportingizning surati so‘raladi. Ma’lumotlar saqlanishidan oldin shifrlanadi."
        ),
        "menu_prompt": "Kerakli bo‘limni tanlang:",
        "register_button": "Ro‘yxatdan o‘tish",
        "settings_button": "Sozlamalar",
        "settings_title": "Sozlamalar",
        "language_label": "Til: O‘zbek tili",
        "change_language_button": "Tilni o‘zgartirish",
        "back_to_menu_button": "Asosiy menyuga qaytish",
        "phone_request": "Telefon raqamingizni quyidagi tugma orqali ulashing.",
        "share_phone_button": "Telefon raqamimni ulashish",
        "phone_own_only": "Iltimos, faqat o‘zingizning telefon raqamingizni ulashing.",
        "phone_invalid": "Telefon raqami noto‘g‘ri. Iltimos, tugma orqali qayta ulashing.",
        "passport_request": "Pasport seriyasi va raqamini kiriting, masalan AB1234567.",
        "passport_prompt": "Ikki harf va undan keyin yetti raqam kiriting, masalan AB1234567.",
        "passport_invalid": (
            "Format noto‘g‘ri. Ikki harf va undan keyin yetti raqam kiriting, "
            "masalan AB1234567."
        ),
        "photo_request": (
            "Pasportingizning shaxsiy ma’lumotlar sahifasini aniq suratga olib, "
            "foto yoki JPEG/PNG rasm sifatida yuboring."
        ),
        "photo_prompt": "Pasport sahifasining JPEG yoki PNG suratini yuboring.",
        "photo_too_large": "Rasm hajmi 5 MB dan oshmasligi kerak. Qayta yuboring.",
        "photo_invalid": "Bu JPEG yoki PNG rasm emas. Iltimos, qayta yuboring.",
        "registration_expired": "Ro‘yxatdan o‘tish muddati tugadi. Qayta boshlash uchun /start yuboring.",
        "registration_failed": "Ma’lumotlarni saqlab bo‘lmadi. Qayta urinish uchun /start yuboring.",
        "registration_complete": (
            "Ro‘yxatdan o‘tish yakunlandi. Kuzatuv kodingiz: {tracking_code}. "
            "Ma’lumotlaringiz shifrlangan holda saqlandi."
        ),
        "language_save_failed": "Til sozlamasini saqlab bo‘lmadi. Iltimos, qayta urinib ko‘ring.",
        "tracking_code_changed": "Yangi kuzatuv kodingiz: {tracking_code}.",
    },
    "ru": {
        "language_prompt": "Выберите язык:",
        "private_only": "Для конфиденциальности регистрация доступна только в личном чате.",
        "privacy_notice": (
            "Для регистрации потребуется ваш номер телефона, номер паспорта и "
            "фотография паспорта. Данные шифруются перед сохранением."
        ),
        "menu_prompt": "Выберите нужный раздел:",
        "register_button": "Регистрация",
        "settings_button": "Настройки",
        "settings_title": "Настройки",
        "language_label": "Язык: Русский",
        "change_language_button": "Изменить язык",
        "back_to_menu_button": "В главное меню",
        "phone_request": "Поделитесь своим номером телефона с помощью кнопки ниже.",
        "share_phone_button": "Поделиться моим номером",
        "phone_own_only": "Пожалуйста, отправьте только свой номер телефона.",
        "phone_invalid": "Номер телефона указан неверно. Поделитесь им с помощью кнопки.",
        "passport_request": "Введите серию и номер паспорта, например AB1234567.",
        "passport_prompt": "Введите две буквы и семь цифр, например AB1234567.",
        "passport_invalid": (
            "Неверный формат. Введите две буквы и семь цифр, например AB1234567."
        ),
        "photo_request": (
            "Загрузите чёткую фотографию страницы паспорта с личными данными "
            "как фото или изображение JPEG/PNG."
        ),
        "photo_prompt": "Загрузите фотографию страницы паспорта в формате JPEG или PNG.",
        "photo_too_large": "Размер изображения не должен превышать 5 МБ. Загрузите его снова.",
        "photo_invalid": "Это не изображение JPEG или PNG. Загрузите файл снова.",
        "registration_expired": "Срок регистрации истёк. Отправьте /start, чтобы начать заново.",
        "registration_failed": "Не удалось сохранить данные. Отправьте /start и попробуйте снова.",
        "registration_complete": (
            "Регистрация завершена. Ваш код отслеживания: {tracking_code}. "
            "Данные сохранены в зашифрованном виде."
        ),
        "language_save_failed": "Не удалось сохранить язык. Попробуйте ещё раз.",
        "tracking_code_changed": "Ваш новый код отслеживания: {tracking_code}.",
    },
}


def text(language: str, key: str, **values: str) -> str:
    selected_language: Language = (
        language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    )
    return TEXTS[selected_language][key].format(**values)
