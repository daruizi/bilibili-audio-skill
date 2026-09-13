# -*- coding: utf-8 -*-
"""
Local helper for the bilibili-audio skill's BROWSER FALLBACK method.

WHY THIS EXISTS
---------------
On some machines yt-dlp cannot download Bilibili audio: the playurl API returns
HTTP 412 (anti-crawler risk control) and yt-dlp cannot read browser cookies
because Chrome/Edge use App-Bound Encryption ("Failed to decrypt with DPAPI").

The reliable workaround: read the audio CDN direct URL from the *live, logged-in*
browser page (window.__playinfo__ / playurl), then have THIS local server
download that URL server-side. The CDN authorizes a download by a signed,
time-limited URL token + a `Referer: https://www.bilibili.com/` header -- NOT by
login cookies -- so a plain server-side fetch works. Then ffmpeg -c copy remuxes
the fragmented-MP4 stream into a clean, lossless .m4a/.flac.

TRANSFER CHANNEL (important)
----------------------------
The bilibili page CANNOT fetch()/POST to http://127.0.0.1 (blocked by the page's
CSP connect-src and/or Chrome Private Network Access). But a TOP-LEVEL NAVIGATION
to localhost is NOT blocked. So the page hands us the URL by navigating:
    window.location.href = 'http://127.0.0.1:<PORT>/save?k=<NONCE>&t=<title>&u=<url>'
(same-tab navigation needs no user gesture and is not popup-blocked).

USAGE
-----
    python bili_browser_fetch.py [--out-dir DIR] [--port 8799]

On startup it prints, to stderr, the actual PORT (auto-incremented if busy) and a
random NONCE. Build the /save URL with BOTH. Poll GET /status?k=<NONCE> for
per-job JSON progress. The server auto-exits when all jobs finish (short grace) or
after an idle timeout, and also on GET /shutdown?k=<NONCE>.
"""
import argparse
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ----------------------------- filename sanitizing ---------------------------
# Prefer yt-dlp's own sanitizer so names byte-match files the user already has
# from yt-dlp (e.g. / -> U+29F8, \ -> U+29F9, : -> ：, | -> ｜ ...).
try:
    from yt_dlp.utils import sanitize_filename as _ytsan
except Exception:
    _ytsan = None

_FALLBACK = {'\\': '＼', '/': '／', ':': '：', '*': '＊', '?': '？',
             '"': '＂', '<': '＜', '>': '＞', '|': '｜'}


def sanitize(title, max_base=120):
    title = (title or "bilibili_audio")
    title = re.sub(r'[\r\n\t]+', ' ', title)
    title = "".join(c for c in title if ord(c) >= 32)          # drop C0 controls
    if _ytsan is not None:
        try:
            title = _ytsan(title, restricted=False)
        except Exception:
            title = "".join(_FALLBACK.get(c, c) for c in title)
    else:
        title = "".join(_FALLBACK.get(c, c) for c in title)
    title = re.sub(r'\s{2,}', ' ', title).strip().rstrip(' .')  # no trailing . / space
    if not title:
        title = "bilibili_audio"
    if len(title) > max_base:
        title = title[:max_base].rstrip(' .')
    return title


def fit_path(directory, base, ext):
    """Truncate the base so the full path stays under Windows MAX_PATH (~255)."""
    budget = 250 - len(os.path.join(directory, "")) - len(ext) - 6  # room for ' (12)'
    if budget > 8 and len(base) > budget:
        base = base[:budget].rstrip(' .')
    p = os.path.join(directory, base + ext)
    i = 2
    while os.path.exists(p) and os.path.getsize(p) > 0:
        p = os.path.join(directory, "%s (%d)%s" % (base, i, ext))
        i += 1
    return p


def have(cmd):
    return shutil.which(cmd) is not None


# --------------------------------- job state ---------------------------------
JOBS = []
JOBS_LOCK = threading.Lock()
QUEUE = []
QUEUE_COND = threading.Condition()
LAST_ACTIVITY = [time.time()]


def touch():
    LAST_ACTIVITY[0] = time.time()


def add_job(url, title, ext, dur, artist=""):
    with JOBS_LOCK:
        job = {"id": len(JOBS) + 1, "title": title, "ext": ext, "expect_dur": dur,
               "artist": artist,
               "phase": "queued", "received": 0, "total": 0,
               "final_path": None, "duration": None, "error": None, "message": "",
               "_url": url}
        JOBS.append(job)
    with QUEUE_COND:
        QUEUE.append(job)
        QUEUE_COND.notify()
    return job


