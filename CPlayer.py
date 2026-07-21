#!/usr/bin/env python3

import re, json, random, requests
from urllib.parse import urlparse, quote
from flask import Flask, Response, jsonify, request

API = "https://api.chksz.top/api"

app = Flask(__name__)

CHROME_VERSIONS = [
    "128.0.6613.137", "128.0.6613.138", "129.0.6668.58", "129.0.6668.59",
    "129.0.6668.70", "130.0.6723.58", "130.0.6723.59", "130.0.6723.69",
    "131.0.6778.85", "131.0.6778.108", "132.0.6834.83", "132.0.6834.110",
]

PLATFORMS = [
    "Windows NT 10.0; Win64; x64",
    "Windows NT 10.0; Win64; x64",
    "Windows NT 10.0; Win64; x64",
    "Macintosh; Intel Mac OS X 14_6_1",
    "Macintosh; Intel Mac OS X 14_5",
    "X11; Linux x86_64",
]


def random_ua():
    chrome = random.choice(CHROME_VERSIONS)
    plat = random.choice(PLATFORMS)
    return (
        f"Mozilla/5.0 ({plat}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chrome} Safari/537.36"
    )


def headers_for(url, extra=None):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    origin = f"{parsed.scheme}://{parsed.hostname}"
    chrome_ver = random.choice(CHROME_VERSIONS)
    h = {
        "User-Agent": random_ua(),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Host": host,
        "Origin": origin,
        "Referer": origin + "/",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": f'"Chromium";v="{chrome_ver.split(".")[0]}", "Not/A)Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": random.choice(['"Windows"', '"macOS"', '"Linux"']),
    }
    if extra:
        h.update(extra)
    for bad in [
        "X-Forwarded-For", "X-Real-IP", "X-Forwarded-Proto",
        "X-Forwarded-Host", "Via", "Forwarded",
        "x-forwarded-for", "x-real-ip", "x-forwarded-proto",
        "x-forwarded-host", "via", "forwarded",
    ]:
        h.pop(bad, None)
    return h


SESSION = requests.Session()


def try_https(url):
    if not url or not url.startswith("http://"):
        return url
    https = url.replace("http://", "https://", 1)
    try:
        r = SESSION.head(https, headers=headers_for(https), timeout=4, allow_redirects=True)
        if r.status_code < 500:
            return https
    except Exception:
        pass
    return url


def api_get(path, params=None, timeout=12):
    h = headers_for(API)
    h["Accept"] = "application/json"
    return SESSION.get(f"{API}{path}", params=params, headers=h, timeout=timeout)


def get_audio_url(sid):
    try:
        r = api_get("/163_music", {"id": sid, "level": "jymaster"})
        body = r.json()
        if body.get("code") != 200:
            return None
        raw = body["data"]
        if isinstance(raw, list):
            raw = raw[0] or {}
        return raw.get("url", "")
    except Exception:
        return None


def get_song_meta(sid):
    try:
        r = api_get("/163_music", {"id": sid, "level": "jymaster"})
        body = r.json()
        if body.get("code") != 200:
            return {}
        raw = body["data"]
        if isinstance(raw, list):
            raw = raw[0] or {}
        return raw
    except Exception:
        return {}


def stream_audio(audio_url):
    parsed = urlparse(audio_url)
    ext = (parsed.path.rsplit(".", 1)[-1] if "." in parsed.path else "flac")
    mime_map = {
        "flac": "audio/flac", "mp3": "audio/mpeg",
        "m4a": "audio/mp4", "aac": "audio/aac",
        "ogg": "audio/ogg", "wav": "audio/wav", "wma": "audio/x-ms-wma",
    }
    content_type = mime_map.get(ext.lower(), "audio/flac")

    def generate():
        try:
            resp = SESSION.get(
                audio_url, headers=headers_for(audio_url, {"Accept": "*/*"}),
                stream=True, timeout=30,
            )
            resp.raise_for_status()
            for chunk in resp.iter_content(256 * 1024):
                if chunk:
                    yield chunk
        except Exception:
            return
    return generate(), content_type, ext


def proxy_fetch(url):
    try:
        r = SESSION.get(url, headers=headers_for(url), timeout=12)
        if r.status_code == 200:
            ct = r.headers.get("content-type", "application/octet-stream")
            return r.content, ct
    except Exception:
        pass
    return None, None


def make_download_headers(filename, fallback="download"):
    ascii_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    if len(ascii_name) > 80:
        ascii_name = fallback
    encoded = quote(filename.encode('utf-8'), safe='')
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{encoded}"
        ),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-cache",
    }


def make_lyric_text(raw, name, artist):
    title_line = f"[ti:{name}]\n" if name else ""
    artist_line = f"[ar:{artist}]\n" if artist else ""
    lrc = raw.get("lrc", "")
    tlrc = raw.get("tlyric", "")
    parts = [title_line, artist_line]
    if lrc:
        parts.append(lrc.strip())
    if tlrc:
        parts.append("\n\n[翻译歌词]\n" + tlrc.strip())
    return "\n".join(p for p in parts if p) + "\n"


