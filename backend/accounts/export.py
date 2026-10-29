"""
下載我的資料 (SP-386; auth-and-demo plan §4.2): the signed-in user's own data as one zip, read off
the data registry (backend/data_registry.py, SP-311: `export_entries`) — what the user made (USER)
and what came in (IMPORTED); never a SECRET (credentials, tokens, sealed passwords, the server's
files) nor a DERIVED cache.

    README.txt          what is where (the user's language)
    manifest.json       app, format, created_at, tenant, tables {name: rows}, files {count, bytes},
                        the classes held and left out
    account.json        the account (e-mail, name, created, plan) and its consent history
                        (SP-326 §7.1: every acceptance of the terms, every analysis withdrawal;
                        every coach accepted or ended, SP-789) and their coaching (coaching.export_record)
    tables/<name>.json  every USER / IMPORTED table of the tenant DB, a list of rows; a table's
                        secret_fields and EXPORT_OMIT_FIELDS left out, user_settings without the keys
                        that hold a credential (EXPORT_SECRET_SETTINGS: the ICS feed's link token)
    files/<rel>         every USER / IMPORTED file of the tenant, as stored, at its path relative to
                        the tenant folder (fit/**: the FIT originals, gzip; event_gpx/**: gzip GPX)

Streamed like 下載我的全部 FIT (api/archive.py): the tables are read up front in one read
transaction (a consistent snapshot, a few MB at most), then the files go out one at a time as the
zip is sent — it never sits in memory or on disk, and the download starts at once. So no
background job (jobs.py) and no download link to expire: a user's data is tens to a few hundred
MB, mostly FIT (2026-10-13; auth-and-demo plan §10.1), and nothing has to wait for the whole zip.
A new registry entry of those classes is carried without a change here
(backend/tests/test_account_data_sp386.py makes one file per entry).
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import sqlite3
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from backend import data_registry as R

FORMAT = "export-1"
APP = "TrailRunCoach"          # manifest.json's "app": the format's name, not the product's (kept, SP-1001)


@dataclass
class Export:
    head: list = field(default_factory=list)        # (name in the zip, bytes): made up front
    files: list = field(default_factory=list)       # (stored file, name in the zip): read while sending
    manifest: dict = field(default_factory=dict)


def _value(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"base64": base64.b64encode(bytes(v)).decode("ascii")}
    return v


def read_tables(db: Path) -> dict[str, list[dict]]:
    """{table: rows} of the tenant DB's USER / IMPORTED tables, in one read transaction."""
    tables, _files = R.export_entries()
    if not Path(db).exists():
        return {}
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN")
        present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        out = {}
        for t in tables:
            if t.name not in present:
                continue
            drop = set(t.secret_fields) | set(R.EXPORT_OMIT_FIELDS.get(t.name, ()))
            rows = []
            for r in con.execute('SELECT * FROM "%s"' % t.name.replace('"', '""')):
                row = {k: _value(r[k]) for k in r.keys() if k not in drop}
                if t.name == "user_settings" and row.get("key") in R.EXPORT_SECRET_SETTINGS:
                    continue
                rows.append(row)
            out[t.name] = rows
        return out
    finally:
        con.close()


def account_record(user) -> dict:
    """The user's own accounts.db rows: the profile, the consent history and the subscription as
    last applied (SP-328; no session, no Google account id, no Polar id)."""
    from backend.accounts import store
    consents = []
    for c in store.consents(user.id):
        try:
            docs = json.loads(c.get("documents") or "{}")
        except ValueError:
            docs = {}
        row = {"action": c.get("action") or "terms", "terms_version": c["terms_version"],
               "documents": docs, "analysis": int(c["analysis"]), "at": c["accepted_at"]}
        if c.get("client"):                     # a link to another project (SP-329)
            row["client"] = c["client"]
        consents.append(row)
    out = {"email": user.email, "display_name": user.display_name, "role": user.role,
           "created_at": user.created_at, "last_login_at": user.last_login_at, "plan": user.plan,
           "plan_expires_at": user.plan_expires_at, "consents": consents, "subscription": _subscription(user.id)}
    # the e-mail sign-in (SP-610): the address and when it was confirmed — never the password's hash or a link
    em = store.email_account_of(user.id)
    if em:
        out["email_sign_in"] = {"email": em["email"], "confirmed_at": em["verified_at"], "created_at": em["created_at"]}
    # coaching (SP-789): their coach profile, coaches and students by name (the accepts / ends are in
    # consents above, action coach_accept / coach_end) — never an e-mail, an account id or a token
    from backend.accounts import coaching
    out["coaching"] = coaching.export_record(user)
    # SP-765: where the account came from and the 疊石 Cairn card's counts (per day)
    out["signup_source"] = store.signup_source(user.id)
    out["cross_product_events"] = store.xp_events_of(user.id)
    return out


