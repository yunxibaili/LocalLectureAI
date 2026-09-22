import base64
import json
from typing import Optional

import cv2
import numpy as np
import requests

from config import OLLAMA_URL, MODEL_NAME, TEXT_MODEL_NAME, REQUEST_TIMEOUT


def _encode_jpeg_b64(img_bgr: np.ndarray, quality: int = 92) -> str:
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def chat(image_bgr: np.ndarray, system_prompt: str, user_prompt: str,
         temperature: float = 0.1, as_json: bool = True,
         timeout: Optional[int] = None, model: Optional[str] = None,
         think: Optional[bool] = None, num_predict: Optional[int] = None,
         num_ctx: Optional[int] = None) -> str:
    """Send a chat request to Ollama. Returns the assistant content string."""
    payload = {
        "model": model or MODEL_NAME,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt, "images": [_encode_jpeg_b64(image_bgr)]},
        ],
        "options": {"temperature": temperature},
    }
    if as_json:
        payload["format"] = "json"
    if think is not None:
        payload["think"] = think
    if num_predict is not None:
        payload["options"]["num_predict"] = num_predict
    if num_ctx is not None:
        payload["options"]["num_ctx"] = num_ctx
    r = requests.post(OLLAMA_URL, json=payload, timeout=timeout or REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "").strip()


def chat_with_retry(image_bgr: np.ndarray, system_prompt: str, user_prompt: str) -> str:
    raw = chat(image_bgr, system_prompt, user_prompt, temperature=0.1, as_json=True)
    try:
        json.loads(raw)
        return raw
    except Exception:
        return chat(image_bgr, system_prompt, user_prompt, temperature=0.0, as_json=True)


def chat_text(system_prompt: str, user_prompt: str, temperature: float = 0.3,
              timeout: Optional[int] = None, model: Optional[str] = None,
              keep_alive: Optional[str] = None,
              think: Optional[bool] = None, num_predict: Optional[int] = None,
              num_ctx: Optional[int] = None) -> str:
    """Text-only chat (no image). Uses TEXT_MODEL_NAME (overridable via model=)."""
    payload = {
        "model": model or TEXT_MODEL_NAME,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": temperature},
    }
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    if think is not None:
        payload["think"] = think
    if num_predict is not None:
        payload["options"]["num_predict"] = num_predict
    if num_ctx is not None:
        payload["options"]["num_ctx"] = num_ctx
    r = requests.post(OLLAMA_URL, json=payload, timeout=timeout or REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("message", {}).get("content", "").strip()
