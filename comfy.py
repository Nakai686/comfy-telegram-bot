"""Клиент для ComfyUI: автозапуск, постановка задания Flux, прогресс, получение картинки."""
import asyncio
import json
import logging
import os
import subprocess
import uuid

import aiohttp

log = logging.getLogger("comfy")


def build_flux_workflow(prompt, *, seed, steps, width, height,
                        guidance, sampler_name, scheduler, ckpt_name):
    """Собирает workflow Flux в API-формате ComfyUI (Load Checkpoint -> KSampler -> Save)."""
    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ckpt_name},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["4", 1]},
        },
        "26": {
            "class_type": "FluxGuidance",
            "inputs": {"guidance": guidance, "conditioning": ["6", 0]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["4", 1]},
        },
        "5": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["26", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "tgbot", "images": ["8", 0]},
        },
    }


def build_flux_unet_workflow(prompt, *, seed, steps, width, height, guidance,
                             sampler_name, scheduler,
                             unet_name, clip_name1, clip_name2, vae_name):
    """txt2img для Flux-моделей в формате UNET (Krea dev и т.п.):
    UNETLoader + DualCLIPLoader + VAELoader -> FluxGuidance -> KSampler.
    """
    return {
        "37": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
        },
        "38": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": clip_name1,
                "clip_name2": clip_name2,
                "type": "flux",
                "device": "default",
            },
        },
        "39": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": vae_name},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["38", 0]},
        },
        "26": {
            "class_type": "FluxGuidance",
            "inputs": {"guidance": guidance, "conditioning": ["6", 0]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["38", 0]},
        },
        "5": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["37", 0],
                "positive": ["26", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["39", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "tgbot", "images": ["8", 0]},
        },
    }


def build_kontext_workflow(prompt, image_name, *, seed, steps, guidance,
                           sampler_name, scheduler,
                           unet_name, clip_name1, clip_name2, vae_name):
    """Workflow Flux Kontext (редактирование фото по инструкции).

    Обвязка нод повторяет рабочий flux_kontext.json: UNETLoader + DualCLIPLoader +
    VAELoader -> LoadImage -> FluxKontextImageScale -> VAEEncode -> ReferenceLatent
    -> FluxGuidance -> KSampler (negative = ConditioningZeroOut).
    """
    return {
        "37": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
        },
        "38": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": clip_name1,
                "clip_name2": clip_name2,
                "type": "flux",
                "device": "default",
            },
        },
        "39": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": vae_name},
        },
        "190": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name},
        },
        "42": {
            "class_type": "FluxKontextImageScale",
            "inputs": {"image": ["190", 0]},
        },
        "124": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["42", 0], "vae": ["39", 0]},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["38", 0]},
        },
        "177": {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": ["6", 0], "latent": ["124", 0]},
        },
        "35": {
            "class_type": "FluxGuidance",
            "inputs": {"conditioning": ["177", 0], "guidance": guidance},
        },
        "135": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["6", 0]},
        },
        "31": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["37", 0],
                "positive": ["35", 0],
                "negative": ["135", 0],
                "latent_image": ["124", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["31", 0], "vae": ["39", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "tgbot_kontext", "images": ["8", 0]},
        },
    }


