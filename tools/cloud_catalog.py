#!/usr/bin/env python3
"""The Mac job: build the fingerprint catalog and run the room-noise check, for a Catchrr backend that runs on Linux (Fly.io).

Apple's `shazam` tool and ShazamKit exist only on a Mac, so GitHub Actions runs this file on a macOS runner (see .github/workflows/build-catalog.yml).
It asks the backend which adverts are live, downloads each reference sound, builds ONE .shazamcatalog with `shazam custom-catalog`, listens to
short clips of every advert through simulated room noise (ShazamKit, bin/match.swift), uploads the catalog, then reports the check result.

  CATCHRR_URL        the backend, e.g. https://catchrr-backend.fly.dev
  CATCHRR_JOB_TOKEN  the job's own secret (PLANB_JOB_TOKEN on the backend); on a Mac with the lab server on 127.0.0.1 use CATCHRR_ADMIN_TOKEN instead
  --dry              build and check, but upload nothing (keeps the catalog file in the folder you ran it from)

Only the standard library is used. Needs on the PATH: shazam, swiftc, ffmpeg.
"""
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ROOM = ("aformat=sample_rates=44100:channel_layouts=mono,highpass=f=300,lowpass=f=5000,"
        "aecho=0.8:0.6:23|61:0.35|0.2")                       # the same room as app/fingerprint.py verify()


def say(msg):
    print(msg, flush=True)


def headers():
    h = {"User-Agent": "catchrr-cloud-catalog/1"}
    if os.environ.get("CATCHRR_JOB_TOKEN"):
        h["X-Job-Token"] = os.environ["CATCHRR_JOB_TOKEN"]
    if os.environ.get("CATCHRR_ADMIN_TOKEN"):
        h["X-Admin-Token"] = os.environ["CATCHRR_ADMIN_TOKEN"]
    return h


def call(method, url, data=None, extra=None, timeout=300):
    h = headers()
    h.update(extra or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("%s failed: %s" % (os.path.basename(cmd[0]), (p.stderr or p.stdout).strip()[:400]))
    return p.stdout


def build_catalog(entries, d):
    """entries: [{title, code, wav}] -> path of one catalog file (same steps as app/fingerprint.py build_bytes)."""
    catalog = os.path.join(d, "catalog.shazamcatalog")
    for i, e in enumerate(entries):
        csvp = os.path.join(d, "e%d.csv" % i)
        with open(csvp, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["title", "code"])
            w.writerow([e["title"], int(e["code"])])
        if i == 0:
            run(["shazam", "custom-catalog", "create", "-s", e["wav"], "-m", csvp, "-o", catalog])
        else:
            nxt = os.path.join(d, "next.shazamcatalog")
            run(["shazam", "custom-catalog", "update", "-i", catalog, "-s", e["wav"], "-m", csvp, "-o", nxt])
            shutil.move(nxt, catalog)
    return catalog


def room_check(entries, catalog, d):
    """Listen to 3 short clips of every advert (6 s or longer) through room noise against the catalog, the way the phone would."""
    tool = os.path.join(d, "shazam-match")
    run(["swiftc", "-parse-as-library", "-O", os.path.join(ROOT, "bin", "match.swift"), "-o", tool])
    clips, meta = [], {}
    for e in entries:
        dur = float(e["seconds"] or 0)
        if dur < 6:
            continue
        for k, frac in enumerate((0.2, 0.5, 0.8)):
            start = max(0.0, min(dur - 5.5, dur * frac - 2.5))
            out = os.path.join(d, "c%s_%d.wav" % (e["advert_id"], k))
            try:
                run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-ss", "%.2f" % start, "-t", "5", "-i", e["wav"],
                     "-filter_complex", "[0:a]%s[a];anoisesrc=d=5:c=pink:r=44100:a=0.02[n];[a][n]amix=inputs=2:duration=first:normalize=0[o]" % ROOM,
                     "-map", "[o]", out])
                clips.append(out)
                meta[os.path.basename(out)] = (e["advert_id"], e["code"])
            except RuntimeError:
                pass
    tally = {e["advert_id"]: {"own": 0, "other": 0, "none": 0, "n": 0} for e in entries}
    if clips:
        res = subprocess.run([tool, catalog] + clips, capture_output=True, text=True).stdout
        for line in res.splitlines():
            parts = line.split("\t")
            if len(parts) < 2 or parts[1] not in meta:
                continue
            aid, code = meta[parts[1]]
            t = tally[aid]
            t["n"] += 1
            if parts[0] == "MATCH" and len(parts) > 2:
                t["own" if re.search(r"#%s$" % code, parts[2]) else "other"] += 1
            else:
                t["none"] += 1
    out = []
    for aid, t in tally.items():
        if t["n"]:
            out.append({"advert_id": aid, "check_ok": t["own"] == t["n"] and t["other"] == 0,
                        "check_text": "%d/%d recognised in room noise, %d mix-ups" % (t["own"], t["n"], t["other"])})
    return out


def multipart(fields, fname, payload):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode())
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n' % (b, fname)).encode())
    parts.append(payload)
    parts.append(("\r\n--%s--\r\n" % b).encode())
    return b"".join(parts), "multipart/form-data; boundary=" + b


def main():
    dry = "--dry" in sys.argv
    base = (os.environ.get("CATCHRR_URL") or "").rstrip("/")
    if not base:
        sys.exit("Set CATCHRR_URL (for example https://catchrr-backend.fly.dev).")
    for tool in ("shazam", "swiftc", "ffmpeg"):
        if not shutil.which(tool):
            sys.exit("This job needs %s on the PATH (it runs on a Mac)." % tool)
    sources = json.loads(call("GET", base + "/api/admin/catalog/sources"))["adverts"]
    say("%d live adverts" % len(sources))
    if not sources:
        sys.exit("No live adverts: nothing to build (the current catalog stays).")
    with tempfile.TemporaryDirectory() as d:
        entries = []
        for s in sources:
            wav = os.path.join(d, "ref%s.wav" % s["advert_id"])
            with open(wav, "wb") as f:
                f.write(call("GET", "%s/api/admin/catalog/source/%s" % (base, s["advert_id"])))
            entries.append(dict(s, wav=wav))
        catalog = build_catalog(entries, d)
        raw = open(catalog, "rb").read()
        say("catalog built: %d bytes, sha256 %s" % (len(raw), hashlib.sha256(raw).hexdigest()[:16]))
        checks = room_check(entries, catalog, d)
        bad = [c for c in checks if not c["check_ok"]]
        say("room check: %d of %d adverts clean" % (len(checks) - len(bad), len(checks)))
        for c in bad:
            say("  advert %s: %s" % (c["advert_id"], c["check_text"]))
        if dry:
            keep = os.path.abspath("cloud-catalog-dry.shazamcatalog")      # next to where the command was run, never inside the server's data folder
            shutil.copy(catalog, keep)
            say("--dry: nothing uploaded. Catalog kept at %s" % keep)
            return
        body, ctype = multipart({"adverts": len(entries)}, "catalog.shazamcatalog", raw)
        say("uploaded: " + call("POST", base + "/api/admin/catalog/upload", body, {"Content-Type": ctype}).decode())
        say("checks saved: " + call("POST", base + "/api/admin/catalog/checks", json.dumps({"results": checks}).encode(),
                                    {"Content-Type": "application/json"}).decode())


if __name__ == "__main__":
    main()
