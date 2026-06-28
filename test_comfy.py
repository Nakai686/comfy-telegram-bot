"""Проверка связки без Telegram: запускает ComfyUI (если нужно) и рисует тестовую картинку.

Запуск:  .venv\Scripts\python.exe test_comfy.py "a cat astronaut, cinematic"
"""
import asyncio
import json
import os
import sys

from comfy import ComfyClient, build_flux_workflow

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)


async def main():
    prompt = sys.argv[1] if len(sys.argv) > 1 else "a cute corgi astronaut in space, cinematic lighting"
    comfy = ComfyClient(CFG)
    print("Проверяю ComfyUI...")
    if not await comfy.ensure_up():
        print("ОШИБКА: ComfyUI не поднялся.")
        return
    print("ComfyUI готов. Генерирую:", prompt)
    d = CFG["txt2img"]
    wf = build_flux_workflow(
        prompt, seed=12345, steps=d["steps"], width=d["width"], height=d["height"],
        guidance=d["guidance"], sampler_name=d["sampler_name"],
        scheduler=d["scheduler"], ckpt_name=d["checkpoint_name"],
    )
    images = await comfy.generate(wf, on_progress=lambda p: print(f"  {p}%", end="\r"))
    out = os.path.join(HERE, "test_output.png")
    with open(out, "wb") as f:
        f.write(images[0][1])
    print("\nГотово! Сохранено:", out)


if __name__ == "__main__":
    asyncio.run(main())
