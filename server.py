#!/usr/bin/env python3
"""DISTRICT_9 server - run on any always-on machine/VPS."""
import asyncio, json, sqlite3, hashlib, secrets, time, os, re

PORT = int(os.environ.get("PORT") or os.environ.get("BF_PORT") or 5555)
DB_PATH = os.environ.get("BF_DB", "district9.db")
MAX_FILE = 20 * 1024 * 1024
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(uid TEXT PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE, salt TEXT, hash TEXT, created REAL);
CREATE TABLE IF NOT EXISTS friends(a TEXT, b TEXT, PRIMARY KEY(a,b));
CREATE TABLE IF NOT EXISTS requests(frm TEXT, too TEXT, PRIMARY KEY(frm,too));
CREATE TABLE IF NOT EXISTS inbox(id INTEGER PRIMARY KEY AUTOINCREMENT, to_uid TEXT, payload TEXT);
""")
try:
    db.execute("ALTER TABLE users ADD COLUMN rec TEXT")
except sqlite3.OperationalError:
    pass
online = {}  # uid -> writer


def hp(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()


def new_code():
    a = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "-".join("".join(secrets.choice(a) for _ in range(4)) for _ in range(3))


def new_uid():
    while True:
        u = "BF" + "".join(secrets.choice("0123456789") for _ in range(6))
        if not db.execute("SELECT 1 FROM users WHERE uid=?", (u,)).fetchone():
            return u


def resolve(q):
    q = (q or "").strip()
    return db.execute("SELECT uid,name FROM users WHERE uid=? OR name=?", (q.upper(), q)).fetchone()


def name_of(uid):
    r = db.execute("SELECT name FROM users WHERE uid=?", (uid,)).fetchone()
    return r[0] if r else "?"


def are_friends(a, b):
    return db.execute("SELECT 1 FROM friends WHERE a=? AND b=?", (a, b)).fetchone() is not None


async def send(w, obj):
    w.write((json.dumps(obj) + "\n").encode())
    await w.drain()


async def deliver(uid, obj):
    w = online.get(uid)
    if w:
        try:
            await send(w, obj)
            return
        except Exception:
            online.pop(uid, None)
    if obj["type"] in ("msg", "file"):
        db.execute("INSERT INTO inbox(to_uid,payload) VALUES(?,?)", (uid, json.dumps(obj)))
        db.commit()


async def resp(w, ok, *lines, **extra):
    await send(w, {"type": "resp", "ok": ok, "lines": list(lines), **extra})


async def handle(r, w):
    uid = None
    fails = 0
    rec_code = None
    try:
        while True:
            line = await r.readline()
            if not line:
                break
            try:
                m = json.loads(line)
            except Exception:
                continue
            cmd = m.get("cmd")

            # ---------- auth ----------
            if uid is None:
                name, pw = (m.get("name") or "").strip(), m.get("pw") or ""
                if cmd == "reset":
                    code = (m.get("code") or "").strip().upper()
                    row = db.execute("SELECT uid,salt,rec FROM users WHERE name=?", (name,)).fetchone()
                    if not row or not row[2] or not secrets.compare_digest(hp(code, row[1]), row[2]):
                        fails += 1
                        await resp(w, False, "Wrong username or recovery code")
                        if fails >= 5:
                            break
                        continue
                    if len(pw) < 6:
                        await resp(w, False, "Password must be 6+ chars"); continue
                    salt, rc = secrets.token_hex(16), new_code()
                    db.execute("UPDATE users SET salt=?,hash=?,rec=? WHERE uid=?", (salt, hp(pw, salt), hp(rc, salt), row[0]))
                    db.commit()
                    await send(w, {"type": "reset", "ok": True, "recovery": rc, "lines": ["Password changed"]})
                    continue
                if cmd == "signup":
                    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", name):
                        await resp(w, False, "Username: 3-20 letters/numbers/_"); continue
                    if len(pw) < 6:
                        await resp(w, False, "Password must be 6+ chars"); continue
                    if db.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone():
                        await resp(w, False, "Username taken"); continue
                    salt = secrets.token_hex(16)
                    uid = new_uid()
                    rec_code = new_code()
                    db.execute("INSERT INTO users(uid,name,salt,hash,created,rec) VALUES(?,?,?,?,?,?)",
                               (uid, name, salt, hp(pw, salt), time.time(), hp(rec_code, salt)))
                    db.commit()
                elif cmd == "login":
                    row = db.execute("SELECT uid,salt,hash FROM users WHERE name=?", (name,)).fetchone()
                    if not row or not secrets.compare_digest(hp(pw, row[1]), row[2]):
                        fails += 1
                        await resp(w, False, "Wrong username or password")
                        if fails >= 5:
                            break
                        continue
                    uid = row[0]
                else:
                    await resp(w, False, "Login or signup first"); continue
                if uid in online:
                    try: online[uid].close()
                    except Exception: pass
                online[uid] = w
                await send(w, {"type": "auth", "ok": True, "uid": uid, "name": name_of(uid), "recovery": rec_code})
                for row in db.execute("SELECT id,payload FROM inbox WHERE to_uid=? ORDER BY id", (uid,)).fetchall():
                    await send(w, json.loads(row[1]))
                    db.execute("DELETE FROM inbox WHERE id=?", (row[0],))
                db.commit()
                continue

            # ---------- commands ----------
            if cmd == "find":
                q = (m.get("q") or "").strip()
                rows = db.execute("SELECT uid,name FROM users WHERE (uid=? OR name LIKE ?) AND uid<>? LIMIT 10",
                                  (q.upper(), f"%{q}%", uid)).fetchall()
                if not rows:
                    await resp(w, False, "No users found"); continue
                await resp(w, True, *[f"{n}  [{u}]  {'online' if u in online else 'offline'}" for u, n in rows])

            elif cmd == "add":
                t = resolve(m.get("to"))
                if not t or t[0] == uid:
                    await resp(w, False, "User not found"); continue
                if are_friends(uid, t[0]):
                    await resp(w, False, "Already friends"); continue
                if db.execute("SELECT 1 FROM requests WHERE frm=? AND too=?", (t[0], uid)).fetchone():
                    db.execute("DELETE FROM requests WHERE frm=? AND too=?", (t[0], uid))
                    db.executemany("INSERT OR IGNORE INTO friends VALUES(?,?)", [(uid, t[0]), (t[0], uid)])
                    db.commit()
                    await resp(w, True, f"You and {t[1]} are now friends")
                    await deliver(t[0], {"type": "notice", "text": f"{name_of(uid)} accepted your request"})
                else:
                    db.execute("INSERT OR IGNORE INTO requests VALUES(?,?)", (uid, t[0])); db.commit()
                    await resp(w, True, f"Request sent to {t[1]}")
                    await deliver(t[0], {"type": "notice", "text": f"Friend request from {name_of(uid)} [{uid}] - /accept {uid}"})

            elif cmd == "requests":
                rows = db.execute("SELECT frm FROM requests WHERE too=?", (uid,)).fetchall()
                await resp(w, True, *([f"{name_of(f)} [{f}]" for (f,) in rows] or ["No pending requests"]))

            elif cmd == "accept":
                t = resolve(m.get("to"))
                if not t or not db.execute("SELECT 1 FROM requests WHERE frm=? AND too=?", (t[0], uid)).fetchone():
                    await resp(w, False, "No request from that user"); continue
                db.execute("DELETE FROM requests WHERE frm=? AND too=?", (t[0], uid))
                db.executemany("INSERT OR IGNORE INTO friends VALUES(?,?)", [(uid, t[0]), (t[0], uid)])
                db.commit()
                await resp(w, True, f"You and {t[1]} are now friends")
                await deliver(t[0], {"type": "notice", "text": f"{name_of(uid)} accepted your request"})

            elif cmd == "friends":
                rows = db.execute("SELECT b FROM friends WHERE a=?", (uid,)).fetchall()
                await resp(w, True, *([f"{name_of(b)}  [{b}]  {'online' if b in online else 'offline'}" for (b,) in rows]
                                      or ["No friends yet - use /find then /add"]))

            elif cmd in ("msg", "file"):
                t = resolve(m.get("to"))
                if not t or not are_friends(uid, t[0]):
                    await resp(w, False, "Not your friend"); continue
                if cmd == "msg":
                    await deliver(t[0], {"type": "msg", "from": uid, "name": name_of(uid), "text": m.get("text", ""), "ts": time.time()})
                    continue
                data = m.get("data", "")
                if len(data) > MAX_FILE * 4 // 3 + 16:
                    await resp(w, False, "File too big (max 20MB)"); continue
                await deliver(t[0], {"type": "file", "from": uid, "name": name_of(uid),
                                     "fname": os.path.basename(m.get("fname", "file")), "data": data})
                await resp(w, True, f"File sent to {t[1]}")
            else:
                await resp(w, False, "Unknown command")
    except (ConnectionError, asyncio.IncompleteReadError, ValueError):
        pass
    finally:
        if uid and online.get(uid) is w:
            online.pop(uid, None)
        try: w.close()
        except Exception: pass


async def main():
    srv = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=40 * 1024 * 1024)
    print(f"[DISTRICT_9] v2 recovery - server up on port {PORT}")
    async with srv:
        await srv.serve_forever()

asyncio.run(main())
