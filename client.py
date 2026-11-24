#!/usr/bin/env python3
"""DISTRICT_9 client."""
import socket, json, threading, os, sys, getpass, base64, time, shlex

_h = os.environ.get("BF_HOST", "127.0.0.1")
HOST, _, _p = _h.partition(":")
PORT = int(_p or os.environ.get("BF_PORT", 5555))
G, R, D, X = "\033[92m", "\033[91m", "\033[90m", "\033[0m"
BANNER = G + r"""
 ____ ___ ____ _____ ____  ___ ____ _____   ___
|  _ \_ _/ ___|_   _|  _ \|_ _/ ___|_   _| / _ \
| | | | |\___ \ | | | |_) || | |     | |  | (_) |
| |_| | | ___) || | |  _ < | | |___  | |   \__, |
|____/___|____/ |_| |_| \_\___\____| |_|     /_/
          [ D I S T R I C T _ 9 ]
""" + X


def storage_dir():
    for base in (os.path.expanduser("~/storage/shared"), "/storage/emulated/0", "/sdcard"):
        if os.path.isdir(base) and os.access(base, os.W_OK):
            return os.path.join(base, "BLACKFOX-inc")
    return os.path.expanduser("~/BLACKFOX-inc")


BOX = storage_dir()
sock = socket.create_connection((HOST, PORT))
f = sock.makefile("rw", encoding="utf-8", newline="\n")
me = {"uid": None, "name": None}
chat = {"to": None}


def tx(obj):
    f.write(json.dumps(obj) + "\n")
    f.flush()


def prompt():
    p = f"{G}[{chat['to']}]{X} " if chat["to"] else f"{G}district9>{X} "
    print(p, end="", flush=True)


def auth():
    print(BANNER)
    while True:
        c = input("1) Sign up   2) Log in   > ").strip()
        if c not in ("1", "2"):
            continue
        name = input("Username: ").strip()
        pw = getpass.getpass("Password: ")
        tx({"cmd": "signup" if c == "1" else "login", "name": name, "pw": pw})
        m = json.loads(f.readline())
        if m.get("type") == "auth":
            me.update(uid=m["uid"], name=m["name"])
            if c == "1":
                print(f"{G}Account created. Your ID: {m['uid']}{X}")
            return
        print(R + "\n".join(m.get("lines", [])) + X)


def save_file(m):
    os.makedirs(BOX, exist_ok=True)
    fname = os.path.basename(m["fname"]) or "file"
    path = os.path.join(BOX, fname)
    if os.path.exists(path):
        path = os.path.join(BOX, f"{int(time.time())}_{fname}")
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(m["data"]))
    return path


def reader():
    try:
        for line in f:
            m = json.loads(line)
            t = m.get("type")
            if t == "resp":
                col = G if m["ok"] else R
                print(f"\r{col}" + "\n".join(m["lines"]) + X)
            elif t == "msg":
                print(f"\r{G}{m['name']}{X} {D}{time.strftime('%H:%M', time.localtime(m['ts']))}{X}: {m['text']}")
            elif t == "file":
                print(f"\r{G}{m['name']} sent a file -> {save_file(m)}{X}")
            elif t == "notice":
                print(f"\r{D}* {m['text']}{X}")
            prompt()
    except Exception:
        pass
    print(f"\n{R}Disconnected.{X}")
    os._exit(0)


HELP = """/find <name|id>     search users
/add <name|id>      send friend request
/requests           pending requests
/accept <name|id>   accept request
/friends            friend list
/chat <name|id>     open chat (just type to send)
/leave              close chat
/send <name|id> <file>   share a file (in chat: /send <file>)
/whoami             your ID
/quit               exit"""


def main():
    auth()
    os.makedirs(BOX, exist_ok=True)
    print(f"{G}Logged in as {me['name']} [{me['uid']}]  |  files -> {BOX}{X}\nType /help")
    threading.Thread(target=reader, daemon=True).start()
    time.sleep(0.3)
    while True:
        prompt()
        try:
            s = input().strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not s:
            continue
        if not s.startswith("/"):
            if chat["to"]:
                tx({"cmd": "msg", "to": chat["to"], "text": s})
            else:
                print("Use /chat <user> first, or /help")
            continue
        try:
            p = shlex.split(s)
        except ValueError:
            continue
        c, a = p[0].lower(), p[1:]
        if c == "/quit":
            break
        elif c == "/help":
            print(HELP)
        elif c == "/whoami":
            print(f"{me['name']} [{me['uid']}]")
        elif c == "/leave":
            chat["to"] = None
        elif c == "/chat" and a:
            chat["to"] = a[0]
        elif c == "/find" and a:
            tx({"cmd": "find", "q": a[0]})
        elif c == "/add" and a:
            tx({"cmd": "add", "to": a[0]})
        elif c == "/accept" and a:
            tx({"cmd": "accept", "to": a[0]})
        elif c == "/requests":
            tx({"cmd": "requests"})
        elif c == "/friends":
            tx({"cmd": "friends"})
        elif c == "/send":
            if len(a) == 1 and chat["to"]:
                to, path = chat["to"], a[0]
            elif len(a) >= 2:
                to, path = a[0], " ".join(a[1:])
            else:
                print("Usage: /send <user> <file>"); continue
            path = os.path.expanduser(path)
            if not os.path.isfile(path):
                print(R + "File not found" + X); continue
            if os.path.getsize(path) > 20 * 1024 * 1024:
                print(R + "Max 20MB" + X); continue
            with open(path, "rb") as fh:
                tx({"cmd": "file", "to": to, "fname": os.path.basename(path),
                    "data": base64.b64encode(fh.read()).decode()})
        else:
            print("Unknown command, /help")


main()
