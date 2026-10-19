"""Upload a built trail dictionary to Cloudflare R2 (S3 API) and switch `latest` (SP-709).

    python -I tools/trail-dict/publish.py --out out/

Order, so a failed or partial upload never moves readers off the previous version:

1. upload every file of the build to ``<prefix>v/<version>/`` (tiles and bundle first, manifest last);
2. list that prefix back and compare names and sizes with the build -- anything missing or a
   different size stops here with an error (latest is untouched);
3. only then write ``<prefix>latest.json`` (the pointer readers start from);
4. delete versions older than the newest two (never the one latest points to).

Settings come from the environment (GitHub secrets in the workflow): R2_ACCOUNT_ID,
R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET; optional R2_PREFIX (default "", i.e. the bucket root).
If any of the four is missing it stops with exit code 4 and says which, before touching anything.

Dependency: boto3 (tools/trail-dict/requirements.txt).
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import trail_dict as td  # noqa: E402

REQUIRED_ENV = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
DEFAULT_PREFIX = ""              # a bucket of its own; set R2_PREFIX to share one
UPLOAD_THREADS = 8
# tiles and the bundle never change once written (a new build is a new version folder)
IMMUTABLE = "public, max-age=31536000, immutable"
SHORT = "public, max-age=300"


class PublishError(Exception):
    pass


def missing_env(env=os.environ) -> list[str]:
    return [k for k in REQUIRED_ENV if not (env.get(k) or "").strip()]


def content_type(rel: str) -> str:
    if rel.endswith(".json.gz") or rel.endswith(".ndjson.gz"):
        return "application/gzip"
    if rel.endswith(".json"):
        return "application/json"
    return mimetypes.guess_type(rel)[0] or "application/octet-stream"


def upload_plan(out_dir: str, manifest: dict) -> list[tuple[str, str, int]]:
    """[(relative path, local path, bytes)] in upload order; raises if the build is incomplete."""
    plan, missing = [], []
    for rel in td.expected_files(manifest):
        local = os.path.join(out_dir, *rel.split("/"))
        if not os.path.isfile(local):
            missing.append(rel)
            continue
        plan.append((rel, local, os.path.getsize(local)))
    if missing:
        raise PublishError(f"the build in {out_dir} is incomplete: {len(missing)} files missing "
                           f"(first: {missing[:3]})")
    return plan


def list_sizes(s3, bucket: str, prefix: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            out[o["Key"][len(prefix):]] = o["Size"]
    return out


def list_versions(s3, bucket: str, prefix: str) -> list[str]:
    vs = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            name = cp["Prefix"][len(prefix):].rstrip("/")
            if name:
                vs.append(name)
    return vs


def verify(plan, remote: dict[str, int]) -> list[str]:
    """Problems with an uploaded version (empty = complete)."""
    bad = []
    for rel, _, size in plan:
        got = remote.get(rel)
        if got is None:
            bad.append(f"missing {rel}")
        elif got != size:
            bad.append(f"{rel}: {got} bytes uploaded, {size} built")
    return bad


def delete_prefix(s3, bucket: str, prefix: str) -> int:
    keys = list(list_sizes(s3, bucket, prefix))
    for i in range(0, len(keys), 1000):
        batch = [{"Key": prefix + k} for k in keys[i:i + 1000]]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
    return len(keys)


def publish(s3, bucket: str, prefix: str, out_dir: str, log=print) -> dict:
    with open(os.path.join(out_dir, td.MANIFEST_NAME), encoding="utf-8") as f:
        manifest = json.load(f)
    version = manifest["version"]
    vroot = prefix + td.VERSIONS_DIR
    vprefix = f"{vroot}{version}/"
    plan = upload_plan(out_dir, manifest)
    body, last = plan[:-1], plan[-1]           # manifest last

    def put(item):
        rel, local, _ = item
        s3.upload_file(local, bucket, vprefix + rel,
                       ExtraArgs={"ContentType": content_type(rel),
                                  "CacheControl": SHORT if rel == td.MANIFEST_NAME else IMMUTABLE})

    log(f"uploading {len(plan)} files to {bucket}/{vprefix}")
    with ThreadPoolExecutor(UPLOAD_THREADS) as ex:
        list(ex.map(put, body))
    put(last)

    problems = verify(plan, list_sizes(s3, bucket, vprefix))
    if problems:
        raise PublishError(f"upload of {version} incomplete, latest NOT switched: "
                           f"{len(problems)} problems, e.g. {problems[:5]}")
    log(f"verified {len(plan)} files")

    pointer = td.latest_pointer(manifest, vroot)
    s3.put_object(Bucket=bucket, Key=prefix + td.LATEST_NAME, Body=td.dumps(pointer).encode("utf-8"),
                  ContentType="application/json", CacheControl=SHORT)
    log(f"latest -> {version}")

    old = td.versions_to_delete(list_versions(s3, bucket, vroot), latest=version)
    for v in old:
        n = delete_prefix(s3, bucket, f"{vroot}{v}/")
        log(f"deleted old version {v} ({n} files)")
    return {"version": version, "files": len(plan), "deleted": old}


def make_client(env=os.environ):
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=env["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        # R2 does not take the newer default CRC checksums on every request
        config=Config(retries={"max_attempts": 5, "mode": "standard"},
                      request_checksum_calculation="when_required",
                      response_checksum_validation="when_required"),
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Upload a trail-dict build to R2 and switch latest.")
    ap.add_argument("--out", required=True, help="the folder build.py wrote")
    a = ap.parse_args(argv)
    miss = missing_env()
    if miss:
        print("R2 is not set up: missing " + ", ".join(miss) + ". Add them as repository secrets "
              "(Settings > Secrets and variables > Actions); see tools/trail-dict/README.md. "
              "Nothing was uploaded.", file=sys.stderr)
        return 4
    prefix = os.environ.get("R2_PREFIX", DEFAULT_PREFIX)
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    try:
        publish(make_client(), os.environ["R2_BUCKET"], prefix, a.out)
    except PublishError as e:
        print(str(e), file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
