#!/usr/bin/env python3
"""
Firebase DB health checker (standalone).

Usage:
    python3 check_firebases.py                # check all DBs in firebases.json
    python3 check_firebases.py --limit 50     # check first 50 only
    python3 check_firebases.py --concurrency 40
    python3 check_firebases.py --url https://x-default-rtdb.firebaseio.com

Writes:
    db_status.json   -> per-DB health used by firebase_bot.py
    db_report.txt    -> human readable report
"""
import argparse, asyncio, json, os, sys, time
from datetime import datetime

import aiohttp

DB_FILE        = "firebases.json"
DB_STATUS_FILE = "db_status.json"
DEVICES_PATH   = "devices"
TIMEOUT        = aiohttp.ClientTimeout(total=45, sock_connect=15, sock_read=30)

KIND_LABEL = {
    200: "OK",
    400: "BAD REQUEST",
    401: "UNAUTHORIZED",
    403: "PERMISSION DENIED",
    404: "NOT FOUND / DELETED",
    423: "LOCKED (quota / billing)",
    429: "RATE LIMITED",
    500: "SERVER ERROR",
    502: "BAD GATEWAY",
    503: "UNAVAILABLE",
    504: "TIMEOUT",
}


async def check_one(session, fb, sem, results):
    url = f"{fb.rstrip('/')}/{DEVICES_PATH}.json?shallow=true"
    async with sem:
        try:
            async with session.get(url, timeout=TIMEOUT) as r:
                body = await r.text()
                results[fb] = (r.status, body[:120])
        except Exception as e:
            results[fb] = (0, f"{type(e).__name__}: {e}"[:120])


def kind_of(status):
    if status == 200:
        return "ok"
    if status == 423:
        return "locked"
    if status == 404:
        return "notfound"
    if status in (401, 403):
        return "denied"
    if status == 400:
        return "bad"
    return "error"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=40)
    ap.add_argument("--url", action="append", default=[])
    args = ap.parse_args()

    if args.url:
        fbs = args.url
    else:
        if not os.path.exists(DB_FILE):
            sys.exit(f"{DB_FILE} nahi mila. Bot folder me rakho.")
        with open(DB_FILE, encoding="utf-8") as f:
            fbs = json.load(f)
    if args.limit:
        fbs = fbs[:args.limit]

    print(f"Checking {len(fbs)} firebase databases ...")
    results = {}
    sem = asyncio.Semaphore(args.concurrency)
    conn = aiohttp.TCPConnector(limit=args.concurrency * 2,
                                limit_per_host=args.concurrency)
    async with aiohttp.ClientSession(connector=conn) as session:
        await asyncio.gather(*[check_one(session, fb, sem, results) for fb in fbs])

    counts, status_map, lines = {}, {}, []
    for fb in fbs:
        status, msg = results.get(fb, (0, "no result"))
        kind = kind_of(status)
        counts[kind] = counts.get(kind, 0) + 1
        status_map[fb] = {"kind": kind, "code": status, "msg": msg,
                          "ts": int(time.time())}
        lines.append(f"{status:>4}  {KIND_LABEL.get(status, 'ERROR'):<24}  {fb}")

    with open(DB_STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_map, f, indent=2, ensure_ascii=False)

    lines.sort()
    header = [f"Firebase health report - {datetime.now():%Y-%m-%d %H:%M:%S}",
              f"Total: {len(fbs)}", ""]
    for kind, label in (("ok", "Working"), ("locked", "Locked (423)"),
                        ("notfound", "Not found (404)"), ("denied", "Denied (401/403)"),
                        ("bad", "Bad request (400)"), ("error", "Other error")):
        if counts.get(kind):
            header.append(f"{label:<22}: {counts[kind]}")
    header.append("")
    with open("db_report.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(header + lines) + "\n")

    print("\n".join(header))
    print("Saved -> db_status.json , db_report.txt")


if __name__ == "__main__":
    asyncio.run(main())
