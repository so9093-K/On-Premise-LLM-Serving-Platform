#!/usr/bin/env python3
"""문서 화면의 JS 번들을 저장소에 내려받고 고정된 SRI 해시로 검증한다.

온프레미스 배포는 외부 egress가 없어도 /docs와 /redoc이 떠야 하므로 번들을 vendoring 한다.
버전과 해시는 ai_model_serving.docs_ui가 단독으로 소유하고, 이 스크립트는 그 값을
읽어 쓴다 -- 여기서 따로 적으면 문서 HTML과 조용히 어긋난다.

  python scripts/build/fetch_docs_assets.py            # 없거나 해시가 다르면 받는다
  python scripts/build/fetch_docs_assets.py --check    # 네트워크 없이 vendoring 파일만 검증
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ai_model_serving.docs_ui import VENDORED_ASSETS, VendoredAsset  # noqa: E402


def subresource_integrity(payload: bytes, declared: str) -> str:
    algorithm, _, _ = declared.partition("-")
    digest = hashlib.new(algorithm, payload).digest()
    return f"{algorithm}-{base64.b64encode(digest).decode('ascii')}"


def check_one(asset: VendoredAsset) -> bool:
    if not asset.path.is_file():
        print(f"missing vendored docs bundle: {asset.path}", file=sys.stderr)
        return False
    actual = subresource_integrity(asset.path.read_bytes(), asset.integrity)
    if actual != asset.integrity:
        print(
            f"{asset.filename} integrity mismatch\n"
            f"  declared: {asset.integrity}\n"
            f"  actual:   {actual}",
            file=sys.stderr,
        )
        return False
    print(f"docs bundle verified: {asset.filename}")
    return True


def check() -> int:
    return 0 if all([check_one(asset) for asset in VENDORED_ASSETS]) else 1


def fetch_one(asset: VendoredAsset) -> bool:
    if asset.path.is_file() and check_one(asset):
        return True
    print(f"downloading {asset.source_url}")
    with urllib.request.urlopen(asset.source_url, timeout=60) as response:
        payload = response.read()
    actual = subresource_integrity(payload, asset.integrity)
    if actual != asset.integrity:
        print(
            f"refusing to vendor {asset.filename}: it does not match the pinned hash\n"
            f"  declared: {asset.integrity}\n"
            f"  actual:   {actual}",
            file=sys.stderr,
        )
        return False
    asset.path.parent.mkdir(parents=True, exist_ok=True)
    asset.path.write_bytes(payload)
    print(f"wrote {asset.path.relative_to(ROOT)} ({len(payload)} bytes)")
    return True


def fetch() -> int:
    return 0 if all([fetch_one(asset) for asset in VENDORED_ASSETS]) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="검증만 하고 내려받지 않는다")
    raise SystemExit(check() if parser.parse_args().check else fetch())
