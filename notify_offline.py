"""Шлёт '🔴 ПК выключается' тем, у кого заявка в очереди. Запускается при выключении ПК.

Регистрируется один раз в Планировщике заданий (см. install_offline_task.ps1).
"""
import json
import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)
TOKEN = CFG["telegram_token"]

try:
    with open(os.path.join(HERE, "queue.json"), encoding="utf-8") as f:
        queue = json.load(f)
except Exception:
    queue = []

seen = set()
for job in queue:
    chat = job.get("chat_id")
    if not chat or chat in seen:
        continue
    seen.add(chat)
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": chat,
                "text": "🔴 ПК выключается. Твоя заявка сохранена и выполнится при следующем включении.",
            },
            timeout=5,
        )
    except Exception:
        pass
