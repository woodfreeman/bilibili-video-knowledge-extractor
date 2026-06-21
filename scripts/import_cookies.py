"""Validate a Bilibili cookies.txt file (Netscape format).

Required fields for full B 站 functionality:
  - DedeUserID        (your numeric UID)
  - DedeUserID__ckMd5 (UID hash, often auto-refreshed)
  - SESSDATA          (login session token)
  - bili_jct          (CSRF token, required for write actions like comments)

Usage:
  python scripts/import_cookies.py <path-to-cookies.txt>
  python scripts/import_cookies.py <path> --check    # also call B 站 API to verify

Exit codes:
  0  cookies file is valid (and --check passed if requested)
  1  cookies file missing or unreadable
  2  required fields missing
  3  --check failed (cookies rejected by B 站 API)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib import error, request


# P0 patch 2026-06-21: subset of cookie names this Skill requires.
# DedeUserID/DedeUserID__ckMd5 are B 站 user identity, SESSDATA is the
# session token, bili_jct is CSRF for write actions.
REQUIRED_FIELDS = (
    "DedeUserID",
    "DedeUserID__ckMd5",
    "SESSDATA",
    "bili_jct",
)

# Some B 站 features only need a subset, e.g. 1080P playback needs login
# but not bili_jct.  We require all 4 here so the user gets a single
# "export everything" instruction.
CHECK_URL = "https://api.bilibili.com/x/web-interface/nav"
CHECK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def parse_netscape_cookies(path: Path) -> dict[str, str]:
    """Parse a cookies file, auto-detecting Netscape vs HTTP-Cookie-Header format.

    Netscape format (tab-separated, 7 columns):
      domain  flag  path  secure  expiration  name  value

    HTTP Cookie header format (single line, ';' separated):
      name1=value1;name2=value2;name3=value3

    Both come out of the box from different browser export extensions:
      - "Get cookies.txt LOCALLY" → Netscape
      - Manual copy from DevTools 'Cookie:' request header → HTTP header

    Auto-detected by scanning the first non-comment line for tabs.
    """
    if not path.exists():
        raise FileNotFoundError(f"Cookies file not found: {path}")
    raw_text = path.read_text(encoding="utf-8", errors="replace")
    cookies: dict[str, str] = {}
    for lineno, raw in enumerate(raw_text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if "\t" in line:
            # Netscape format
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[-2].strip()
            value = parts[-1].strip()
        else:
            # HTTP Cookie header format (single line, ';' separated)
            for pair in line.split(";"):
                pair = pair.strip()
                if "=" not in pair:
                    continue
                name, _, value = pair.partition("=")
                name = name.strip()
                value = value.strip()
                if not name:
                    continue
                cookies[name] = value
            continue
        if not name:
            continue
        cookies[name] = value
    return cookies


def check_bilibili_cookies(cookies: dict[str, str]) -> tuple[bool, str]:
    """Hit B 站 /nav API with the cookies and report the result.

    Returns (ok, message).  ok=True when B 站 reports the user is logged in
    (code=0, isLogin=true).
    """
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = request.Request(
        CHECK_URL,
        headers={
            "User-Agent": CHECK_UA,
            "Referer": "https://www.bilibili.com/",
            "Cookie": cookie_header,
        },
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as e:
        return False, f"HTTP {e.code} (cookies may be expired)"
    except Exception as e:
        return False, f"Network error: {e}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False, "B 站 returned non-JSON response (likely blocked)"
    code = data.get("code")
    if code == 0 and data.get("data", {}).get("isLogin"):
        uname = data.get("data", {}).get("uname", "?")
        return True, f"Logged in as {uname}"
    return False, f"B 站 rejected cookies (code={code}, message={data.get('message')})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cookies_path", help="Path to Netscape-format cookies.txt")
    parser.add_argument("--check", action="store_true",
                        help="Also call B 站 /nav API to verify cookies work live.")
    parser.add_argument("--show", action="store_true",
                        help="Print all parsed cookie names (for debugging).")
    args = parser.parse_args()

    path = Path(args.cookies_path).expanduser()
    print(f"== Reading cookies from: {path}")
    try:
        cookies = parse_netscape_cookies(path)
    except FileNotFoundError as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return 1

    print(f"  parsed {len(cookies)} cookies")
    if args.show:
        for name in sorted(cookies.keys()):
            print(f"    {name}")

    missing = [f for f in REQUIRED_FIELDS if f not in cookies]
    if missing:
        print(f"  ERROR: missing required fields: {', '.join(missing)}", file=sys.stderr)
        print("  Tip: re-export cookies using 'Get cookies.txt LOCALLY' extension", file=sys.stderr)
        print("       or bilibili.com (NOT bangumi/play/) with all cookies selected.", file=sys.stderr)
        return 2

    print(f"  ✓ all {len(REQUIRED_FIELDS)} required fields present: {', '.join(REQUIRED_FIELDS)}")

    if args.check:
        print(f"== Checking cookies live against B 站 /nav API...")
        ok, msg = check_bilibili_cookies(cookies)
        if ok:
            print(f"  ✓ {msg}")
            return 0
        print(f"  ✗ {msg}", file=sys.stderr)
        return 3

    print("  Pass --check to verify against B 站 live API.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
