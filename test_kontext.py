"""Проверка Kontext без Telegram: грузит фото в ComfyUI и редактирует по инструкции."""
import asyncio
import json
import os
import sys

from comfy import ComfyClient, build_kontext_workflow

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
    CFG = json.load(f)


async def main():
    img_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "test_output.png")
    instruction = sys.argv[2] if len(sys.argv) > 2 else "change the background to a sunny tropical beach, keep the character exactly the same"
    comfy = ComfyClient(CFG)
    if not await comfy.ensure_up():
        print("ComfyUI не поднялся"); return
    with open(img_path, "rb") as fh:
        raw = fh.read()
    name = await comfy.upload_image(raw, "kontext_test_input.png")
    print("Фото загружено как:", name)
    k = CFG["kontext"]
    wf = build_kontext_workflow(
        instruction, name, seed=42, steps=k["steps"], guidance=k["guidance"],
        sampler_name=k["sampler_name"], scheduler=k["scheduler"],
        unet_name=k["unet_name"], clip_name1=k["clip_name1"],
        clip_name2=k["clip_name2"], vae_name=k["vae_name"],
    )
    print("Редактирую:", instruction)
    images = await comfy.generate(wf, on_progress=lambda p: print(f"  {p}%", end="\r"))
    out = os.path.join(HERE, "test_kontext_output.png")
    with open(out, "wb") as fh:
        fh.write(images[0][1])
    print("\nГотово:", out)


if __name__ == "__main__":
    asyncio.run(main())