def _download(url, tmp_path, job, out_dir):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # some mcdn/PCDN nodes have quirky certs
    headers = {"Referer": "https://www.bilibili.com/", "User-Agent": UA,
               "Origin": "https://www.bilibili.com", "Accept": "*/*"}
    attempts = 0
    pos = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
    while True:
        attempts += 1
        h = dict(headers)
        if pos:
            h["Range"] = "bytes=%d-" % pos
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=90, context=ctx) as resp:
                cr = resp.headers.get("Content-Range")
                clen = int(resp.headers.get("Content-Length", 0) or 0)
                if cr and "/" in cr:
                    job["total"] = int(cr.split("/")[-1])
                elif pos == 0 and clen:
                    job["total"] = clen
                # disk-space guard (best effort)
                if job["total"]:
                    try:
                        free = shutil.disk_usage(out_dir).free
                        if free < job["total"] * 2 + (50 << 20):
                            raise OSError("not enough free disk space for %d bytes" % job["total"])
                    except OSError:
                        raise
                mode = "ab" if pos else "wb"
                with open(tmp_path, mode) as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        pos += len(chunk)
                        job["received"] = pos
            if job["total"] and pos < job["total"] and attempts < 6:
                time.sleep(1)
                continue                      # short read -> resume via Range
            return
        except Exception as e:
            # 403/410 usually = token expired; caller must re-mint via the browser
            msg = str(e)
            if ("403" in msg or "410" in msg):
                raise RuntimeError("CDN token expired/forbidden (%s) -- re-extract a "
                                   "fresh URL from the page and call /save again" % msg)
            if attempts >= 6:
                raise
            job["message"] = "retry %d after: %s" % (attempts, e)
            time.sleep(2)
            pos = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0


def _probe_duration(path):
    if not have("ffprobe"):
        return None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return float(r.stdout.strip())
    except Exception:
        return None


def process(job, out_dir):
    url = job.pop("_url")
    try:
        base = sanitize(job["title"])
        ext = job.get("ext") or "m4a"
        tmp_path = os.path.join(out_dir, "._biliaudio_tmp_%d" % job["id"])
        job["phase"] = "downloading"
        _download(url, tmp_path, job, out_dir)
        final = fit_path(out_dir, base, "." + ext.lstrip("."))
        if have("ffmpeg"):
            job["phase"] = "remuxing"
            cmd = ["ffmpeg", "-y", "-i", tmp_path, "-map", "0:a:0", "-c", "copy"]
            # -movflags is a mov/mp4-family muxer option; for other outputs
            # (e.g. .flac) it is silently ignored (verified rc=0), but only add
            # it where it actually applies to keep the command honest
            if ext.lstrip(".").lower() in ("m4a", "mp4", "m4b", "mov"):
                cmd += ["-movflags", "+faststart"]
            cmd += ["-metadata", "title=" + job["title"]]
            if job.get("artist"):
                cmd += ["-metadata", "artist=" + job["artist"]]
            cmd.append(final)
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                shutil.move(tmp_path, final)
                job["message"] = "ffmpeg remux failed (%s); saved raw stream" % (r.stderr or "")[-200:]
            else:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        else:
            shutil.move(tmp_path, final)
            job["message"] = "ffmpeg not found; saved raw stream as .%s (still valid audio)" % ext
        job["final_path"] = final
        job["duration"] = _probe_duration(final)
        # verify duration vs expectation (catches truncated 试看/preview clips)
        if job["expect_dur"] and job["duration"] and job["duration"] < job["expect_dur"] * 0.9:
            job["message"] = ((job["message"] + " | ") if job["message"] else "") + \
                ("WARNING: audio %.0fs is much shorter than expected %.0fs -- likely a "
                 "preview/试看 clip or wrong part" % (job["duration"], job["expect_dur"]))
        job["phase"] = "done"
        sys.stderr.write("DONE job %d -> %s (%.1f MB, dur=%s)\n"
                         % (job["id"], final, job["received"] / 1048576.0, job["duration"]))
        sys.stderr.flush()
    except Exception as e:
        job["phase"] = "error"
        job["error"] = str(e)
        sys.stderr.write("ERROR job %d: %s\n" % (job["id"], e))
        sys.stderr.flush()


