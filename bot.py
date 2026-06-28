"""Telegram-бот: генерация (Flux dev) и редактирование фото (Flux Kontext) через ComfyUI."""
import asyncio
import json
import logging
import os
import random
import sys
import time
from io import BytesIO

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import keyboards as kb
from comfy import (
    ComfyClient, build_flux_unet_workflow, build_flux_workflow,
    build_kontext_workflow,
)
from prompts import (
    HELP_KONTEXT, HELP_TEXT, KONTEXT_FACE_SUFFIX, KONTEXT_QUALITY_SUFFIX,
    STYLE_PRESETS, ImproveError,
    apply_style, has_cyrillic, improve_edit_prompt, improve_prompt, translate_ru_en,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

HERE = os.path.dirname(os.path.abspath(__file__))

# Защита от двойного запуска: эксклюзивная файловая блокировка Windows.
import msvcrt
_LOCK_FH = open(os.path.join(HERE, "bot.lock"), "w")
try:
    msvcrt.locking(_LOCK_FH.fileno(), msvcrt.LK_NBLCK, 1)
except OSError:
    print("Bot already running in another window. Closing this one.")
    sys.exit(42)
with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)

# ───────── доступ: статичный список (config) + динамический (users.json) ─────────
USERS_PATH = os.path.join(HERE, "users.json")
STATIC_ALLOWED = set(CFG.get("allowed_users", []))
ADMIN_ID = CFG.get("admin_id") or (sorted(STATIC_ALLOWED)[0] if STATIC_ALLOWED else None)


