"""Перевод RU→EN, улучшение промптов через HF, пресеты стилей и тексты помощи."""
import asyncio
import logging
import re

import aiohttp

log = logging.getLogger("prompts")

_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


def has_cyrillic(text):
    return bool(_CYRILLIC.search(text or ""))


async def translate_ru_en(text):
    """RU→EN через deep-translator (Google, без ключа). Блокирующий вызов — в отдельном потоке."""
    def _do():
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source="auto", target="en").translate(text)
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        log.warning("Перевод не удался: %s", e)
        return text  # на крайний случай — как есть


class ImproveError(Exception):
    pass


_IMPROVE_SYSTEM = (
    "You are a prompt engineer for the Flux text-to-image model. "
    "Turn the user's short idea (it may be in Russian) into ONE vivid, detailed "
    "English image prompt. Use natural descriptive language, not a list of tags. "
    "Include subject, setting, lighting, mood, composition and camera/style details. "
    "Output ONLY the final prompt — no quotes, no explanations, no preamble."
)


_IMPROVE_EDIT_SYSTEM = (
    "You are a prompt engineer for the FLUX Kontext image-EDITING model. "
    "The user gives a short edit instruction (often in Russian) for an EXISTING photo. "
    "Rewrite it as ONE clear, specific English editing instruction. Rules: translate to "
    "English; be concrete about WHAT changes and the new content/scene; explicitly keep the "
    "main subject's face, identity and pose unchanged unless the user is editing them; use "
    "'change'/'replace', avoid 'transform'. Keep under 50 words. "
    "Output ONLY the instruction, no quotes, no preamble."
)


async def improve_prompt(idea, hf_token, model, system=_IMPROVE_SYSTEM):
    """Расширяет идею в детальный английский промпт через HF Inference.

    system задаёт режим: генерация (_IMPROVE_SYSTEM) или редактирование (_IMPROVE_EDIT_SYSTEM).
    Бросает ImproveError при любой проблеме — вызывающий код делает фолбэк на перевод.
    """
    url = "https://router.huggingface.co/v1/chat/completions"
    headers = {"Authorization": f"Bearer {hf_token}"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": idea},
        ],
        "max_tokens": 300,
        "temperature": 0.7,
    }
    timeout = aiohttp.ClientTimeout(total=45)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, headers=headers, json=payload) as r:
                if r.status != 200:
                    text = await r.text()
                    raise ImproveError(f"HF {r.status}: {text[:200]}")
                data = await r.json()
        out = data["choices"][0]["message"]["content"].strip()
        out = out.strip('"').strip()
        if not out:
            raise ImproveError("пустой ответ")
        return out
    except ImproveError:
        raise
    except Exception as e:
        raise ImproveError(str(e))


async def improve_edit_prompt(idea, hf_token, model):
    """Улучшает короткую инструкцию редактирования в чёткую Kontext-инструкцию."""
    return await improve_prompt(idea, hf_token, model, system=_IMPROVE_EDIT_SYSTEM)


# Пресеты стилей: ключ -> (подпись для кнопки, суффикс для промпта)
STYLE_PRESETS = {
    "off": ("🚫 Без стиля", ""),
    "photo": ("📷 Фотореализм",
              "photorealistic, shot on Canon EOS R5, 85mm f/1.4, natural lighting, "
              "ultra sharp focus, high detail, 8k"),
    "cinematic": ("🎬 Кино",
                  "cinematic film still, dramatic lighting, shallow depth of field, "
                  "anamorphic, professional color grading, moody atmosphere"),
    "anime": ("🌸 Аниме",
              "anime style, vibrant colors, clean linework, studio-quality 2D "
              "illustration, expressive"),
    "art": ("🎨 Арт",
            "digital painting, trending on artstation, intricate detail, "
            "expressive brushwork, dramatic composition"),
    "cyberpunk": ("🌆 Киберпанк",
                  "cyberpunk aesthetic, glowing neon lights, rain-soaked streets, "
                  "futuristic megacity at night, high contrast, volumetric light"),
    "3d": ("🧊 3D-рендер",
           "3D render, octane render, physically based materials, soft global "
           "illumination, subsurface scattering, high detail"),
}


def apply_style(prompt, style_key):
    suffix = STYLE_PRESETS.get(style_key, ("", ""))[1]
    if suffix:
        return f"{prompt}, {suffix}"
    return prompt


HELP_TEXT = (
    "💡 <b>Как писать промпты для Flux</b>\n\n"
    "Flux любит <b>живой описательный текст целыми фразами</b>, а не теги через запятую.\n"
    "Можешь писать <b>по-русски</b> — я сам переведу на английский 🔤\n\n"
    "<b>Структура хорошего промпта:</b>\n"
    "• <b>что/кто</b> — главный объект\n"
    "• <b>где</b> — обстановка, фон\n"
    "• <b>свет</b> — мягкий/драматичный, закат, неон…\n"
    "• <b>стиль</b> — фото, арт, 3D, аниме…\n"
    "• <b>детали</b> — ракурс, объектив, настроение\n\n"
    "<b>Пример:</b>\n"
    "<i>Рыжий кот-самурай в неоновом городе под дождём, "
    "драматичный свет, киношный кадр, крупный план</i>\n\n"
    "👇 Жми пресет стиля — он добавится к твоим промптам автоматически.\n"
    "✨ Или нажми «Улучшить промпт» — я разверну короткую идею в детальный промпт."
)

# Дописывается к Kontext-инструкции (на английском) для сохранения лица
KONTEXT_FACE_SUFFIX = (
    " Keep the person's face, facial features, hairstyle and identity exactly the same; "
    "do not alter the face."
)

# Хвост качества для Kontext — против «мыла», добавляется всегда
KONTEXT_QUALITY_SUFFIX = " Sharp focus, highly detailed, high quality, crisp textures."


HELP_KONTEXT = (
    "🖼 <b>Редактирование фото (Flux Kontext)</b>\n\n"
    "Пришли <b>фото</b> и в <b>подписи к нему</b> напиши, что изменить. "
    "Можно по-русски — переведу сам.\n\n"
    "📎 <b>Важно для резкости:</b> Telegram сжимает обычные фото. Чтобы результат "
    "был чётким, шли картинку <b>как файл</b> (скрепка → «Файл»), без сжатия.\n\n"
    "<b>Kontext понимает инструкции:</b>\n"
    "• «поменяй фон на зимний лес, оставь человека на месте»\n"
    "• «сделай в стиле масляной живописи»\n"
    "• «измени цвет машины на красный»\n"
    "• «добавь солнечные очки»\n\n"
    "<b>Секрет качества:</b> указывай, что нужно <b>сохранить</b> "
    "(«сохрани лицо и позу»), и используй слова «измени/замени», а не «преврати».\n\n"
    "Если пришлёшь фото без подписи — я спрошу, что изменить.\n\n"
    "🧑 По умолчанию я <b>сохраняю лицо</b> без изменений. Если правка про само лицо "
    "(борода, состарить, причёска) — выключи «Лицо при ред. фото» в ⚙️ Настройках."
)
