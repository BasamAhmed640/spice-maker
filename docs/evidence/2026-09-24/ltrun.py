"""Run one LTspice deck in batch mode with a minimal environment and record evidence.

usage: python ltrun.py <deck.cir> <timeout_s> <out.json> [--keep-log]
Writes <deck>.log.txt (UTF-8) next to the deck, deletes <deck>.raw/.op.raw,
and writes a JSON summary (never includes environment values).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

LTSPICE = r"C:\Users\basam\AppData\Local\Programs\ADI\LTspice\LTspice.exe"
ENV_KEYS = ("SystemRoot", "SYSTEMDRIVE", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA")


def minimal_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in ENV_KEYS if k in os.environ}
    env["PATH"] = r"C:\Windows\System32"
    return env


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def decode_log(b: bytes) -> tuple[str, str]:
    """Decode an LTspice log that may be UTF-16LE, UTF-8, or a mix of both."""
    had_bom = b.startswith(b"\xff\xfe")
    if had_bom:
        b = b[2:]
    out: list[str] = []
    kinds: set[str] = set()
    i, n = 0, len(b)
    while i < n:
        j = i
        while j + 1 < n and b[j + 1] == 0 and b[j] != 0:
            j += 2
        if j - i >= 4:  # >= 2 UTF-16LE code units
            out.append(b[i:j].decode("utf-16le", "replace"))
            kinds.add("utf-16le")
            i = j
            continue
        k = i
        while k < n and not (k + 3 < n and b[k] != 0 and b[k + 1] == 0 and b[k + 2] != 0 and b[k + 3] == 0):
            k += 1
        if k == i:
            k = i + 1
        chunk = b[i:k].replace(b"\x00", b"")
        if chunk:
            out.append(chunk.decode("utf-8", "replace"))
            kinds.add("utf-8")
        i = k
    label = "mixed(utf-16le+utf-8)" if len(kinds) > 1 else (kinds.pop() if kinds else "empty")
    if had_bom:
        label += " with UTF-16LE BOM"
    text = "".join(out).replace("\r\n", "\n").replace("\r", "\n")
    return text, label


def main() -> None:
    deck = Path(sys.argv[1]).resolve()
    timeout = float(sys.argv[2])
    out_json = Path(sys.argv[3])
    keep_log = "--keep-log" in sys.argv
    log = deck.with_suffix(".log")
    for stale in (log, deck.with_suffix(".raw"), deck.with_suffix(".op.raw")):
        if stale.exists():
            stale.unlink()
    cmd = [LTSPICE, "-b", str(deck)]
    t0 = time.perf_counter()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=str(deck.parent), env=minimal_env(), timeout=timeout,
                              capture_output=True)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out, rc = True, None
    elapsed = time.perf_counter() - t0
    res: dict = {
        "command": f'"{LTSPICE}" -b "{deck}"',
        "cwd": str(deck.parent),
        "env_keys_passed": sorted([*minimal_env().keys()]),
        "timeout_s": timeout,
        "timed_out": timed_out,
        "returncode": rc,
        "elapsed_s": round(elapsed, 3),
        "deck": str(deck),
        "deck_sha256": sha256(deck),
    }
    if log.exists():
        raw = log.read_bytes()
        text, enc = decode_log(raw)
        txt = deck.with_name(deck.stem + ".log.txt")
        txt.write_text(text, encoding="utf-8", newline="\n")
        res.update(log_original_encoding=enc, log_original_sha256=hashlib.sha256(raw).hexdigest(),
                   log_txt=str(txt), log_txt_sha256=sha256(txt), log_text=text)
        if not keep_log:
            log.unlink()
    else:
        res["log_missing"] = True
    removed = []
    for r in (deck.with_suffix(".raw"), deck.with_suffix(".op.raw")):
        if r.exists():
            res.setdefault("raw_bytes_deleted", {})[r.name] = r.stat().st_size
            r.unlink()
            removed.append(r.name)
    res["raw_files_deleted"] = removed
    out_json.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "log_text"}, indent=2))
    print(res.get("log_text", ""))


if __name__ == "__main__":
    main()