def _subscription(uid: int):
    from backend.accounts import store
    row = store.billing_row(uid)
    if not row:
        return None
    return {k: row.get(k) for k in ("interval", "status", "current_period_end", "cancel_at_period_end", "ends_at",
                                    "past_due_since", "updated_at")}


def _readme() -> bytes:
    from backend.i18n import _
    from backend.i18n.pages import current_brand
    text = _("這是你在 {brand}的資料（格式 {fmt}）。\n\n"
             "account.json：你的帳號（e-mail、名稱、建立時間、方案與訂閱）、同意條款與撤回同意的紀錄、"
             "用 email 登入的確認時間、教練關係，以及註冊來源（signup_source：從疊石 Cairn 的連結來註冊是 cairn、"
             "從賽事分享頁來是 share，其他是空的）和疊石 Cairn 互推小卡每天的次數（cross_product_events："
             "xp-card-shown 小卡出現、xp-card-click 按打開、xp-card-dismiss 按不用了、xp-linked 連結了帳號）。\n"
             "tables/：你輸入或匯入的資料，一個資料表一個 JSON 檔（設定、課表、標註、傷病紀錄、活動清單等）。\n"
             "files/：你的檔案，照伺服器上的位置存放：賽季計畫、校正、圖表檢視、上傳的 GPX（.gz 是 gzip 壓縮），"
             "fit/ 底下是同步或匯入的 FIT 原始檔（gzip 壓縮，解壓後就是 .fit）。\n\n"
             "沒有放進來的：COROS／Garmin／TrainingPeaks 的登入憑證與記住的密碼、課表訂閱網址的金鑰"
             "（機密），以及可以重新計算的快取（衍生資料）。", brand=current_brand(), fmt=FORMAT)
    return text.encode("utf-8")


def prepare(user, tenant, now: Optional[dt.datetime] = None) -> Export:
    """The export of `user` (their tenant): the small entries made now, the files listed."""
    from backend import tenancy
    from backend.engine import backup as B
    now = now or dt.datetime.now(dt.timezone.utc)
    _tables, entries = R.export_entries()
    roots = B.check_roots(B.tenant_roots(tenant.root, tenancy.base_of(tenant).root, tenant.shared))
    tables = read_tables(Path(tenant.root) / R.DB.pattern)
    files = B.tenant_files(roots, entries)
    size = 0
    for p, _rel in files:
        try:
            size += p.stat().st_size
        except OSError:
            pass
    manifest = {"app": APP, "format": FORMAT, "app_version": B.app_version(), "tenant": tenant.id,
                "created_at": now.replace(microsecond=0).isoformat(),
                "classes": [R.USER, R.IMPORTED], "left_out": [R.DERIVED, R.SECRET],
                "tables": {k: len(v) for k, v in tables.items()},
                "files": {"count": len(files), "bytes": size}}
    dump = lambda o: json.dumps(o, ensure_ascii=False, indent=1, default=str).encode("utf-8")   # noqa: E731
    head = [("README.txt", _readme()), ("manifest.json", dump(manifest)),
            ("account.json", dump(account_record(user)))]
    head += [(f"tables/{k}.json", dump(v)) for k, v in sorted(tables.items())]
    return Export(head=head, files=[(p, f"files/{rel}") for p, rel in files], manifest=manifest)


class _Pipe(io.RawIOBase):
    """A write-only, non-seekable buffer the zip writes into; `take` hands over what it holds."""

    def __init__(self):
        super().__init__()
        self.buf = bytearray()
        self.n = 0

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self.buf += b
        self.n += len(b)
        return len(b)

    def tell(self) -> int:
        return self.n

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _info(name: str, when: dt.datetime) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=when.timetuple()[:6])
    zi.external_attr = 0o644 << 16
    return zi


def stream(exp: Export, now: Optional[dt.datetime] = None) -> Iterator[bytes]:
    """The zip, chunk by chunk: one file in memory at a time. A file gone meanwhile (an archive
    run, a deletion) is left out."""
    now = (now or dt.datetime.now()).replace(microsecond=0)
    when = max(now, dt.datetime(1980, 1, 1))
    pipe = _Pipe()
    with zipfile.ZipFile(pipe, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name, data in exp.head:
            zf.writestr(_info(name, when), data)
        yield pipe.take()
        for path, name in exp.files:
            try:
                data = Path(path).read_bytes()
                mtime = dt.datetime.fromtimestamp(max(Path(path).stat().st_mtime, 315532800))
            except OSError:
                continue
            zf.writestr(_info(name, mtime), data,
                        compress_type=zipfile.ZIP_STORED if name.endswith(".gz") else zipfile.ZIP_DEFLATED)
            yield pipe.take()
    yield pipe.take()
