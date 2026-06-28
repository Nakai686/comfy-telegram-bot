"""Клавиатуры бота: нижнее reply-меню + inline-кнопки (пресеты, действия, настройки)."""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from prompts import STYLE_PRESETS

# Тексты кнопок нижнего меню (по ним матчим хендлеры — менять синхронно с bot.py)
BTN_WIZARD = "🧩 Собрать промпт"
BTN_HELP = "💡 Помощь с промптами"
BTN_IMPROVE = "✨ Улучшить промпт"
BTN_SETTINGS = "⚙️ Настройки"
BTN_PHOTO = "🖼 Редактировать фото"


def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_WIZARD), KeyboardButton(text=BTN_HELP)],
            [KeyboardButton(text=BTN_IMPROVE), KeyboardButton(text=BTN_PHOTO)],
            [KeyboardButton(text=BTN_SETTINGS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши промпт или пришли фото…",
    )


def wizard_options_kb(options, allow_skip, allow_finish):
    """Кнопки популярных вариантов шага + Пропустить/Хватит."""
    kb = InlineKeyboardBuilder()
    for i, (label, _v) in enumerate(options):
        kb.button(text=label, callback_data=f"wiz:opt:{i}")
    if allow_skip:
        kb.button(text="⏭ Пропустить", callback_data="wiz:skip")
    if allow_finish:
        kb.button(text="✅ Хватит, рисуй", callback_data="wiz:finish")
    kb.adjust(2)
    return kb.as_markup()


def wizard_after_subject_kb():
    """После 1-го шага: рисовать сразу или продолжить уточнять."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🎨 Нарисовать сейчас", callback_data="wiz:finish")
    kb.button(text="➕ Добавить детали", callback_data="wiz:next")
    kb.adjust(2)
    return kb.as_markup()


def wizard_style_kb(allow_finish=True):
    kb = InlineKeyboardBuilder()
    for key, (label, _) in STYLE_PRESETS.items():
        if key == "off":
            continue
        kb.button(text=label, callback_data=f"wiz:style:{key}")
    kb.button(text="⏭ Без стиля", callback_data="wiz:skip")
    if allow_finish:
        kb.button(text="✅ Хватит, рисуй", callback_data="wiz:finish")
    kb.adjust(2)
    return kb.as_markup()


# Быстрые правки для Kontext (label, инструкция). Инструкция уйдёт в улучшайзер/перевод.
KONTEXT_QUICK = [
    ("🌲 Фон → лес", "поменяй фон на густой лес, сохрани человека и позу"),
    ("🌃 Фон → ночной город", "поменяй фон на ночной неоновый город, сохрани человека и позу"),
    ("🏖 Фон → пляж", "поменяй фон на солнечный пляж, сохрани человека и позу"),
    ("🎨 Стиль аниме", "сделай в стиле аниме, сохрани композицию"),
    ("🖌 Стиль арт", "сделай в стиле цифровой живописи, сохрани композицию"),
    ("✨ Резче/детальнее", "повысь детализацию и резкость, ничего не меняя по содержанию"),
]


def kontext_quick_kb():
    kb = InlineKeyboardBuilder()
    for i, (label, _v) in enumerate(KONTEXT_QUICK):
        kb.button(text=label, callback_data=f"kfix:{i}")
    kb.adjust(2)
    return kb.as_markup()


def request_access_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📨 Запросить доступ", callback_data="request_access")
    return kb.as_markup()


def approve_kb(uid):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Разрешить", callback_data=f"approve:{uid}")
    kb.button(text="❌ Отклонить", callback_data=f"deny:{uid}")
    kb.adjust(2)
    return kb.as_markup()


def style_kb(active_key):
    kb = InlineKeyboardBuilder()
    for key, (label, _) in STYLE_PRESETS.items():
        mark = "✅ " if key == active_key else ""
        kb.button(text=f"{mark}{label}", callback_data=f"style:{key}")
    kb.adjust(2)
    return kb.as_markup()


def after_image_kb():
    """Кнопки под готовой картинкой."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Ещё вариант", callback_data="regen")
    kb.button(text="✏️ Изменить это фото", callback_data="edit_this")
    kb.adjust(2)
    return kb.as_markup()


def settings_kb(s, translate_on, improve_on, preserve_face):
    """s — словарь настроек пользователя (width/height/steps)."""
    kb = InlineKeyboardBuilder()
    # шаги
    for n in (12, 20, 28):
        mark = "✅ " if s["steps"] == n else ""
        kb.button(text=f"{mark}{n} шагов", callback_data=f"set:steps:{n}")
    # размеры
    sizes = [("1024x1024", "⬛ Квадрат"), ("832x1216", "📱 Портрет"),
             ("1216x832", "🖥 Пейзаж")]
    for val, label in sizes:
        w, h = val.split("x")
        mark = "✅ " if (s["width"] == int(w) and s["height"] == int(h)) else ""
        kb.button(text=f"{mark}{label}", callback_data=f"set:size:{val}")
    # авто-улучшение промпта
    imp = "✨ Авто-улучшение: ВКЛ" if improve_on else "✨ Авто-улучшение: выкл"
    kb.button(text=imp, callback_data="set:improve")
    # перевод
    tr = "🔤 Перевод: ВКЛ" if translate_on else "🔤 Перевод: выкл"
    kb.button(text=tr, callback_data="set:translate")
    # сохранение лица при редактировании фото (Kontext)
    fc = "🧑 Лицо при ред. фото: ВКЛ" if preserve_face else "🧑 Лицо при ред. фото: выкл"
    kb.button(text=fc, callback_data="set:face")
    kb.adjust(3, 3, 1, 1, 1)
    return kb.as_markup()