def load_dynamic_users():
    try:
        with open(USERS_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_dynamic_users():
    with open(USERS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(dynamic_allowed), f)


dynamic_allowed = load_dynamic_users()


def allowed_now(uid):
    if not STATIC_ALLOWED and not dynamic_allowed:
        return True  # никто не настроен — открыт для всех
    return uid in STATIC_ALLOWED or uid in dynamic_allowed


comfy = ComfyClient(CFG)
bot = Bot(
    token=CFG["telegram_token"],
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
router = Router()

# Состояние пользователя в памяти (сбрасывается при перезапуске)
_state = {}


class Flow(StatesGroup):
    improve = State()      # ждём короткую идею для улучшения
    kontext = State()      # ждём инструкцию для редактирования фото
    wizard = State()       # пошаговый конструктор промпта


# Шаги конструктора промпта (структура хорошего промпта).
# options — популярные варианты-кнопки (label, значение). Можно и вписать своё текстом.
WIZARD_STEPS = [
    {"key": "subject", "skip": False, "style": False,
     "q": "🧩 <b>Шаг 1 — Субъект.</b> Что или кто на картинке?\nВыбери или впиши своё:",
     "options": [("👨 Мужчина", "мужчина"), ("👩 Девушка", "девушка"),
                 ("🐱 Кот", "кот"), ("🐶 Собака", "собака"),
                 ("🏞 Пейзаж", "пейзаж"), ("🚗 Машина", "машина")]},
    {"key": "scene", "skip": True, "style": False,
     "q": "📍 <b>Шаг 2 — Сцена.</b> Где это? Фон, обстановка.",
     "options": [("🏙 Город", "в городе"), ("🌲 Лес", "в лесу"),
                 ("🏖 Пляж", "на пляже"), ("🏠 Комната", "в комнате"),
                 ("🌌 Космос", "в космосе"), ("⛰ Горы", "в горах")]},
    {"key": "light", "skip": True, "style": False,
     "q": "💡 <b>Шаг 3 — Свет.</b> Освещение / время суток?",
     "options": [("🌅 Закат", "на закате"), ("☀️ День", "дневной свет"),
                 ("🌙 Ночь", "ночью"), ("💡 Студия", "студийный свет"),
                 ("🕯 Тёплый", "тёплый мягкий свет"), ("🌫 Туман", "в тумане")]},
    {"key": "style", "skip": True, "style": True,
     "q": "🎨 <b>Шаг 4 — Стиль.</b> Выбери кнопкой или напиши свой."},
    {"key": "details", "skip": True, "style": False,
     "q": "✨ <b>Шаг 5 — Детали.</b> Ракурс, настроение, камера?",
     "options": [("📷 Крупный план", "крупный план"), ("🎬 Киношно", "киношный кадр"),
                 ("🔍 Детально", "очень детально"), ("🌫 Мягкий фокус", "мягкий фокус"),
                 ("📐 Широкий", "широкий ракурс"), ("✨ Боке", "красивое боке")]},
]


def st(uid):
    if uid not in _state:
        t2 = CFG["txt2img"]
        _state[uid] = {
            "settings": {"width": t2["width"], "height": t2["height"], "steps": t2["steps"]},
            "style": "off",
            "translate_on": CFG["translate"]["enabled"],
            "improve_on": False,  # по умолчанию выкл — чистый промпт даёт лучше результат
            "preserve_face": True,
            "last_prompt": None,
            "last_image_bytes": None,
        }
    return _state[uid]


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ───────────────────────── доступ (middleware) ─────────────────────────
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        uid = user.id if user else None
        if allowed_now(uid):
            return await handler(event, data)
        # не в списке — пропускаем только нажатие «Запросить доступ»
        if isinstance(event, CallbackQuery) and event.data == "request_access":
            return await handler(event, data)
        if isinstance(event, Message):
            await event.answer(
                "🔒 <b>Доступ к боту по приглашению.</b>\n"
                "Нажми кнопку ниже — владельцу придёт запрос, и он откроет тебе доступ.",
                reply_markup=kb.request_access_kb(),
            )
        elif isinstance(event, CallbackQuery):
            await event.answer("Нет доступа", show_alert=True)
        return


# ───────────────────────── вспомогательное ─────────────────────────
def _progress_cb(status_msg, prefix):
    last = {"t": 0.0, "pct": -1}

    def cb(pct):
        now = time.monotonic()
        if pct != last["pct"] and now - last["t"] > 1.5:
            last["t"] = now
            last["pct"] = pct
            asyncio.create_task(_safe_edit(status_msg, f"{prefix} {pct}%"))
    return cb


async def _safe_edit(msg, text):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


async def _safe_delete(msg):
    try:
        await msg.delete()
    except Exception:
        pass


async def download_input_image(message):
    """Скачивает картинку из сообщения: фото (сжатое) или документ-картинку (без сжатия)."""
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document:
        file_id = message.document.file_id
    else:
        return None
    f = await bot.get_file(file_id)
    buf = BytesIO()
    await bot.download_file(f.file_path, buf)
    return buf.getvalue()


# ───────────────────────── очередь заданий ─────────────────────────
QUEUE_PATH = os.path.join(HERE, "queue.json")
QIMG_DIR = os.path.join(HERE, "queue_images")
os.makedirs(QIMG_DIR, exist_ok=True)

# Последняя картинка/промпт на пользователя — на диске, чтобы «🔄/✏️» работали после перезапуска
LAST_DIR = os.path.join(HERE, "last_images")
os.makedirs(LAST_DIR, exist_ok=True)
LAST_PROMPTS_PATH = os.path.join(HERE, "last_prompts.json")


def save_last_image(uid, raw):
    try:
        with open(os.path.join(LAST_DIR, f"{uid}.png"), "wb") as f:
            f.write(raw)
    except Exception as e:
        log.warning("save_last_image: %s", e)


def get_last_image(uid):
    p = os.path.join(LAST_DIR, f"{uid}.png")
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    return None


def save_last_prompt(uid, prompt):
    try:
        d = {}
        if os.path.exists(LAST_PROMPTS_PATH):
            with open(LAST_PROMPTS_PATH, encoding="utf-8") as f:
                d = json.load(f)
        d[str(uid)] = prompt
        with open(LAST_PROMPTS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        log.warning("save_last_prompt: %s", e)


def get_last_prompt(uid):
    try:
        with open(LAST_PROMPTS_PATH, encoding="utf-8") as f:
            return json.load(f).get(str(uid))
    except Exception:
        return None

QUEUE = []                 # job-словари; QUEUE[0] обрабатывается прямо сейчас
_wake = asyncio.Event()    # будит воркер при новом задании
_job_seq = 0
MAX_PENDING_PER_USER = 5


def _next_job_id():
    global _job_seq
    _job_seq += 1
    return _job_seq


def save_queue():
    try:
        with open(QUEUE_PATH, "w", encoding="utf-8") as f:
            json.dump(QUEUE, f, ensure_ascii=False)
    except Exception as e:
        log.warning("Не смог сохранить очередь: %s", e)


def load_queue():
    global _job_seq
    try:
        with open(QUEUE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    QUEUE.extend(data)
    _job_seq = max((j.get("id", 0) for j in QUEUE), default=0)


def save_job_image(raw):
    """Сохраняет фото для отложенного kontext-задания на диск, возвращает путь."""
    import uuid
    path = os.path.join(QIMG_DIR, f"{uuid.uuid4().hex}.png")
    with open(path, "wb") as f:
        f.write(raw)
    return path


def _cleanup_job(job):
    p = job.get("image_file")
    if p and os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass


# фото в queue_images, не привязанные к заданию и старше этого порога, считаем брошенными
ORPHAN_MAX_AGE = 3600  # секунд (1 час)


async def cleanup_orphans_worker(interval=1800):
    """Периодически чистит брошенные фото в queue_images.

    Орфаны появляются, когда правку фото начали (фото сохранено на диск),
    но до очереди она не дошла (юзер передумал). Удаляем файлы старше
    ORPHAN_MAX_AGE, которых нет среди активных заданий очереди.
    """
    while True:
        try:
            active = {os.path.abspath(j["image_file"]) for j in QUEUE if j.get("image_file")}
            now = time.time()
            removed = 0
            for name in os.listdir(QIMG_DIR):
                path = os.path.abspath(os.path.join(QIMG_DIR, name))
                if path in active:
                    continue
                try:
                    if now - os.path.getmtime(path) < ORPHAN_MAX_AGE:
                        continue
                    os.remove(path)
                    removed += 1
                except FileNotFoundError:
                    pass
                except Exception as e:
                    log.warning("cleanup_orphans: не удалил %s: %s", name, e)
            if removed:
                log.info("cleanup_orphans: удалено брошенных фото: %d", removed)
        except Exception:
            log.exception("cleanup_orphans: сбой прохода")
        await asyncio.sleep(interval)


def _pending_for(uid):
    return sum(1 for j in QUEUE if j["uid"] == uid)


async def make_effective(raw, translate_on, style_key, with_style):
    """Перевод (если RU) + стиль. Возвращает (текст_для_flux, note_перевода|None)."""
    text, note = raw, None
    if translate_on and has_cyrillic(raw):
        text = await translate_ru_en(raw)
        note = text
    if with_style:
        text = apply_style(text, style_key)
    return text, note


async def enqueue(kind, message, uid, prompt, image_file=None, style=None, improve=None):
    if _pending_for(uid) >= MAX_PENDING_PER_USER:
        await message.answer(
            f"⏳ У тебя уже {MAX_PENDING_PER_USER} заданий в очереди — дождись их выполнения."
        )
        # задание не создаём — чтобы фото не осталось висеть в queue_images
        if image_file and os.path.exists(image_file):
            try:
                os.remove(image_file)
            except Exception:
                pass
        return
    s = st(uid)
    job = {
        "id": _next_job_id(),
        "kind": kind,
        "uid": uid,
        "chat_id": message.chat.id,
        "prompt": prompt,
        "settings": dict(s["settings"]),
        "style": s["style"] if style is None else style,
        "translate_on": s["translate_on"],
        "improve_on": s["improve_on"] if improve is None else improve,
        "preserve_face": s["preserve_face"],
        "image_file": image_file,
    }
    QUEUE.append(job)
    save_queue()
    _wake.set()
    pos = len(QUEUE)
    if pos > 1:
        await message.answer(
            f"🕓 Принято! Ты в очереди, позиция <b>{pos}</b>. Пришлю, как дойдёт черёд."
        )


async def prep_txt2img_prompt(job):
    """Готовит промпт: авто-улучшение (если вкл) ИЛИ перевод, затем стиль.

    note показывается пользователю только для перевода (маленький); улучшённый
    длинный промпт не показываем, чтобы не засорять чат.
    """
    raw = job["prompt"]
    note = None
    if job.get("improve_on") and CFG["improve"]["enabled"]:
        try:
            text = await improve_prompt(raw, CFG["improve"]["hf_token"], CFG["improve"]["model"])
        except ImproveError as e:
            log.warning("auto-improve fallback: %s", e)
            if job["translate_on"] and has_cyrillic(raw):
                text = await translate_ru_en(raw)
                note = text
            else:
                text = raw
    elif job["translate_on"] and has_cyrillic(raw):
        text = await translate_ru_en(raw)
        note = text
    else:
        text = raw
    return apply_style(text, job["style"]), note


async def process_txt2img(job):
    chat = job["chat_id"]
    eff, note = await prep_txt2img_prompt(job)
    seed = random.randint(0, 2**63 - 1)
    t2 = CFG["txt2img"]
    cset = job["settings"]
    if t2.get("engine") == "unet":
        wf = build_flux_unet_workflow(
            eff, seed=seed, steps=cset["steps"], width=cset["width"], height=cset["height"],
            guidance=t2["guidance"], sampler_name=t2["sampler_name"],
            scheduler=t2["scheduler"], unet_name=t2["unet_name"],
            clip_name1=t2["clip_name1"], clip_name2=t2["clip_name2"], vae_name=t2["vae_name"],
        )
    else:
        wf = build_flux_workflow(
            eff, seed=seed, steps=cset["steps"], width=cset["width"], height=cset["height"],
            guidance=t2["guidance"], sampler_name=t2["sampler_name"],
            scheduler=t2["scheduler"], ckpt_name=t2["checkpoint_name"],
        )
    head = "🎨 Генерирую…"
    status = await bot.send_message(chat, f"{head} 0%")
    t0 = time.monotonic()
    images = await comfy.generate(wf, on_progress=_progress_cb(status, head))
    dt = time.monotonic() - t0
    s = st(job["uid"])
    s["last_image_bytes"] = images[0][1]
    s["last_prompt"] = job["prompt"]
    save_last_image(job["uid"], images[0][1])
    save_last_prompt(job["uid"], job["prompt"])
    cap = f"✅ Готово за {dt:.0f} c · 📐 {cset['width']}x{cset['height']} · 🔁 {cset['steps']}"
    await bot.send_document(chat, BufferedInputFile(images[0][1], filename=images[0][0]),
                            caption=cap, reply_markup=kb.after_image_kb())
    await _safe_delete(status)


async def prep_kontext_prompt(job):
    """Готовит инструкцию для Kontext: улучшение (editing-режим) или перевод + хвосты."""
    raw = job["prompt"]

    async def _tr(t):
        return await translate_ru_en(t) if (job["translate_on"] and has_cyrillic(t)) else t

    if job.get("preserve_face") and CFG["improve"]["enabled"]:
        try:
            eff = await improve_edit_prompt(raw, CFG["improve"]["hf_token"], CFG["improve"]["model"])
        except ImproveError as e:
            log.warning("kontext improve fallback: %s", e)
            eff = await _tr(raw) + KONTEXT_FACE_SUFFIX
    else:
        eff = await _tr(raw)
        if job.get("preserve_face"):
            eff = eff + KONTEXT_FACE_SUFFIX
    return eff + KONTEXT_QUALITY_SUFFIX


async def process_kontext(job):
    chat = job["chat_id"]
    with open(job["image_file"], "rb") as f:
        raw = f.read()
    image_name = await comfy.upload_image(raw, f"job_{job['id']}.png")
    try:
        eff = await prep_kontext_prompt(job)
        seed = random.randint(0, 2**63 - 1)
        k = CFG["kontext"]
        wf = build_kontext_workflow(
            eff, image_name, seed=seed, steps=k["steps"], guidance=k["guidance"],
            sampler_name=k["sampler_name"], scheduler=k["scheduler"],
            unet_name=k["unet_name"], clip_name1=k["clip_name1"],
            clip_name2=k["clip_name2"], vae_name=k["vae_name"],
        )
        head = "🖼 Редактирую…"
        status = await bot.send_message(chat, f"{head} 0%")
        t0 = time.monotonic()
        images = await comfy.generate(wf, on_progress=_progress_cb(status, head))
        dt = time.monotonic() - t0
        st(job["uid"])["last_image_bytes"] = images[0][1]
        save_last_image(job["uid"], images[0][1])
        cap = f"✅ Готово за {dt:.0f} c"
        await bot.send_document(chat, BufferedInputFile(images[0][1], filename=images[0][0]),
                                caption=cap, reply_markup=kb.after_image_kb())
        await _safe_delete(status)
    finally:
        comfy.delete_input(image_name)  # удалить присланное фото из ComfyUI/input


async def queue_worker():
    """Один воркер обрабатывает задания по очереди. ComfyUI всё равно рисует по одному."""
    # после включения ПК — уведомить тех, чьи заявки остались в очереди
    seen = set()
    for j in QUEUE:
        if j["uid"] not in seen:
            seen.add(j["uid"])
            try:
                await bot.send_message(j["chat_id"], "🟢 ПК снова онлайн — возобновляю твою заявку!")
            except Exception:
                pass
    while True:
        if not QUEUE:
            await _wake.wait()
            _wake.clear()
            continue
        job = QUEUE[0]
        try:
            if not await comfy.ensure_up():
                await bot.send_message(job["chat_id"], "❌ ComfyUI не запустился, пропускаю заявку.")
            elif job["kind"] == "kontext":
                await process_kontext(job)
            else:
                await process_txt2img(job)
        except Exception as e:
            log.exception("Ошибка задания")
            try:
                await bot.send_message(job["chat_id"], f"❌ Ошибка: {e}")
            except Exception:
                pass
        finally:
            if QUEUE and QUEUE[0] is job:
                QUEUE.pop(0)
            _cleanup_job(job)
            save_queue()


# ───────────────────────── команды и меню ─────────────────────────
@router.message(CommandStart())
@router.message(Command("help", "menu"))
async def cmd_start(message: Message):
    await message.answer(
        "🎨 <b>Привет! Я рисую картинки нейросетью Flux.</b>\n\n"
        "<b>Как пользоваться:</b>\n"
        "✍️ Напиши, что нарисовать — например:\n"
        "   <i>рыжий кот на крыше под закатом</i>\n"
        "🖼 Или пришли <b>фото с подписью</b>, что изменить:\n"
        "   <i>смени фон на зимний лес</i>\n\n"
        "Можно писать <b>по-русски</b> — переведу сам 🔤\n"
        "Кнопки внизу: помощь, стили, настройки 👇",
        reply_markup=kb.main_menu(),
    )


@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(
        f"🆔 Твой Telegram ID: <code>{message.from_user.id}</code>\n"
        "Скинь его владельцу, чтобы он добавил тебя в доступ."
    )


@router.message(F.text == kb.BTN_HELP)
async def btn_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=kb.style_kb(st(message.from_user.id)["style"]))


@router.message(F.text == kb.BTN_SETTINGS)
async def btn_settings(message: Message):
    s = st(message.from_user.id)
    await message.answer(
        "⚙️ Настройки генерации:",
        reply_markup=kb.settings_kb(s["settings"], s["translate_on"],
                                    s["improve_on"], s["preserve_face"]),
    )


@router.message(F.text == kb.BTN_PHOTO)
async def btn_photo(message: Message):
    await message.answer(HELP_KONTEXT)


@router.message(F.text == kb.BTN_IMPROVE)
async def btn_improve(message: Message, state: FSMContext):
    if not CFG["improve"]["enabled"]:
        await message.answer("Улучшение промптов отключено в конфиге.")
        return
    await state.set_state(Flow.improve)
    await message.answer(
        "✨ Опиши идею коротко (можно по-русски) — разверну в детальный промпт и нарисую.\n"
        "Например: <i>кот-самурай под дождём в неоновом городе</i>"
    )


# ───────────────────────── inline-колбэки ─────────────────────────
@router.callback_query(F.data.startswith("style:"))
async def cb_style(call: CallbackQuery):
    key = call.data.split(":", 1)[1]
    if key in STYLE_PRESETS:
        st(call.from_user.id)["style"] = key
        await call.answer(f"Стиль: {STYLE_PRESETS[key][0]}")
        try:
            await call.message.edit_reply_markup(reply_markup=kb.style_kb(key))
        except Exception:
            pass
    else:
        await call.answer()


@router.callback_query(F.data.startswith("set:"))
async def cb_settings(call: CallbackQuery):
    s = st(call.from_user.id)
    cset = s["settings"]
    lim = CFG["limits"]
    parts = call.data.split(":")
    kind = parts[1]
    if kind == "steps":
        cset["steps"] = clamp(int(parts[2]), lim["min_steps"], lim["max_steps"])
    elif kind == "size":
        w, h = parts[2].split("x")
        cset["width"] = clamp(int(w), lim["min_side"], lim["max_side"])
        cset["height"] = clamp(int(h), lim["min_side"], lim["max_side"])
    elif kind == "translate":
        s["translate_on"] = not s["translate_on"]
    elif kind == "improve":
        s["improve_on"] = not s["improve_on"]
    elif kind == "face":
        s["preserve_face"] = not s["preserve_face"]
    await call.answer("Сохранено")
    try:
        await call.message.edit_reply_markup(
            reply_markup=kb.settings_kb(cset, s["translate_on"],
                                        s["improve_on"], s["preserve_face"])
        )
    except Exception:
        pass


@router.callback_query(F.data == "regen")
async def cb_regen(call: CallbackQuery):
    await call.answer("Добавляю в очередь…")
    uid = call.from_user.id
    lp = st(uid).get("last_prompt") or get_last_prompt(uid)
    if not lp:
        await call.message.answer("Нет предыдущего промпта — напиши новый.")
        return
    await enqueue("txt2img", call.message, uid, lp)


@router.callback_query(F.data == "edit_this")
async def cb_edit_this(call: CallbackQuery, state: FSMContext):
    await call.answer()
    uid = call.from_user.id
    raw = st(uid).get("last_image_bytes") or get_last_image(uid)
    if not raw:
        await call.message.answer("Нет картинки для редактирования — сначала сгенерируй или пришли фото.")
        return
    path = save_job_image(raw)
    await state.set_state(Flow.kontext)
    await state.update_data(image_file=path)
    await call.message.answer("✏️ Что изменить на этой картинке? Напиши инструкцию (можно по-русски).")


# ───────────────────────── запросы доступа ─────────────────────────
@router.callback_query(F.data == "request_access")
async def cb_request_access(call: CallbackQuery):
    u = call.from_user
    if allowed_now(u.id):
        await call.answer("У тебя уже есть доступ 🙂", show_alert=True)
        return
    await call.answer("Запрос отправлен ✅ Жди подтверждения.", show_alert=True)
    if not ADMIN_ID:
        return
    uname = f"@{u.username}" if u.username else "(без username)"
    text = (
        "👤 <b>Запрос доступа к боту</b>\n"
        f"Имя: {u.full_name}\n"
        f"Username: {uname}\n"
        f"ID: <code>{u.id}</code>"
    )
    try:
        await bot.send_message(ADMIN_ID, text, reply_markup=kb.approve_kb(u.id))
    except Exception as e:
        log.warning("Не смог уведомить админа: %s", e)


@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Только владелец", show_alert=True)
        return
    uid = int(call.data.split(":")[1])
    dynamic_allowed.add(uid)
    save_dynamic_users()
    await call.answer("Доступ открыт ✅")
    try:
        await call.message.edit_text(call.message.html_text + "\n\n✅ <b>Разрешено</b>")
    except Exception:
        pass
    try:
        await bot.send_message(uid, "✅ Тебе открыли доступ к боту! Нажми /start 🎨")
    except Exception:
        pass


@router.callback_query(F.data.startswith("deny:"))
async def cb_deny(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Только владелец", show_alert=True)
        return
    uid = int(call.data.split(":")[1])
    dynamic_allowed.discard(uid)
    save_dynamic_users()
    await call.answer("Отклонено")
    try:
        await call.message.edit_text(call.message.html_text + "\n\n❌ <b>Отклонено</b>")
    except Exception:
        pass


# ───────────────────────── конструктор промпта ─────────────────────────
async def wizard_ask(message, state):
    data = await state.get_data()
    i = data["i"]
    step = WIZARD_STEPS[i]
    allow_finish = i > 0  # на 1-м шаге ещё нечего рисовать
    if step["style"]:
        markup = kb.wizard_style_kb(allow_finish=allow_finish)
    else:
        markup = kb.wizard_options_kb(step.get("options", []),
                                      allow_skip=step["skip"], allow_finish=allow_finish)
    await message.answer(step["q"], reply_markup=markup)


async def wizard_advance(message, uid, state):
    data = await state.get_data()
    i = data["i"] + 1
    if i >= len(WIZARD_STEPS):
        await wizard_finish(message, uid, state)
    else:
        await state.update_data(i=i)
        await wizard_ask(message, state)


async def wizard_finish(message, uid, state):
    data = await state.get_data()
    parts = data.get("parts", {})
    await state.clear()
    order = [parts.get(k) for k in ("subject", "scene", "light", "details")]
    text = ", ".join([p for p in order if p])
    if parts.get("style"):
        text += (", " if text else "") + parts["style"]
    if not text:
        await message.answer("Пусто 🤷 Просто напиши промпт обычным сообщением.")
        return
    # стиль уже в тексте, улучшение не нужно — не переписываем собранное; промпт не показываем
    await enqueue("txt2img", message, uid, text, style="off", improve=False)


@router.message(F.text == kb.BTN_WIZARD)
async def btn_wizard(message: Message, state: FSMContext):
    await state.set_state(Flow.wizard)
    await state.update_data(parts={}, i=0)
    await wizard_ask(message, state)


@router.callback_query(Flow.wizard, F.data == "wiz:skip")
async def wiz_skip(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await wizard_advance(call.message, call.from_user.id, state)


@router.callback_query(Flow.wizard, F.data == "wiz:finish")
async def wiz_finish(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await wizard_finish(call.message, call.from_user.id, state)


@router.callback_query(Flow.wizard, F.data.startswith("wiz:opt:"))
async def wiz_opt(call: CallbackQuery, state: FSMContext):
    await call.answer()
    idx = int(call.data.split(":")[2])
    data = await state.get_data()
    step = WIZARD_STEPS[data["i"]]
    opts = step.get("options", [])
    if 0 <= idx < len(opts):
        parts = data.get("parts", {})
        parts[step["key"]] = opts[idx][1]
        await state.update_data(parts=parts)
    await wizard_advance(call.message, call.from_user.id, state)


@router.callback_query(Flow.wizard, F.data.startswith("wiz:style:"))
async def wiz_style(call: CallbackQuery, state: FSMContext):
    await call.answer()
    key = call.data.split(":")[2]
    suffix = STYLE_PRESETS.get(key, ("", ""))[1]
    data = await state.get_data()
    parts = data.get("parts", {})
    if suffix:
        parts["style"] = suffix
    await state.update_data(parts=parts)
    await wizard_advance(call.message, call.from_user.id, state)


@router.callback_query(Flow.wizard, F.data == "wiz:next")
async def wiz_next(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await wizard_advance(call.message, call.from_user.id, state)


@router.message(Flow.wizard, F.text)
async def wizard_text(message: Message, state: FSMContext):
    data = await state.get_data()
    i = data["i"]
    step = WIZARD_STEPS[i]
    parts = data.get("parts", {})
    parts[step["key"]] = message.text.strip()
    await state.update_data(parts=parts)
    # после 1-го шага (субъект) — не гоним по шагам, а спрашиваем: рисовать или уточнять
    if i == 0:
        await message.answer(
            f"Принял: «{message.text.strip()}»\nНарисовать сейчас или добавить детали?",
            reply_markup=kb.wizard_after_subject_kb(),
        )
        return
    await wizard_advance(message, message.from_user.id, state)


# ───────────────────────── фото и FSM-состояния ─────────────────────────
@router.message(F.photo | F.document)
async def on_photo(message: Message, state: FSMContext):
    uid = message.from_user.id
    # документ должен быть картинкой
    if message.document and not (message.document.mime_type or "").startswith("image/"):
        await message.answer("Это не картинка 🤔 Пришли фото или изображение-файл.")
        return
    try:
        raw = await download_input_image(message)
    except Exception as e:
        await message.answer(f"❌ Не смог принять фото: {e}")
        return
    path = save_job_image(raw)
    # подсказка про качество, если фото пришло сжатым (как «фото», а не файлом)
    tip = ""
    if message.photo and not message.document:
        tip = "\n\n📎 <i>Совет: для резкого результата шли фото как ФАЙЛ (скрепка → Файл) — без сжатия.</i>"
    caption = (message.caption or "").strip()
    if caption:
        await enqueue("kontext", message, uid, caption, image_file=path)
    else:
        await state.set_state(Flow.kontext)
        await state.update_data(image_file=path)
        await message.answer(
            "🖼 Что изменить на фото? Выбери кнопку или напиши свою инструкцию (можно по-русски)." + tip,
            reply_markup=kb.kontext_quick_kb(),
        )


@router.message(Flow.improve, F.text)
async def improve_idea(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    idea = message.text.strip()
    imp = CFG["improve"]
    status = await message.answer("✨ Улучшаю промпт…")
    try:
        improved = await improve_prompt(idea, imp["hf_token"], imp["model"])
        await _safe_delete(status)
    except ImproveError as e:
        log.warning("improve fallback: %s", e)
        improved = await translate_ru_en(idea) if has_cyrillic(idea) else idea
        await _safe_edit(status, "⚠️ Авто-улучшение недоступно, рисую по переводу.")
    await enqueue("txt2img", message, uid, improved)


@router.callback_query(Flow.kontext, F.data.startswith("kfix:"))
async def kfix(call: CallbackQuery, state: FSMContext):
    await call.answer("Добавляю в очередь…")
    idx = int(call.data.split(":")[1])
    data = await state.get_data()
    image_file = data.get("image_file")
    await state.clear()
    if not image_file or not os.path.exists(image_file):
        await call.message.answer("Не нашёл фото — пришли заново.")
        return
    if 0 <= idx < len(kb.KONTEXT_QUICK):
        instr = kb.KONTEXT_QUICK[idx][1]
        await enqueue("kontext", call.message, call.from_user.id, instr, image_file=image_file)


@router.message(Flow.kontext, F.text)
async def kontext_instruction(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    image_file = data.get("image_file")
    if not image_file or not os.path.exists(image_file):
        await message.answer("Не нашёл фото. Пришли его заново.")
        return
    await enqueue("kontext", message, message.from_user.id, message.text.strip(),
                  image_file=image_file)


# Любой прочий текст = промпт для генерации
@router.message(F.text & ~F.text.startswith("/"))
async def on_prompt(message: Message):
    await enqueue("txt2img", message, message.from_user.id, message.text.strip())


# ───────────────────────── запуск ─────────────────────────
async def setup_bot_profile():
    """Меню команд и описание — подсказки при входе в бота."""
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="🎨 Запустить / меню"),
        BotCommand(command="help", description="💡 Помощь с промптами"),
        BotCommand(command="id", description="🆔 Узнать свой ID"),
    ])
    try:
        await bot.set_my_short_description(
            "Рисую и редактирую картинки нейросетью Flux. Пиши по-русски."
        )
        await bot.set_my_description(
            "🎨 Я рисую картинки нейросетью Flux.\n\n"
            "• Напиши, что нарисовать (можно по-русски)\n"
            "• Или пришли фото с подписью, что изменить\n\n"
            "Нажми «Запустить» и пробуй!"
        )
    except Exception as e:
        log.warning("Не удалось задать описание бота: %s", e)


async def main():
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    dp.include_router(router)
    load_queue()
    me = await bot.get_me()
    log.info("Бот запущен: @%s", me.username)
    log.info("ComfyUI: %s, автозапуск=%s", comfy.base, CFG.get("comfy_autostart"))
    if QUEUE:
        log.info("В очереди заданий: %d", len(QUEUE))
    await setup_bot_profile()
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(queue_worker())
    asyncio.create_task(cleanup_orphans_worker())
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено.")
