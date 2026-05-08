from __future__ import annotations

import base64
import hashlib
import math
import os
import shlex
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
import wave
import webbrowser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from .types import AttachmentRef, InboundMessage
from .util import ensure_dir, expand_path, hidden_windows_subprocess_kwargs

try:  # pragma: no cover - runtime dependency
    import qrcode
except Exception:  # pragma: no cover
    qrcode = None

try:  # pragma: no cover - runtime dependency
    import requests
except Exception:  # pragma: no cover
    requests = None

try:  # pragma: no cover - runtime dependency
    from Crypto.Cipher import AES
except Exception:  # pragma: no cover
    AES = None

try:  # pragma: no cover - runtime dependency
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

API = "https://ilinkai.weixin.qq.com"
CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"
VER = "2.1.10"
UA = f"ga-wechat-clawbot/{VER}"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = (2 << 16) | (1 << 8) | 10
MSG_USER, MSG_BOT = 1, 2
STATE_FINISH = 2
ITEM_TEXT, ITEM_IMAGE, ITEM_VOICE, ITEM_FILE, ITEM_VIDEO = 1, 2, 3, 4, 5
UPLOAD_MEDIA_IMAGE, UPLOAD_MEDIA_VIDEO, UPLOAD_MEDIA_FILE, UPLOAD_MEDIA_VOICE = 1, 2, 3, 4
MEDIA_EXTS = {
    "image_item": ".jpg",
    "video_item": ".mp4",
    "file_item": ".bin",
    "voice_item": ".silk",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
VOICE_EXTS = {".silk", ".wav", ".mp3", ".m4a", ".aac", ".opus", ".ogg"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


class WxClawClient:
    def __init__(
        self,
        token_file: str | os.PathLike[str] = "~/.wxbot/token.json",
        media_dir: str | os.PathLike[str] = "./state/media",
        voice_encoder_cmd: str = "",
        request_timeout: int = 15,
    ) -> None:
        self._require_runtime()
        self._tf = expand_path(token_file)
        self._tf.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir = ensure_dir(media_dir)
        self.voice_encoder_cmd = str(voice_encoder_cmd or "").strip()
        self.request_timeout = request_timeout
        self.token = ""
        self.bot_id = ""
        self._buf = ""
        self._seen_message_ids: list[int] = []
        self._load()

    @staticmethod
    def _require_runtime() -> None:
        missing = []
        if requests is None:
            missing.append("requests")
        if AES is None:
            missing.append("pycryptodome")
        if missing:
            raise RuntimeError("Missing runtime dependencies: " + ", ".join(missing))

    @staticmethod
    def _uin() -> str:
        return base64.b64encode(str(struct.unpack(">I", os.urandom(4))[0]).encode()).decode()

    def _load(self) -> None:
        if not self._tf.exists():
            return
        import json

        data = json.loads(self._tf.read_text("utf-8"))
        self.token = data.get("bot_token", "")
        self.bot_id = data.get("ilink_bot_id", "")
        self._buf = data.get("updates_buf", "")

    def _save(self, **extra: Any) -> None:
        import json

        payload = {
            "bot_token": self.token or "",
            "ilink_bot_id": self.bot_id or "",
            "updates_buf": self._buf or "",
            **extra,
        }
        self._tf.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")

    def _post(self, endpoint: str, body: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
        import json

        timeout = timeout or self.request_timeout
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Content-Length": str(len(data)),
            "X-WECHAT-UIN": self._uin(),
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
            "User-Agent": UA,
        }
        token = (self.token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = requests.post(f"{API}/{endpoint}", data=data, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        errcode = payload.get("errcode")
        if errcode:
            raise RuntimeError(f"WeChat API error {errcode}: {payload.get('errmsg', '')}")
        return payload

    def login_qr(self, poll_interval: int = 2, max_wait_sec: int = 600) -> dict[str, Any]:
        if qrcode is None:
            raise RuntimeError("qrcode is required for QR login")
        response = requests.get(
            f"{API}/ilink/bot/get_bot_qrcode",
            params={"bot_type": 3},
            headers={"User-Agent": UA},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        qr_id = data["qrcode"]
        url = data.get("qrcode_img_content", "")
        print(f"[WeChat] QR login id={qr_id}")
        if url:
            image_path = self._tf.parent / "wx_qr.png"
            qrcode.make(url).save(str(image_path))
            try:
                webbrowser.open(str(image_path))
            except Exception:
                pass
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        start = time.time()
        last = ""
        while True:
            if max_wait_sec and time.time() - start > max_wait_sec:
                raise RuntimeError("二维码登录超时")
            time.sleep(poll_interval)
            try:
                payload = requests.get(
                    f"{API}/ilink/bot/get_qrcode_status",
                    params={"qrcode": qr_id},
                    headers={"User-Agent": UA},
                    timeout=60,
                ).json()
            except requests.exceptions.ReadTimeout:
                continue
            status = payload.get("status", "")
            if status != last:
                print(f"[WeChat] QR status={status}")
                last = status
            if status == "confirmed":
                self.token = payload.get("bot_token", "")
                self.bot_id = payload.get("ilink_bot_id", "")
                self._save(login_time=time.strftime("%Y-%m-%d %H:%M:%S"))
                return payload
            if status == "expired":
                raise RuntimeError("二维码过期")

    def get_updates(self, timeout: int = 30) -> list[dict[str, Any]]:
        try:
            payload = self._post(
                "ilink/bot/getupdates",
                {"get_updates_buf": self._buf or "", "base_info": {"channel_version": VER}},
                timeout=timeout + 5,
            )
        except requests.exceptions.ReadTimeout:
            return []
        except RuntimeError as exc:
            if "-14" in str(exc):
                self._buf = ""
                self._save()
            print(f"[WeChat] getupdates failed: {exc}")
            return []
        next_buf = payload.get("get_updates_buf", "")
        if next_buf:
            self._buf = next_buf
            self._save()
        return payload.get("msgs") or []

    @staticmethod
    def is_user_msg(msg: dict[str, Any]) -> bool:
        return msg.get("message_type") == MSG_USER

    @staticmethod
    def extract_text(msg: dict[str, Any]) -> str:
        parts = []
        for item in msg.get("item_list", []) or []:
            if item.get("type") == ITEM_TEXT and item.get("text_item"):
                text = item["text_item"].get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()

    def iter_user_messages(self, timeout: int = 30) -> Iterable[InboundMessage]:
        for raw in self.get_updates(timeout):
            message_id = int(raw.get("message_id", 0) or 0)
            if not self.is_user_msg(raw):
                continue
            if message_id and message_id in self._seen_message_ids:
                continue
            if message_id:
                self._seen_message_ids.append(message_id)
                if len(self._seen_message_ids) > 5000:
                    self._seen_message_ids = self._seen_message_ids[-2000:]
            yield self.decode_message(raw)

    def decode_message(self, msg: dict[str, Any]) -> InboundMessage:
        text = self.extract_text(msg)
        attachments = self.download_attachments(msg.get("item_list", []) or [])
        return InboundMessage(
            message_id=int(msg.get("message_id", 0) or 0),
            from_user_id=str(msg.get("from_user_id", "") or ""),
            to_user_id=str(msg.get("to_user_id", "") or ""),
            context_token=str(msg.get("context_token", "") or ""),
            text=text,
            attachments=attachments,
            raw=msg,
        )

    def send_text(self, to_user_id: str, text: str, context_token: str = "") -> dict[str, Any]:
        msg = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"pyclient-{uuid.uuid4().hex[:16]}",
            "message_type": MSG_BOT,
            "message_state": STATE_FINISH,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
        }
        if context_token:
            msg["context_token"] = context_token
        return self._post("ilink/bot/sendmessage", {"msg": msg, "base_info": {"channel_version": VER}})

    def send_typing(self, to_user_id: str, typing_ticket: str = "", cancel: bool = False) -> dict[str, Any]:
        return self._post(
            "ilink/bot/sendtyping",
            {
                "ilink_user_id": to_user_id,
                "typing_ticket": typing_ticket,
                "status": 2 if cancel else 1,
                "base_info": {"channel_version": VER},
            },
        )

    def get_typing_ticket(self, to_user_id: str, context_token: str = "") -> str:
        payload = {"ilink_user_id": to_user_id}
        if context_token:
            payload["context_token"] = context_token
        return self._post("ilink/bot/getconfig", payload).get("typing_ticket", "")

    @staticmethod
    def _encrypt(raw: bytes, aes_key: bytes) -> bytes:
        pad = 16 - (len(raw) % 16)
        return AES.new(aes_key, AES.MODE_ECB).encrypt(raw + bytes([pad] * pad))

    @staticmethod
    def _decrypt(ciphertext: bytes, aes_key: bytes) -> bytes:
        plain = AES.new(aes_key, AES.MODE_ECB).decrypt(ciphertext)
        return plain[:-plain[-1]]

    def _upload(self, filekey: str, upload_param: str, raw: bytes, aes_key: bytes, timeout: int = 120, upload_url: str = "") -> dict[str, Any]:
        url = upload_url.strip() if upload_url else f"{CDN_BASE}/upload?encrypted_query_param={quote(upload_param)}&filekey={filekey}"
        encrypted = self._encrypt(raw, aes_key)
        last_err = None
        for attempt in range(1, 4):
            try:
                response = requests.post(url, data=encrypted, headers={"Content-Type": "application/octet-stream", "User-Agent": UA}, timeout=timeout)
                if 400 <= response.status_code < 500:
                    message = response.headers.get("x-error-message") or response.text[:300]
                    raise RuntimeError(f"CDN upload client error {response.status_code}: {message}")
                if response.status_code != 200:
                    message = response.headers.get("x-error-message") or f"status {response.status_code}"
                    raise RuntimeError(f"CDN upload server error: {message}")
                encrypted_query = response.headers.get("x-encrypted-param", "")
                if not encrypted_query:
                    raise RuntimeError("CDN upload response missing x-encrypted-param")
                return {
                    "encrypt_query_param": encrypted_query,
                    "aes_key": base64.b64encode(aes_key.hex().encode()).decode(),
                    "encrypt_type": 1,
                }
            except Exception as exc:  # pragma: no branch
                last_err = exc
                if "client error" in str(exc) or attempt >= 3:
                    break
                print(f"[WeChat] upload retry {attempt}: {exc}")
        raise last_err

    def _request_upload_slot(self, to_user_id: str, raw: bytes, media_type: int, aes_key: bytes, thumb_raw: bytes = b"") -> dict[str, Any]:
        ciphertext_size = ((len(raw) // 16) + 1) * 16
        body = {
            "filekey": uuid.uuid4().hex,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": len(raw),
            "rawfilemd5": hashlib.md5(raw).hexdigest(),
            "filesize": ciphertext_size,
            "no_need_thumb": not thumb_raw,
            "aeskey": aes_key.hex(),
            "base_info": {"channel_version": VER},
        }
        thumb_ciphertext_size = 0
        if thumb_raw:
            thumb_ciphertext_size = ((len(thumb_raw) // 16) + 1) * 16
            body.update(
                {
                    "thumb_rawsize": len(thumb_raw),
                    "thumb_rawfilemd5": hashlib.md5(thumb_raw).hexdigest(),
                    "thumb_filesize": thumb_ciphertext_size,
                }
            )
        slot = self._post("ilink/bot/getuploadurl", body)
        slot["_ciphertext_size"] = ciphertext_size
        slot["_thumb_ciphertext_size"] = thumb_ciphertext_size
        slot["_filekey"] = body["filekey"]
        return slot

    @staticmethod
    def _make_thumb(file_path: str) -> tuple[bytes, int, int]:
        if Image is None:
            return b"", 0, 0
        from io import BytesIO

        image = Image.open(file_path)
        image.thumbnail((240, 240))
        width, height = image.size
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        bio = BytesIO()
        image.save(bio, format="JPEG", quality=85)
        return bio.getvalue(), width, height

    def _send_message_with_item(self, to_user_id: str, item_type: int, item_key: str, item_value: dict[str, Any], context_token: str = "") -> dict[str, Any]:
        msg = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": f"pyclient-{uuid.uuid4().hex[:16]}",
            "message_type": MSG_BOT,
            "message_state": STATE_FINISH,
            "item_list": [{"type": item_type, item_key: item_value}],
        }
        if context_token:
            msg["context_token"] = context_token
        return self._post("ilink/bot/sendmessage", {"msg": msg, "base_info": {"channel_version": VER}})

    def send_file(self, to_user_id: str, file_path: str, context_token: str = "") -> dict[str, Any]:
        path = Path(file_path)
        raw = path.read_bytes()
        aes_key = os.urandom(16)
        slot = self._request_upload_slot(to_user_id, raw, UPLOAD_MEDIA_FILE, aes_key)
        media = self._upload(slot["_filekey"], slot.get("upload_param", ""), raw, aes_key=aes_key, upload_url=slot.get("upload_full_url", ""))
        item = {"media": media, "file_name": path.name, "len": str(len(raw))}
        return self._send_message_with_item(to_user_id, ITEM_FILE, "file_item", item, context_token=context_token)

    def send_image(self, to_user_id: str, file_path: str, context_token: str = "") -> dict[str, Any]:
        path = Path(file_path)
        raw = path.read_bytes()
        aes_key = os.urandom(16)
        thumb_raw, width, height = self._make_thumb(str(path))
        slot = self._request_upload_slot(to_user_id, raw, UPLOAD_MEDIA_IMAGE, aes_key, thumb_raw=thumb_raw)
        media = self._upload(slot["_filekey"], slot.get("upload_param", ""), raw, aes_key=aes_key, upload_url=slot.get("upload_full_url", ""))
        thumb_media = media
        thumb_size = slot["_ciphertext_size"]
        if thumb_raw and (slot.get("thumb_upload_param") or slot.get("thumb_upload_full_url")):
            thumb_media = self._upload(slot["_filekey"], slot.get("thumb_upload_param", ""), thumb_raw, aes_key=aes_key, upload_url=slot.get("thumb_upload_full_url", ""))
            thumb_size = slot["_thumb_ciphertext_size"]
        item = {
            "media": media,
            "thumb_media": thumb_media,
            "mid_size": slot["_ciphertext_size"],
            "thumb_size": thumb_size,
            "thumb_width": width,
            "thumb_height": height,
        }
        return self._send_message_with_item(to_user_id, ITEM_IMAGE, "image_item", item, context_token=context_token)

    def send_video(self, to_user_id: str, file_path: str, context_token: str = "") -> dict[str, Any]:
        path = Path(file_path)
        raw = path.read_bytes()
        aes_key = os.urandom(16)
        slot = self._request_upload_slot(to_user_id, raw, UPLOAD_MEDIA_VIDEO, aes_key)
        media = self._upload(slot["_filekey"], slot.get("upload_param", ""), raw, aes_key=aes_key, upload_url=slot.get("upload_full_url", ""))
        item = {"media": media, "video_size": slot["_ciphertext_size"]}
        return self._send_message_with_item(to_user_id, ITEM_VIDEO, "video_item", item, context_token=context_token)

    def _probe_duration_seconds(self, file_path: str) -> int:
        if shutil.which("ffprobe"):
            try:
                proc = subprocess.run(
                    [
                        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=True,
                    **hidden_windows_subprocess_kwargs(),
                )
                seconds = float((proc.stdout or "0").strip() or 0)
                if seconds > 0:
                    return max(1, int(math.ceil(seconds)))
            except Exception:
                pass
        if file_path.lower().endswith(".wav"):
            try:
                with wave.open(file_path, "rb") as wav_file:
                    frames = wav_file.getnframes()
                    rate = wav_file.getframerate() or 1
                    return max(1, int(math.ceil(frames / float(rate))))
            except Exception:
                pass
        return 1

    def _shell_quote(self, value: str) -> str:
        if os.name == "nt":
            return subprocess.list2cmdline([str(value)])
        return shlex.quote(str(value))

    def _format_voice_encoder_cmd(self, input_path: str, output_path: str) -> str:
        return self.voice_encoder_cmd.format(
            input=input_path,
            output=output_path,
            input_q=self._shell_quote(input_path),
            output_q=self._shell_quote(output_path),
        )

    def _maybe_transcode_voice(self, file_path: str) -> tuple[str, bool]:
        if file_path.lower().endswith(".silk") or not self.voice_encoder_cmd:
            return file_path, False
        fd, output_path = tempfile.mkstemp(prefix="ga_wechat_voice_", suffix=".silk")
        os.close(fd)
        try:
            cmd = self._format_voice_encoder_cmd(file_path, output_path)
            subprocess.run(cmd, shell=True, timeout=300, check=True, **hidden_windows_subprocess_kwargs())
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path, True
        except Exception as exc:
            print(f"[WeChat] voice transcode failed: {exc}")
        try:
            os.remove(output_path)
        except OSError:
            pass
        return file_path, False

    def send_voice(self, to_user_id: str, file_path: str, context_token: str = "", transcript: str = "") -> dict[str, Any]:
        prepared_path, temporary = self._maybe_transcode_voice(file_path)
        try:
            path = Path(prepared_path)
            raw = path.read_bytes()
            aes_key = os.urandom(16)
            slot = self._request_upload_slot(to_user_id, raw, UPLOAD_MEDIA_VOICE, aes_key)
            media = self._upload(slot["_filekey"], slot.get("upload_param", ""), raw, aes_key=aes_key, upload_url=slot.get("upload_full_url", ""))
            item = {"media": media, "playtime": self._probe_duration_seconds(str(path))}
            if transcript:
                item["text"] = transcript
            return self._send_message_with_item(to_user_id, ITEM_VOICE, "voice_item", item, context_token=context_token)
        finally:
            if temporary:
                try:
                    os.remove(prepared_path)
                except OSError:
                    pass

    def send_audio_best_effort(self, to_user_id: str, file_path: str, context_token: str = "") -> dict[str, Any]:
        try:
            return self.send_voice(to_user_id, file_path, context_token=context_token)
        except Exception as exc:
            print(f"[WeChat] send_voice failed, fallback to file: {exc}")
            return self.send_file(to_user_id, file_path, context_token=context_token)

    def send_path(self, to_user_id: str, file_path: str, context_token: str = "") -> dict[str, Any]:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in IMAGE_EXTS:
            return self.send_image(to_user_id, file_path, context_token=context_token)
        if ext in VIDEO_EXTS:
            return self.send_video(to_user_id, file_path, context_token=context_token)
        if ext in VOICE_EXTS:
            return self.send_audio_best_effort(to_user_id, file_path, context_token=context_token)
        return self.send_file(to_user_id, file_path, context_token=context_token)

    def _decrypt_item_to_attachment(self, item_key: str, item: dict[str, Any]) -> AttachmentRef | None:
        media = item.get("media") or {}
        encrypted_query = media.get("encrypt_query_param")
        if not encrypted_query:
            return None
        key_value = media.get("aes_key") or item.get("aeskey", "")
        if not key_value:
            return None
        try:
            if media.get("aes_key"):
                aes_key = bytes.fromhex(base64.b64decode(key_value).decode())
            else:
                aes_key = bytes.fromhex(key_value)
            response = requests.get(
                f"{CDN_BASE}/download?encrypted_query_param={quote(encrypted_query)}",
                headers={"User-Agent": UA},
                timeout=60,
            )
            response.raise_for_status()
            plain = self._decrypt(response.content, aes_key)
        except Exception as exc:
            print(f"[WeChat] media download error ({item_key}): {exc}")
            return None
        default_ext = MEDIA_EXTS.get(item_key, ".bin")
        name = item.get("file_name") or f"{uuid.uuid4().hex[:8]}{default_ext}"
        path = self._unique_path(self.media_dir / os.path.basename(name))
        path.write_bytes(plain)
        transcript = self._voice_transcript(item) if item_key == "voice_item" else ""
        kind = {"image_item": "image", "video_item": "video", "file_item": "file", "voice_item": "voice"}.get(item_key, "file")
        return AttachmentRef(kind=kind, path=str(path), name=path.name, transcript=transcript, size=len(plain), media_key=item_key)

    @staticmethod
    def _voice_transcript(item: dict[str, Any]) -> str:
        for key in ("text", "recognize_text", "translate_text", "asr_text", "stt_text"):
            value = str(item.get(key, "") or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        counter = 1
        while True:
            candidate = path.with_name(f"{stem}_{counter}{suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def download_attachments(self, items: Iterable[dict[str, Any]]) -> list[AttachmentRef]:
        attachments: list[AttachmentRef] = []
        for entry in items or []:
            for item_key in ("image_item", "video_item", "file_item", "voice_item"):
                sub = entry.get(item_key)
                if not sub:
                    continue
                attachment = self._decrypt_item_to_attachment(item_key, sub)
                if attachment:
                    attachments.append(attachment)
                break
        return attachments
