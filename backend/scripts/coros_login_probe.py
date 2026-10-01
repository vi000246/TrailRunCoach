"""Diagnose COROS login: log in on each region and immediately try listing activities.

Run it yourself (it asks for the password; nothing is stored):
    .venv\\Scripts\\python.exe -m backend.scripts.coros_login_probe
It prints only result codes / messages, never the token.
"""
import asyncio
import getpass
from datetime import date, timedelta

from backend.sync import http
from backend.sync.coros_client import COROS_BASES, _headers, _md5


async def main() -> None:
    email = input("COROS email: ").strip()
    pwd = getpass.getpass("COROS password (not stored): ")
    payload = {"account": email, "accountType": 2, "pwd": _md5(pwd)}
    today = date.today()
    params = {"size": 1, "pageNumber": 1,
              "startDay": (today - timedelta(days=30)).strftime("%Y%m%d"), "endDay": today.strftime("%Y%m%d")}
    for region, base in COROS_BASES.items():
        async with http.client(timeout=30) as c:
            r = await c.post(f"{base}/account/login", json=payload, headers=_headers())
        body = r.json() if r.status_code == 200 else {}
        print(f"[login {region}] HTTP {r.status_code} result={body.get('result')} message={body.get('message')!r}")
        if body.get("result") != "0000":
            continue
        tok, uid = body["data"]["accessToken"], str(body["data"].get("userId", ""))
        print(f"   token length {len(tok)}, region field {body['data'].get('regionId')!r}")
        for b in COROS_BASES.values():
            async with http.client(timeout=30) as c:
                q = await c.get(f"{b}/activity/query", headers=_headers(tok, uid), params=params)
            qb = q.json() if q.status_code == 200 else {}
            print(f"   list on {b}: HTTP {q.status_code} result={qb.get('result')} message={qb.get('message')!r}")
        return


if __name__ == "__main__":
    asyncio.run(main())
