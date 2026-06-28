"""Сторож бота: следит, что бот реально опрашивает Telegram, и лечит зависания.

Зачем: restart-loop в start.bat ловит только *падение* процесса. Но бот может
*зависнуть* — процесс жив и держит блокировку, а апдейты Telegram уже не опрашивает.
Сторож раз в минуту проверяет это (по 409-конфликту getUpdates) и при зависании
снимает залипший процесс: start.bat сам поднимет новый. Если окна start.bat нет —
запускает его. Сетевые сбои не считаются зависанием (бота не трогаем).

Запускается скрытно при входе в Windows (start-watchdog.vbs).
"""
import json
import os
import subprocess
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
TOKEN = CFG["telegram_token"]
START_BAT = os.path.join(HERE, "start.bat")
LOG_PATH = os.path.join(HERE, "watchdog.log")

CHECK_INTERVAL = 60       # как часто проверять, сек
FAILS_BEFORE_ACTION = 2   # сколько проверок подряд «не опрашивает» до перезапуска
GRACE_AFTER_ACTION = 90   # пауза после перезапуска (бот успевает подняться), сек


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_polling(probes=6, gap=0.8):
    """True — бот опрашивает Telegram; False — никто (завис/упал).

    Важно: здоровый бот отвечает 409-конфликтом НЕ на каждый запрос — между
    длинными опросами aiogram есть паузы, и одиночная проба может их застать.
    Поэтому пробуем несколько раз: любой 409 = бот точно жив. False вернём только
    если ВСЕ пробы пустые (ok=True). Сетевые/иные ошибки трактуем как 'жив'.
    """
    for i in range(probes):
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"timeout": 0, "limit": 1}, timeout=15,
            ).json()
            if not r.get("ok"):
                # 409 = конфликт (бот опрашивает); иная ошибка TG — тоже не трогаем
                return True
        except Exception:
            return True                   # сеть недоступна — не наша вина
        if i < probes - 1:
            time.sleep(gap)
    return False                          # все пробы пустые — никто не опрашивает


def _ps(cmd):
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True, text=True,
    )


def kill_bot():
    """Снять зависшие процессы bot.py (редиректор + рабочий). Только этот проект."""
    _ps(
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot.py*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )


def start_bat_running():
    out = _ps(
        "(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and "
        "$_.CommandLine -like '*start.bat*' } | Measure-Object).Count"
    ).stdout.strip()
    try:
        return int(out) > 0
    except ValueError:
        return False


def restart():
    log("Бот не опрашивает Telegram — перезапускаю.")
    kill_bot()           # снять зависший процесс; start.bat сам поднимет новый
    time.sleep(3)
    if not start_bat_running():
        log("Окно start.bat не найдено — запускаю start.bat.")
        try:
            os.startfile(START_BAT)
        except Exception as e:
            log(f"Не смог запустить start.bat: {e}")


def main():
    log("Watchdog запущен.")
    fails = 0
    while True:
        time.sleep(CHECK_INTERVAL)
        if is_polling():
            fails = 0
            continue
        fails += 1
        log(f"Бот не опрашивает ({fails}/{FAILS_BEFORE_ACTION}).")
        if fails >= FAILS_BEFORE_ACTION:
            restart()
            fails = 0
            time.sleep(GRACE_AFTER_ACTION)


if __name__ == "__main__":
    main()