@app.route("/api/search")
def search():
    kw = request.args.get("q", "").strip()
    if not kw:
        return jsonify({"ok": False, "data": []})
    try:
        r = api_get("/163_search", {"keyword": kw, "limit": 30})
        body = r.json()
        if body.get("code") != 200:
            return jsonify({"ok": False, "data": []})
        raw = body.get("data", {})
        if isinstance(raw, dict):
            raw = raw.get("songs", [])
        songs = []
        for s in raw:
            artists = s.get("artists", s.get("artist", ""))
            if isinstance(artists, list):
                artists = " / ".join(
                    a["name"] if isinstance(a, dict) else str(a)
                    for a in artists
                )
            album = s.get("album", "")
            if isinstance(album, dict):
                album = album.get("name", "")
            cover = s.get("picUrl", "")
            if not cover and isinstance(s.get("album"), dict):
                cover = s["album"].get("picUrl", "")
            songs.append({
                "id": s["id"],
                "name": s.get("name", ""),
                "artist": artists,
                "album": album,
                "cover": try_https(cover),
                "duration": s.get("duration", 0),
            })
        return jsonify({"ok": True, "data": songs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/song/<sid>")
def song(sid):
    try:
        raw = get_song_meta(sid)
        if not raw:
            return jsonify({"ok": False}), 404
        return jsonify({"ok": True, "data": {
            "id": raw.get("id"),
            "url": raw.get("url", ""),
            "br": raw.get("br", 0),
            "size": raw.get("size", 0),
            "level": raw.get("level", ""),
            "md5": raw.get("md5", ""),
            "name": raw.get("name", ""),
            "artist": raw.get("artist", ""),
            "cover": try_https(raw.get("picUrl", "")),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/lyric/<sid>")
def lyric(sid):
    try:
        r = api_get("/163_lyric", {"id": sid}, timeout=8)
        body = r.json()
        d = (body.get("data") or {}) if body.get("code") == 200 else {}
        return jsonify({"ok": True, "data": {
            "lrc": d.get("lrc", ""),
            "tlrc": d.get("tlyric", ""),
        }})
    except Exception:
        return jsonify({"ok": True, "data": {"lrc": "", "tlrc": ""}})


@app.route("/api/playlist/<pid>")
def playlist(pid):
    try:
        r = api_get("/163_playlist", {"id": pid})
        body = r.json()
        tracks = []
        if body.get("data") and isinstance(body["data"], dict):
            tracks = body["data"].get("tracks", [])
        elif body.get("data"):
            tracks = body["data"]
        songs = []
        for t in tracks:
            artists = t.get("artists", t.get("artist", ""))
            if isinstance(artists, list):
                artists = " / ".join(
                    a["name"] if isinstance(a, dict) else str(a)
                    for a in artists
                )
            album = t.get("album", "")
            if isinstance(album, dict):
                album = album.get("name", "")
            songs.append({
                "id": t["id"],
                "name": t.get("name", ""),
                "artist": artists,
                "album": album,
                "cover": try_https(t.get("picUrl", "")),
            })
        return jsonify({"ok": True, "data": songs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cover-proxy")
def cover_proxy():
    url = request.args.get("url", "")
    if not url:
        return "", 400
    data, ct = proxy_fetch(url)
    if data:
        return Response(data, mimetype=ct or "image/jpeg")
    return "", 404


@app.route("/api/stream/<sid>")
def stream(sid):
    audio_url = get_audio_url(sid)
    if not audio_url:
        return "", 404
    gen, ct, ext = stream_audio(audio_url)
    return Response(
        gen, status=200, mimetype=ct,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            "Content-Disposition": "inline",
        },
    )


@app.route("/api/download/<sid>")
def download(sid):
    raw = get_song_meta(sid)
    audio_url = raw.get("url", "")
    if not audio_url:
        return jsonify({"ok": False, "error": "no url"}), 404
    name = raw.get("name", sid)
    artist = raw.get("artist", "")
    title = f"{artist} - {name}" if artist else name
    ext = (urlparse(audio_url).path.rsplit(".", 1)[-1]
           if "." in urlparse(audio_url).path else "flac")
    filename = f"{title}.{ext}"
    gen, ct, _ = stream_audio(audio_url)
    hdrs = make_download_headers(filename, fallback=f"{name}.{ext}")
    hdrs["Content-Type"] = ct
    return Response(gen, status=200, headers=hdrs)


@app.route("/api/download-lyric/<sid>")
def download_lyric(sid):
    try:
        r = api_get("/163_lyric", {"id": sid}, timeout=8)
        body = r.json()
        d = (body.get("data") or {}) if body.get("code") == 200 else {}
    except Exception:
        d = {}
    raw = get_song_meta(sid)
    name = raw.get("name", sid)
    artist = raw.get("artist", "")
    title = f"{artist} - {name}" if artist else name
    filename = f"{title}.lrc"
    content = make_lyric_text(d, name, artist)
    hdrs = make_download_headers(filename, fallback=f"{name}.lrc")
    hdrs["Content-Type"] = "text/plain; charset=utf-8"
    return Response(content, status=200, headers=hdrs)


HTML = r'''<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kazam</title>
<style>
:root {
  --bg: #fdf6e3; --card: #fffef9; --ink: #1a1a2e;
  --mild: #555570; --fade: #9999b0; --border: #1a1a2e;
  --red: #ff4d6a; --blue: #3b82f6; --yellow: #facc15;
  --green: #22c55e; --shadow: 4px 4px 0 #1a1a2e;
  --shadow-sm: 3px 3px 0 #1a1a2e;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--ink); font-family:"DM Sans","PingFang SC","Microsoft YaHei",sans-serif; height:100vh; display:flex; flex-direction:column; overflow:hidden; background-image: radial-gradient(circle at 20% 80%, #ff4d6a10 0%, transparent 50%), radial-gradient(circle at 80% 20%, #3b82f610 0%, transparent 50%), radial-gradient(circle at 50% 50%, #facc1510 0%, transparent 50%); }
.topbar { display:flex; align-items:center; gap:0; padding:14px 18px; flex-shrink:0; }
.logo { font-size:28px; font-weight:900; letter-spacing:-1px; padding:7px 16px; background:var(--yellow); border:3px solid var(--border); box-shadow:var(--shadow-sm); margin-right:16px; white-space:nowrap; }
.search-wrap { flex:1; max-width:520px; display:flex; gap:0; }
.search-wrap input { flex:1; border:3px solid var(--border); border-right:none; padding:12px 18px; font-size:15px; font-weight:600; background:var(--card); outline:none; color:var(--ink); }
.search-wrap input::placeholder { color:var(--fade); font-weight:400; }
.search-wrap input:focus { background:#fff; }
.search-wrap button { background:var(--blue); color:#fff; border:3px solid var(--border); padding:12px 24px; font-size:15px; font-weight:800; cursor:pointer; box-shadow:var(--shadow-sm); transition:transform 0.1s; }
.search-wrap button:hover { transform:translate(-1px,-1px); box-shadow:5px 5px 0 var(--ink); }
.search-wrap button:active { transform:translate(1px,1px); box-shadow:1px 1px 0 var(--ink); }
.top-tabs { display:flex; gap:0; margin-left:12px; }
.top-tabs button { background:var(--card); border:3px solid var(--border); padding:10px 16px; font-size:14px; font-weight:700; cursor:pointer; margin-left:-3px; }
.top-tabs button:hover { background:var(--yellow); }
.top-tabs button.active { background:var(--ink); color:#fff; }
.main { flex:1; display:flex; gap:0; padding:0 18px 10px; overflow:hidden; min-height:0; }
.left-player { width:340px; flex-shrink:0; border:3px solid var(--border); border-right:none; background:var(--card); display:flex; flex-direction:column; align-items:center; padding:30px 20px 20px; gap:14px; }
.np-cover-wrap { width:180px; height:180px; flex-shrink:0; position:relative; border:5px solid var(--border); background:#f0ebe0; overflow:hidden; box-shadow:var(--shadow); }
.np-cover-wrap .placeholder { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; font-size:56px; color:var(--fade); }
.np-cover-wrap img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:none; z-index:1; }
.np-cover-wrap.has-cover .placeholder { display:none; }
.np-cover-wrap.has-cover img { display:block; }
.np-title { font-size:20px; font-weight:900; text-align:center; line-height:1.2; word-break:break-all; max-width:100%; }
.np-artist { font-size:13px; font-weight:600; color:var(--mild); text-align:center; }
.np-quality { font-size:10px; font-weight:800; padding:3px 10px; border:2px solid var(--border); background:var(--yellow); display:none; }
.np-actions { display:flex; gap:8px; }
.np-actions button { padding:6px 14px; border:2px solid var(--border); background:var(--card); font-size:11px; font-weight:700; cursor:pointer; box-shadow:2px 2px 0 var(--ink); transition:transform 0.1s; display:flex; align-items:center; gap:4px; }
.np-actions button:hover { transform:translate(-1px,-1px); box-shadow:3px 3px 0 var(--ink); background:var(--yellow); }
.np-actions button:active { transform:translate(0,0); box-shadow:1px 1px 0 var(--ink); }
.np-controls { display:flex; gap:10px; }
.np-controls button { width:42px; height:42px; border:3px solid var(--border); background:var(--card); font-size:18px; cursor:pointer; font-weight:bold; box-shadow:var(--shadow-sm); transition:transform 0.1s; display:flex; align-items:center; justify-content:center; }
.np-controls button:hover { transform:translate(-1px,-1px); box-shadow:4px 4px 0 var(--ink); }
.np-controls button:active { transform:translate(1px,1px); box-shadow:1px 1px 0 var(--ink); }
.np-controls .play-btn { width:52px; height:52px; font-size:22px; background:var(--red); color:#fff; }
.progress-wrap { width:100%; display:flex; align-items:center; gap:8px; }
.progress-wrap span { font-size:10px; font-weight:700; font-variant-numeric:tabular-nums; color:var(--mild); min-width:28px; }
.progress-wrap span:last-child { text-align:right; }
.prog-track { flex:1; height:10px; border:3px solid var(--border); background:#f0ebe0; cursor:pointer; }
.prog-fill { height:100%; background:var(--green); transition:width 0.15s linear; }
.vol-row { display:flex; align-items:center; gap:8px; }
.vol-row button { width:34px; height:34px; border:3px solid var(--border); background:var(--card); font-size:14px; cursor:pointer; font-weight:bold; display:flex; align-items:center; justify-content:center; }
.vol-row input { width:100px; accent-color:var(--ink); }
.right-panel { flex:1; min-width:0; border:3px solid var(--border); background:var(--card); display:flex; flex-direction:column; overflow:hidden; }
.rp-header { padding:10px 16px; border-bottom:3px solid var(--border); font-weight:800; font-size:13px; text-transform:uppercase; letter-spacing:2px; color:var(--mild); flex-shrink:0; display:flex; justify-content:space-between; align-items:center; }
.rp-body { flex:1; overflow-y:auto; }
.rp-body::-webkit-scrollbar { width:8px; }
.rp-body::-webkit-scrollbar-thumb { background:var(--border); }
.card-grid { padding:10px; display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:8px; align-content:start; }
.card { background:var(--card); border:3px solid var(--border); padding:12px; display:flex; align-items:center; gap:10px; cursor:pointer; box-shadow:var(--shadow-sm); transition:transform 0.12s, box-shadow 0.12s; position:relative; }
.card:hover { transform:translate(-2px,-2px); box-shadow:5px 5px 0 var(--ink); }
.card:hover .card-dl { opacity:1; }
.card:active { transform:translate(0,0); box-shadow:2px 2px 0 var(--ink); }
.card.playing { background:var(--green); color:#fff; border-color:var(--ink); }
.card.playing .sub { color:rgba(255,255,255,0.7); }
.card.playing .card-dl { border-color:rgba(255,255,255,0.4); color:rgba(255,255,255,0.7); }
.card.playing .card-dl:hover { border-color:#fff; color:#fff; background:rgba(255,255,255,0.15); }
.card-dl { position:absolute; top:6px; right:6px; width:28px; height:28px; border:2px solid var(--border); background:var(--card); font-size:14px; cursor:pointer; display:flex; align-items:center; justify-content:center; opacity:0; transition:all 0.15s; z-index:2; }
.card-dl:hover { background:var(--blue); color:#fff; border-color:var(--blue); }
.card .thumb-wrap { width:46px; height:46px; flex-shrink:0; border:3px solid var(--border); display:flex; align-items:center; justify-content:center; background:#f0ebe0; font-size:22px; color:var(--fade); overflow:hidden; }
.card .thumb-wrap img { width:100%; height:100%; object-fit:cover; display:block; }
.card .info { flex:1; min-width:0; padding-right:6px; }
.card .info .title { font-weight:800; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.card .info .sub { font-size:11px; font-weight:600; color:var(--mild); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:2px; }
.pl-item { display:flex; align-items:center; gap:8px; padding:10px 14px; border:2px solid transparent; cursor:pointer; font-weight:600; font-size:13px; position:relative; }
.pl-item:hover { border-color:var(--border); background:#f8f5ec; }
.pl-item.active { background:var(--ink); color:#fff; }
.pl-item.active .pl-del { color:rgba(255,255,255,0.5); }
.pl-item.active .pl-del:hover { color:#fff; background:var(--red); border-color:var(--red); }
.pl-item.active .pl-dl { color:rgba(255,255,255,0.5); border-color:rgba(255,255,255,0.3); }
.pl-item.active .pl-dl:hover { color:#fff; background:var(--blue); border-color:#fff; }
.pl-item .idx { width:24px; text-align:center; font-size:11px; font-weight:800; color:var(--fade); flex-shrink:0; }
.pl-item.active .idx { color:var(--yellow); }
.pl-item .name { flex:1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pl-dl, .pl-del { width:28px; height:28px; flex-shrink:0; border:2px solid transparent; background:transparent; font-size:14px; font-weight:700; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.15s; }
.pl-dl { color:var(--fade); }
.pl-dl:hover { border-color:var(--blue); color:var(--blue); background:#eef2ff; }
.pl-del { color:var(--fade); }
.pl-del:hover { border-color:var(--red); color:var(--red); background:#fff0f0; }
.pl-item:not(:hover) .pl-dl, .pl-item:not(:hover) .pl-del { opacity:0; }
.pl-item:hover .pl-dl, .pl-item:hover .pl-del { opacity:1; }
.pl-menu { position:fixed; background:var(--card); border:3px solid var(--border); box-shadow:var(--shadow); padding:4px; z-index:999; display:none; min-width:150px; }
.pl-menu.show { display:block; }
.pl-menu div { padding:10px 18px; font-size:13px; font-weight:700; cursor:pointer; }
.pl-menu div:hover { background:var(--ink); color:#fff; }
.lyric-line { padding:12px 0; font-weight:600; transition:all 0.3s cubic-bezier(0.4,0,0.2,1); }
.lyric-line .orig { font-size:20px; color:var(--fade); line-height:1.5; }
.lyric-line .tran { font-size:14px; color:var(--fade); opacity:0.4; margin-top:4px; }
.lyric-line.active .orig { font-size:26px; color:var(--ink); font-weight:900; }
.lyric-line.active .tran { font-size:17px; color:var(--mild); opacity:0.7; }
.lyrics-pad { padding:24px 28px 40px; }
.empty { text-align:center; padding:60px 20px; color:var(--fade); font-weight:700; }
.empty .icon { font-size:40px; margin-bottom:8px; }
.empty .hint { font-size:12px; opacity:0.6; }
.toast { position:fixed; bottom:30px; left:50%; transform:translateX(-50%); background:var(--ink); color:#fff; border:3px solid var(--ink); box-shadow:var(--shadow); padding:12px 28px; font-size:14px; font-weight:700; z-index:100; opacity:0; pointer-events:none; transition:opacity 0.3s; }
.toast.show { opacity:1; }
</style>
</head>
<body>

<div class="topbar">
  <div class="logo">KAZAM!</div>
  <div class="search-wrap">
    <input id="searchInput" placeholder="搜歌..." autofocus>
    <button id="searchBtn">&rarr;</button>
  </div>
  <div class="top-tabs">
    <button id="tabSearch" class="active">&#128270; 搜索</button>
    <button id="tabPlaylist">&#128195; 列表</button>
    <button id="tabLyrics">&#127908; 歌词</button>
    <button id="btnLoadPl">&#128229; 加载</button>
  </div>
</div>

<div class="main">
  <div class="left-player" id="leftPlayer">
    <div class="np-cover-wrap" id="npCoverWrap">
      <div class="placeholder">&#9835;</div>
      <img id="npCover" alt="">
    </div>
    <div class="np-title" id="npTitle">KAZAM!</div>
    <div class="np-artist" id="npArtist">等待播放</div>
    <div class="np-quality" id="npQuality"></div>
    <div class="np-actions">
      <button id="btnDlSong" title="下载音频" style="display:none;">&#128229; 下载音频</button>
      <button id="btnDlLyric" title="下载歌词" style="display:none;">&#128221; 下载歌词</button>
    </div>
    <div class="progress-wrap">
      <span id="timeCur">0:00</span>
      <div class="prog-track" id="progTrack"><div class="prog-fill" id="progFill" style="width:0%"></div></div>
      <span id="timeTot">0:00</span>
    </div>
    <div class="np-controls">
      <button id="btnMode" title="列表循环">&#128257;</button>
      <button id="btnPrev">&#9198;</button>
      <button class="play-btn" id="btnPlay">&#9654;</button>
      <button id="btnNext">&#9197;</button>
    </div>
    <div class="vol-row">
      <button id="btnVol">&#128266;</button>
      <input type="range" id="volSlider" min="0" max="100" value="70">
    </div>
  </div>

  <div class="right-panel" id="rightPanel">
    <div class="rp-header">
      <span id="rpTitle">搜 索 结 果</span>
      <span id="rpCount" style="font-size:11px;">&mdash;</span>
    </div>
    <div class="rp-body" id="rpBody">
      <div class="empty"><div class="icon">&#128269;</div>输入关键词搜索<div class="hint">双击播放 · 单击加入列表 · 悬停下载</div></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>
<audio id="audio" preload="auto" crossorigin="anonymous"></audio>

<script>
const $=s=>document.querySelector(s);
const $$=s=>document.querySelectorAll(s);
const audio=$('#audio');
let playlist=[],curIdx=-1,curTrack=null,mode='loop';
let lyricData=[],lyricCur=-1,viewMode='search',searchCache=[];

async function api(p){return (await fetch(p)).json();}
let tt;
function toast(m){const e=$('#toast');e.textContent=m;e.classList.add('show');clearTimeout(tt);tt=setTimeout(()=>e.classList.remove('show'),1800);}

function setActiveTab(tab){
  viewMode=tab;
  ['search','playlist','lyrics'].forEach(t=>$(`#tab${t[0].toUpperCase()+t.slice(1)}`).classList.toggle('active',t===tab));
}

function updateRightPanel(){
  const body=$('#rpBody');body.className='rp-body';
  if(viewMode==='search'){
    $('#rpTitle').textContent='搜 索 结 果';
    if(!searchCache.length){body.innerHTML='<div class="empty"><div class="icon">&#128269;</div>输入关键词搜索<div class="hint">双击播放 · 单击加入列表 · 悬停下载</div></div>';$('#rpCount').textContent='—';}
    else{body.innerHTML=`<div class="card-grid">${searchCache.map(cardHTML).join('')}</div>`;$('#rpCount').textContent=searchCache.length+' hits';bindCards();}
  }else if(viewMode==='playlist'){
    $('#rpTitle').textContent='播 放 列 表';
    if(!playlist.length){body.innerHTML='<div class="empty"><div class="icon">&#128195;</div>列表为空<div class="hint">双击搜索结果添加歌曲</div></div>';$('#rpCount').textContent='0';}
    else{body.innerHTML=playlist.map((t,i)=>`<div class="pl-item${i===curIdx?' active':''}" data-idx="${i}"><span class="idx">${i+1}</span><span class="name">${esc(t.name)} — ${esc(t.artist)}</span><button class="pl-dl" data-idx="${i}" title="下载音频">&#128229;</button><button class="pl-del" data-idx="${i}" title="移除">×</button></div>`).join('');$('#rpCount').textContent=playlist.length;
    body.querySelectorAll('.pl-item').forEach(el=>{el.onclick=()=>{curIdx=parseInt(el.dataset.idx);loadAndPlay(playlist[curIdx]);};el.oncontextmenu=e=>{e.preventDefault();showPlaylistMenu(e.clientX,e.clientY,parseInt(el.dataset.idx));};});
    body.querySelectorAll('.pl-dl').forEach(btn=>{btn.onclick=e=>{e.stopPropagation();downloadSong(playlist[parseInt(btn.dataset.idx)]);};});
    body.querySelectorAll('.pl-del').forEach(btn=>{btn.onclick=e=>{e.stopPropagation();removeFromPlaylist(parseInt(btn.dataset.idx));};});}
  }else if(viewMode==='lyrics'){
    $('#rpTitle').textContent='歌 词';body.className='rp-body lyrics-pad';
    if(!lyricData.length){body.innerHTML='<div class="empty"><div class="icon">&#127925;</div>暂无歌词<div class="hint">开始播放后显示</div></div>';}
    else{body.innerHTML=lyricData.map((l,i)=>`<div class="lyric-line${i===lyricCur?' active':''}" data-idx="${i}"><div class="orig">${esc(l.orig)}</div>${l.tran?`<div class="tran">${esc(l.tran)}</div>`:''}</div>`).join('');}
    $('#rpCount').textContent=lyricData.length?lyricData.length+' 句':'—';}
}

$('#tabSearch').onclick=()=>{setActiveTab('search');updateRightPanel();};
$('#tabPlaylist').onclick=()=>{setActiveTab('playlist');updateRightPanel();};
$('#tabLyrics').onclick=()=>{setActiveTab('lyrics');updateRightPanel();};
$('#searchBtn').onclick=doSearch;$('#searchInput').onkeydown=e=>{if(e.key==='Enter')doSearch();};

async function doSearch(){
  const kw=$('#searchInput').value.trim();if(!kw)return;setActiveTab('search');
  $('#rpBody').innerHTML='<div class="empty"><span class="icon">&#9203;</span>走你...</div>';
  const r=await api('/api/search?q='+encodeURIComponent(kw));
  if(!r.ok||!r.data.length){searchCache=[];updateRightPanel();return;}searchCache=r.data;updateRightPanel();
}

function cardHTML(t){
  const d=t.duration,ds=d?`${Math.floor(d/60000)}:${String(Math.floor(d/1000)%60).padStart(2,'0')}`:'--:--';
  const co=t.cover?`/api/cover-proxy?url=${encodeURIComponent(t.cover)}`:'';
  const thumb=co?`<img src="${co}" loading="lazy" onerror="this.parentElement.innerHTML='&#9835;'">`:`<span class="placeholder">&#9835;</span>`;
  return `<div class="card" data-id="${t.id}"><div class="thumb-wrap">${thumb}</div><button class="card-dl" data-id="${t.id}" title="下载音频">&#128229;</button><div class="info"><div class="title">${esc(t.name)}</div><div class="sub">${esc(t.artist)}</div><div class="sub" style="font-size:10px;">${ds} &middot; ${esc(t.album||'')}</div></div></div>`;
}

function bindCards(){$$('.card').forEach(c=>{c.ondblclick=()=>playById(c.dataset.id);c.onclick=()=>addToList(c.dataset.id);});$$('.card-dl').forEach(btn=>{btn.onclick=e=>{e.stopPropagation();const t=searchCache.find(x=>String(x.id)===String(btn.dataset.id));if(t)downloadSong(t);};});}

function downloadSong(track){toast('下载: '+(track.artist?track.artist+' - ':'')+track.name);const a=document.createElement('a');a.href=`/api/download/${track.id}`;a.download='';a.click();}
function downloadLyric(){if(!curTrack)return;toast('下载歌词: '+(curTrack.artist?curTrack.artist+' - ':'')+curTrack.name);const a=document.createElement('a');a.href=`/api/download-lyric/${curTrack.id}`;a.download='';a.click();}
$('#btnDlSong').onclick=()=>{if(curTrack)downloadSong(curTrack);};
$('#btnDlLyric').onclick=()=>downloadLyric();

function addToList(id){const t=searchCache.find(x=>String(x.id)===String(id));if(!t)return;if(!playlist.find(x=>x.id==id)){playlist.push({...t});toast('+ '+t.name);}if(viewMode==='playlist')updateRightPanel();}
function playById(id){if(!playlist.find(x=>x.id==id))addToList(id);curIdx=playlist.findIndex(x=>x.id==id);if(curIdx>=0)loadAndPlay(playlist[curIdx]);}

function showPlaylistMenu(x,y,idx){
  let m=document.getElementById('plMenu');if(!m){m=document.createElement('div');m.id='plMenu';m.className='pl-menu';document.body.appendChild(m);}
  m.innerHTML=`<div onclick="curIdx=${idx};loadAndPlay(playlist[${idx}]);hideMenu()">▶ 播放</div><div onclick="downloadSong(playlist[${idx}]);hideMenu()">&#128229; 下载音频</div><div onclick="curIdx=${idx};loadAndPlay(playlist[${idx}]);setTimeout(()=>downloadLyric(),500);hideMenu()">&#128221; 下载歌词</div><div onclick="removeFromPlaylist(${idx});hideMenu()" style="color:var(--red)">✕ 移除</div><div style="border-top:2px solid var(--border);" onclick="playlist=[];curIdx=-1;audio.pause();audio.src='';curTrack=null;lyricData=[];lyricCur=-1;updateRightPanel();updateCardStates();$('#npTitle').textContent='KAZAM!';$('#npArtist').textContent='等待播放';$('#npQuality').style.display='none';$('#btnDlSong').style.display='none';$('#btnDlLyric').style.display='none';showNpCover('');hideMenu();">🗑 清空全部</div>`;
  m.style.left=x+'px';m.style.top=y+'px';m.classList.add('show');
}
function hideMenu(){const m=document.getElementById('plMenu');if(m)m.classList.remove('show');}
document.addEventListener('click',e=>{if(!e.target.closest('#plMenu'))hideMenu();});

function removeFromPlaylist(idx){toast('已移除: '+playlist[idx].name);playlist.splice(idx,1);if(curIdx===idx){audio.pause();audio.src='';curTrack=null;curIdx=-1;$('#npTitle').textContent='KAZAM!';$('#npArtist').textContent='等待播放';$('#npQuality').style.display='none';$('#btnDlSong').style.display='none';$('#btnDlLyric').style.display='none';showNpCover('');lyricData=[];lyricCur=-1;}else if(curIdx>idx)curIdx--;updateRightPanel();updateCardStates();}

async function loadAndPlay(t){
  curTrack=t;$('#npTitle').textContent=t.name;$('#npArtist').textContent=t.artist;$('#npQuality').style.display='none';showNpCover(t.cover||'');lyricData=[];lyricCur=-1;if(viewMode==='lyrics')updateRightPanel();
  const sr=await api(`/api/song/${t.id}`);
  if(sr.ok&&sr.data.url){audio.src=`/api/stream/${t.id}`;audio.play();const lv=sr.data.level;const qm={jymaster:'💎臻品母带',lossless:'🔵无损FLAC',hires:'🟣Hi-Res',exhigh:'🟢极高320kbps',higher:'🟡较高192kbps',standard:'⚪标准 128kbps'};const label=qm[lv]||`🎵 ${lv?.toUpperCase()||'HQ'}`;const br=sr.data.br?`${Math.round(sr.data.br/1000)}kbps`:'',sz=sr.data.size?` · ${(sr.data.size/1024/1024).toFixed(1)}MB`:'';$('#npQuality').textContent=`${label} · ${br}${sz}`;$('#npQuality').style.display='inline-block';$('#btnDlSong').style.display='inline-flex';$('#btnDlLyric').style.display='inline-flex';if(sr.data.cover)showNpCover(sr.data.cover);}
  else{toast('❌ 获取链接失败');$('#btnDlSong').style.display='none';$('#btnDlLyric').style.display='none';}
  const lr=await api(`/api/lyric/${t.id}`);if(lr.ok&&lr.data){parseLyric(lr.data.lrc,lr.data.tlrc);$('#btnDlLyric').style.display='inline-flex';}
  updateCardStates();if(viewMode==='playlist'||viewMode==='lyrics')updateRightPanel();
}

function showNpCover(url){const wrap=$('#npCoverWrap'),img=$('#npCover');if(!url){wrap.classList.remove('has-cover');img.src='';return;}img.src=`/api/cover-proxy?url=${encodeURIComponent(url)}`;img.onload=()=>wrap.classList.add('has-cover');img.onerror=()=>{wrap.classList.remove('has-cover');img.src='';};}
function updateCardStates(){$$('.card').forEach(c=>c.classList.toggle('playing',c.dataset.id==(curTrack?.id)));}

function parseLyric(lrc,tlrc){lyricData=[];const om=new Map(),tm=new Map();for(const m of(lrc||'').matchAll(/\[(\d+):(\d+(?:\.\d+)?)\](.*)/g)){const ms=parseInt(m[1])*60000+parseInt(parseFloat(m[2])*1000),tx=m[3].trim();if(tx)om.set(ms,tx);}for(const m of(tlrc||'').matchAll(/\[(\d+):(\d+(?:\.\d+)?)\](.*)/g)){const ms=parseInt(m[1])*60000+parseInt(parseFloat(m[2])*1000),tx=m[3].trim();if(tx)tm.set(ms,tx);}const all=new Set([...om.keys(),...tm.keys()]);lyricData=[...all].sort((a,b)=>a-b).map(ms=>({ms,orig:om.get(ms)||'',tran:tm.get(ms)||''}));if(viewMode==='lyrics')updateRightPanel();}
function syncLyric(){if(!lyricData.length)return;const pos=audio.currentTime*1000;let idx=-1;for(let i=0;i<lyricData.length;i++){if(lyricData[i].ms<=pos)idx=i;else break;}if(idx===lyricCur)return;$$('.lyric-line').forEach((el,i)=>{el.classList.toggle('active',i===idx);if(i===idx)el.scrollIntoView({behavior:'smooth',block:'center'});});lyricCur=idx;}
setInterval(syncLyric,200);

$('#btnPlay').onclick=()=>{if(audio.paused){if(!audio.src&&playlist.length){curIdx=0;loadAndPlay(playlist[0]);}else audio.play();}else audio.pause();};
$('#btnNext').onclick=()=>{if(!playlist.length)return;const n=playlist.length;if(mode==='shuffle')curIdx=Math.floor(Math.random()*n);else if(mode==='repeat-one'){audio.currentTime=0;audio.play();return}else curIdx=(curIdx+1)%n;loadAndPlay(playlist[curIdx]);};
$('#btnPrev').onclick=()=>{if(!playlist.length)return;curIdx=(curIdx-1+playlist.length)%playlist.length;loadAndPlay(playlist[curIdx]);};
$('#btnMode').onclick=()=>{const modes={loop:['🔁','列表循环'],shuffle:['🔀','随机'],'repeat-one':['🔂','单曲循环']};const ks=Object.keys(modes);mode=ks[(ks.indexOf(mode)+1)%3];$('#btnMode').textContent=modes[mode][0];$('#btnMode').title=modes[mode][1];};
$('#btnVol').onclick=()=>{audio.muted=!audio.muted;$('#btnVol').textContent=audio.muted?'🔇':'🔊';};
$('#volSlider').oninput=()=>{audio.volume=$('#volSlider').value/100;};
$('#progTrack').onclick=e=>{const r=$('#progTrack').getBoundingClientRect();audio.currentTime=(e.clientX-r.left)/r.width*audio.duration;};
audio.ontimeupdate=()=>{if(audio.duration){$('#progFill').style.width=(audio.currentTime/audio.duration*100)+'%';$('#timeCur').textContent=fmt(audio.currentTime);}};
audio.ondurationchange=()=>{$('#timeTot').textContent=fmt(audio.duration);};
audio.onended=()=>{if(mode==='repeat-one'){audio.currentTime=0;audio.play();}else $('#btnNext').click();};
audio.onplay=()=>{$('#btnPlay').innerHTML='⏸';updateCardStates();};
audio.onpause=()=>{$('#btnPlay').innerHTML='▶';updateCardStates();};
audio.onerror=()=>toast('⚠ 播放失败');
function fmt(s){if(!s||!isFinite(s))return'0:00';s=Math.floor(s);return`${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;}

$('#btnLoadPl').onclick=async()=>{const raw=prompt('歌单 ID 或链接:');if(!raw)return;let pid=raw;const m=raw.match(/[?&]id=(\d+)/);if(m)pid=m[1];if(!/^\d+$/.test(pid)){toast('不是有效ID');return;}toast('加载中...');const r=await api(`/api/playlist/${pid}`);if(r.ok&&r.data.length){playlist=r.data;curIdx=-1;toast(`加载了 ${r.data.length} 首`);setActiveTab('playlist');updateRightPanel();}else toast('❌ 加载失败');};

document.onkeydown=e=>{if(e.target.tagName==='INPUT')return;switch(e.code){case'Space':e.preventDefault();$('#btnPlay').click();break;case'ArrowRight':$('#btnNext').click();break;case'ArrowLeft':$('#btnPrev').click();break;case'ArrowUp':audio.volume=Math.min(1,audio.volume+0.05);$('#volSlider').value=Math.round(audio.volume*100);break;case'ArrowDown':audio.volume=Math.max(0,audio.volume-0.05);$('#volSlider').value=Math.round(audio.volume*100);break;case'KeyM':$('#btnVol').click();break;case'Delete':if(viewMode==='playlist'&&curIdx>=0)removeFromPlaylist(curIdx);break;case'KeyS':if(e.ctrlKey&&curTrack){e.preventDefault();downloadSong(curTrack);}break;case'KeyL':if(e.ctrlKey&&curTrack){e.preventDefault();downloadLyric();}break;}};
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
$('#searchInput').focus();
</script>
</body>
</html>'''


@app.route("/")
def index():
    return HTML


def main():
    port = 58080
    print(f"""
╔══════════════════════════╗
║  KAZAM!  http://127.0.0.1:{port} ║
╚══════════════════════════╝
""")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()