def worker(out_dir):
    while True:
        with QUEUE_COND:
            while not QUEUE:
                QUEUE_COND.wait()
            job = QUEUE.pop(0)
        process(job, out_dir)


def watchdog(httpd, idle_no_job=300, idle_after_done=45):
    """Auto-exit: if no job ever arrives within idle_no_job, or all jobs finished
    and the server has been idle for idle_after_done seconds."""
    while True:
        time.sleep(5)
        idle = time.time() - LAST_ACTIVITY[0]
        with JOBS_LOCK:
            jobs = list(JOBS)
        if not jobs:
            if idle > idle_no_job:
                sys.stderr.write("EXIT: idle, no jobs\n"); sys.stderr.flush()
                threading.Thread(target=httpd.shutdown, daemon=True).start()
                return
            continue
        active = any(j["phase"] in ("queued", "downloading", "remuxing") for j in jobs)
        if not active and idle > idle_after_done:
            sys.stderr.write("EXIT: all jobs done, idle\n"); sys.stderr.flush()
            threading.Thread(target=httpd.shutdown, daemon=True).start()
            return


class Handler(http.server.BaseHTTPRequestHandler):
    NONCE = None

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _auth(self, q):
        return q.get("k", [""])[0] == self.NONCE

    def do_GET(self):
        touch()
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/ping":
            self._send(200, b"ok"); return
        if u.path in ("/save", "/status", "/shutdown") and not self._auth(q):
            self._send(403, b"bad nonce"); return
        if u.path == "/save":
            url = q.get("u", [""])[0]
            title = q.get("t", [""])[0] or "bilibili_audio"
            artist = q.get("a", [""])[0]
            ext = q.get("ext", ["m4a"])[0]
            try:
                dur = float(q.get("dur", ["0"])[0])
            except ValueError:
                dur = 0
            if not url:
                self._send(400, b"missing u"); return
            job = add_job(url, title, ext, dur, artist)
            sys.stderr.write("SAVE job %d ext=%s title=%r host=%s\n"
                             % (job["id"], ext, title[:50],
                                urllib.parse.urlparse(url).hostname))
            sys.stderr.flush()
            html = ("<!doctype html><meta charset='utf-8'>"
                    "<body style='font:18px sans-serif;padding:40px'>"
                    "下载已在本地服务器开始（job %d）。可以关闭此标签页。<br>"
                    "Download started on local server (job %d). You can close this tab."
                    "</body>" % (job["id"], job["id"])).encode("utf-8")
            self._send(200, html, "text/html; charset=utf-8")
        elif u.path == "/status":
            with JOBS_LOCK:
                clean = [{k: v for k, v in j.items() if k != "_url"} for j in JOBS]
            self._send(200, json.dumps({"jobs": clean}, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        elif u.path == "/shutdown":
            self._send(200, b"bye")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(200, b"ok")

    def log_message(self, *a):
        pass


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def pick_port(start):
    for p in range(start, start + 30):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            s.close()
    raise SystemExit("no free port near %d" % start)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(os.path.expanduser("~"), "Downloads"))
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--info-file", default=None,
                    help="write {port,nonce,out_dir,pid} JSON here once listening; "
                         "use this instead of parsing stderr when spawned hidden "
                         "(Start-Process -WindowStyle Hidden gives no stderr)")
    args = ap.parse_args()
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)
    port = pick_port(args.port)
    Handler.NONCE = secrets.token_urlsafe(12)
    threading.Thread(target=worker, args=(out_dir,), daemon=True).start()
    httpd = ThreadedServer(("127.0.0.1", port), Handler)
    threading.Thread(target=watchdog, args=(httpd,), daemon=True).start()
    if args.info_file:
        tmp = args.info_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"port": port, "nonce": Handler.NONCE,
                       "out_dir": out_dir, "pid": os.getpid()}, f)
        os.replace(tmp, args.info_file)   # atomic: poller never sees a half-written file
    # The two lines the orchestrator parses:
    sys.stderr.write("PORT=%d\nNONCE=%s\n" % (port, Handler.NONCE))
    sys.stderr.write("LISTENING on 127.0.0.1:%d  out_dir=%s  ffmpeg=%s  ytdlp_sanitizer=%s\n"
                     % (port, out_dir, have("ffmpeg"), _ytsan is not None))
    sys.stderr.flush()
    httpd.serve_forever()
    sys.stderr.write("SERVER STOPPED\n"); sys.stderr.flush()


if __name__ == "__main__":
    main()