class ComfyClient:
    def __init__(self, cfg):
        self.host = cfg["comfy_host"]
        self.port = cfg["comfy_port"]
        self.base = f"http://{self.host}:{self.port}"
        self.ws_base = f"ws://{self.host}:{self.port}"
        self.cfg = cfg
        self._proc = None
        self._start_lock = asyncio.Lock()
        portable = cfg.get("comfy_portable_dir", "")
        self.output_dir = cfg.get("comfy_output_dir") or os.path.join(portable, "ComfyUI", "output")
        self.input_dir = os.path.join(portable, "ComfyUI", "input")

    async def is_up(self):
        try:
            timeout = aiohttp.ClientTimeout(total=4)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.get(f"{self.base}/system_stats") as r:
                    return r.status == 200
        except Exception:
            return False

    def _spawn_comfy(self):
        portable = self.cfg["comfy_portable_dir"]
        py = os.path.join(portable, "python_embeded", "python.exe")
        main = os.path.join("ComfyUI", "main.py")
        out = self.cfg.get("comfy_output_dir")
        args = [py, "-s", main, "--windows-standalone-build"]
        if out:
            args += ["--output-directory", out]
        # порт по умолчанию 8188; если в конфиге другой — пробросим
        if int(self.port) != 8188:
            args += ["--port", str(self.port)]
        log.info("Запускаю ComfyUI: %s (cwd=%s)", " ".join(args), portable)
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_CONSOLE  # отдельное окно, чтобы видеть логи Comfy
        self._proc = subprocess.Popen(args, cwd=portable, creationflags=flags)

    async def ensure_up(self):
        """Гарантирует, что ComfyUI отвечает. При необходимости запускает его и ждёт."""
        if await self.is_up():
            return True
        if not self.cfg.get("comfy_autostart", False):
            return False
        async with self._start_lock:
            if await self.is_up():
                return True
            self._spawn_comfy()
            deadline = self.cfg.get("comfy_startup_timeout_sec", 240)
            waited = 0
            while waited < deadline:
                await asyncio.sleep(3)
                waited += 3
                if await self.is_up():
                    log.info("ComfyUI поднялся за ~%ss", waited)
                    return True
            log.error("ComfyUI не ответил за %ss", deadline)
            return False

    async def upload_image(self, raw, filename):
        """Загружает картинку в input ComfyUI. Возвращает имя для ноды LoadImage."""
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            form = aiohttp.FormData()
            form.add_field("image", raw, filename=filename,
                           content_type="application/octet-stream")
            form.add_field("overwrite", "true")
            async with s.post(f"{self.base}/upload/image", data=form) as r:
                if r.status != 200:
                    text = await r.text()
                    raise RuntimeError(f"Загрузка фото не удалась ({r.status}): {text[:300]}")
                data = await r.json()
        name = data["name"]
        sub = data.get("subfolder", "")
        return f"{sub}/{name}" if sub else name

    async def generate(self, workflow, *, on_progress=None):
        """Ставит задание, ждёт завершения, возвращает список (filename, bytes).

        on_progress(percent:int) — необязательный колбэк прогресса (0..100).
        """
        client_id = uuid.uuid4().hex
        timeout = aiohttp.ClientTimeout(total=None)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            # 1) поставить в очередь
            payload = {"prompt": workflow, "client_id": client_id}
            async with s.post(f"{self.base}/prompt", json=payload) as r:
                if r.status != 200:
                    text = await r.text()
                    raise RuntimeError(f"ComfyUI /prompt вернул {r.status}: {text[:400]}")
                data = await r.json()
            prompt_id = data["prompt_id"]
            log.info("Задание поставлено, prompt_id=%s", prompt_id)

            # 2) слушать прогресс по websocket
            ws_url = f"{self.ws_base}/ws?clientId={client_id}"
            async with s.ws_connect(ws_url, heartbeat=20) as ws:
                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    ev = json.loads(msg.data)
                    etype = ev.get("type")
                    edata = ev.get("data", {})
                    if etype == "progress" and edata.get("prompt_id") == prompt_id:
                        if on_progress and edata.get("max"):
                            pct = int(edata["value"] / edata["max"] * 100)
                            on_progress(pct)
                    elif etype == "executing":
                        if edata.get("node") is None and edata.get("prompt_id") == prompt_id:
                            break  # готово
                    elif etype == "execution_error" and edata.get("prompt_id") == prompt_id:
                        raise RuntimeError(f"Ошибка ComfyUI: {edata}")

            # 3) забрать результат из истории
            async with s.get(f"{self.base}/history/{prompt_id}") as r:
                hist = await r.json()
            entry = hist.get(prompt_id, {})
            images = []
            for node_out in entry.get("outputs", {}).values():
                for img in node_out.get("images", []):
                    if img.get("type") == "temp":
                        continue
                    raw = await self._view(s, img)
                    images.append((img["filename"], raw))
                    # считали в память — файл на диске больше не нужен
                    if self.cfg.get("cleanup_outputs", True) and img.get("type") == "output":
                        self._delete_output_file(img)
            if not images:
                raise RuntimeError("ComfyUI завершил задание, но картинок не вернул.")
            return images

    async def _view(self, session, img):
        params = {
            "filename": img["filename"],
            "subfolder": img.get("subfolder", ""),
            "type": img.get("type", "output"),
        }
        async with session.get(f"{self.base}/view", params=params) as r:
            return await r.read()

    def _delete_output_file(self, img):
        """Удаляет выходной PNG с диска (строго свой файл по имени из истории)."""
        path = os.path.join(self.output_dir, img.get("subfolder", ""), img["filename"])
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            log.warning("Не смог удалить выходной файл %s: %s", path, e)

    def delete_input(self, name):
        """Удаляет загруженное в ComfyUI входное фото (для kontext) после генерации."""
        if not name:
            return
        path = os.path.join(self.input_dir, name.replace("/", os.sep))
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            log.warning("Не смог удалить входное фото %s: %s", path, e)
