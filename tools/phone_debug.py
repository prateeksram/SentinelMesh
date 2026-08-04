"""Debug helper: inspect the game page in Chrome on the phone via CDP.

Usage (with `adb forward tcp:9222 localabstract:chrome_devtools_remote` active):
    python tools/phone_debug.py                # probe page state
    python tools/phone_debug.py close-dupes    # close duplicate game tabs
    python tools/phone_debug.py reload         # reload the game tab
    python tools/phone_debug.py eval "<expr>"  # evaluate JS in the game tab
"""
import asyncio
import json
import sys
import urllib.request

import aiohttp

BASE = "http://127.0.0.1:9222"


def tabs():
    return [t for t in json.loads(urllib.request.urlopen(BASE + "/json", timeout=8).read())
            if t.get("type") == "page"]


def game_tabs():
    return [t for t in tabs() if "phone.html" in t.get("url", "")]


async def ev(ws, expr, i=1):
    await ws.send_json({"id": i, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True,
                                   "awaitPromise": True}})
    while True:
        msg = await ws.receive()
        r = json.loads(msg.data)
        if r.get("id") == i:
            res = r.get("result", {})
            if "exceptionDetails" in res:
                return "JS ERROR: " + res["exceptionDetails"].get("text", "?")
            return res.get("result", {}).get("value")


async def probe(tab):
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(tab["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            checks = [
                ("ai badge", "document.getElementById('ai').textContent"),
                ("body badge", "document.getElementById('bodyChk').textContent"),
                ("cam perm", "navigator.permissions.query({name:'camera'}).then(p=>p.state)"),
                ("video ready", "document.getElementById('video').readyState"),
                ("ws led on", "document.getElementById('led').className.includes('on')"),
                ("big", "document.getElementById('big').textContent"),
                ("hint", "document.getElementById('hint').textContent"),
            ]
            for i, (name, expr) in enumerate(checks, start=1):
                print(f"{name}: {await ev(ws, expr, i)}")


async def run_eval(tab, expr):
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(tab["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            print(await ev(ws, expr))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    gts = game_tabs()
    if not gts:
        print("no game tab open")
        return
    if cmd == "close-dupes":
        for t in gts[1:]:
            urllib.request.urlopen(f"{BASE}/json/close/{t['id']}", timeout=5).read()
            print("closed", t["id"], t["url"])
        print("kept", gts[0]["id"], gts[0]["url"])
    elif cmd == "reload":
        asyncio.run(run_eval(gts[0], "location.reload(); 'reloading'"))
    elif cmd == "eval":
        asyncio.run(run_eval(gts[0], sys.argv[2]))
    else:
        print("tab:", gts[0]["id"], gts[0]["url"])
        asyncio.run(probe(gts[0]))


main()
