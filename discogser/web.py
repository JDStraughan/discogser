"""A small local web UI for people who would rather not live in a terminal.

`discogser-web` starts a server on http://127.0.0.1:8765. Drag your photos onto
the page (or paste a folder path), pick dry-run or commit, and watch the same
matching engine stream results into a live table in the browser.

Requires the `web` extra:

    pip install "discogser[web]"

The server binds to localhost only, because it uses your Discogs and Anthropic
tokens. Do not expose it to a network.
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from .config import Config, ConfigError
from .discogs import DiscogsClient, DiscogsError
from .ledger import Ledger
from .pipeline import (
    HEIC_HELP,
    IMAGE_EXTENSIONS,
    discover_images,
    heic_unsupported_count,
    run,
)

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB total per upload

# The server holds the user's Discogs + Anthropic tokens, so it must only ever
# answer requests addressed to localhost. A foreign Host header means a
# DNS-rebinding attempt from a page the user is visiting; reject it.
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}

# default-src 'none' locks everything down; we then open exactly what the single
# inline page needs. 'unsafe-inline' is retained for the inline <style>/<script>
# (this is one self-contained local document); all dynamic text is escaped
# before insertion, and connect/img/frame/base are tightly scoped.
_CSP = (
    "default-src 'none'; "
    "img-src 'self' https://*.discogs.com data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def _is_localhost(host: str) -> bool:
    host = (host or "").strip().lower()
    if "@" in host:                       # no legitimate Host carries userinfo
        return False
    if host.startswith("["):              # bracketed IPv6: [::1] or [::1]:8765
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:            # host:port (IPv4 or name)
        host = host.split(":")[0]
    # else leave a bare IPv6 ("::1") or plain hostname intact
    return host in _ALLOWED_HOSTS


def _bump(counts: dict[str, int], status: str) -> None:
    """Increment the tally buckets for a status, mirroring the terminal UI."""
    if status == "high":
        counts["added"] += 1
    elif status == "cover":
        counts["added"] += 1
        counts["covers"] += 1
    elif status == "medium":
        counts["added"] += 1
        counts["medium"] += 1
    elif status == "guess":
        counts["review"] += 1
        counts["guesses"] += 1
    elif status == "review":
        counts["review"] += 1
    elif status == "skipped":
        counts["skipped"] += 1
    elif status == "error":
        counts["errors"] += 1


class WebReporter:
    """A `ui.Reporter` that serializes pipeline events onto a queue for SSE."""

    def __init__(self, total: int, commit: bool, events: queue.Queue) -> None:
        self.total = total
        self.commit = commit
        self._q = events
        self._done = 0
        self.counts = dict(added=0, covers=0, medium=0, guesses=0, review=0, skipped=0, errors=0)

    def __enter__(self) -> WebReporter:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def header(self, folder_name: str, folder_id: int, owned: int) -> None:
        self._q.put({
            "type": "header", "folder": folder_name, "folder_id": folder_id,
            "owned": owned, "total": self.total, "commit": self.commit,
        })

    def album(
        self, *, status: str, artist: str, title: str, release_id: int | None,
        signal: str, committed: bool, value: str = "-", extra: dict | None = None,
    ) -> None:
        self._done += 1
        _bump(self.counts, status)
        url = f"https://www.discogs.com/release/{release_id}" if release_id else ""
        event = {
            "type": "album", "index": self._done, "total": self.total,
            "status": status, "artist": artist, "title": title,
            "release_id": release_id, "url": url, "signal": signal,
            "value": value, "committed": committed,
            "candidates": (extra or {}).get("candidates", []),
            "key": (extra or {}).get("key"),
        }
        self._q.put(event)

    def drift_halt(self, names: tuple[str, str, str], roles: tuple[str, ...]) -> None:
        self._q.put({"type": "drift", "names": list(names), "roles": list(roles)})

    def leftovers(self, names: list[str]) -> None:
        self._q.put({"type": "leftovers", "names": list(names)})

    def summary(self, tokens: tuple[int, int] | None = None) -> None:
        self._q.put({
            "type": "summary", "commit": self.commit,
            "tokens": list(tokens) if tokens else None, **self.counts,
        })


def create_app() -> Any:
    from flask import Flask, Response, jsonify, render_template_string, request, send_file
    from werkzeug.utils import secure_filename

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    runs: dict[str, queue.Queue] = {}
    run_dirs: dict[str, Path] = {}
    uploads: dict[str, Path] = {}

    @app.before_request
    def _guard():
        if not _is_localhost(request.host):
            return "forbidden host", 403
        # CSRF: requiring a custom header forces a CORS preflight, so a page the
        # user is merely visiting cannot drive these endpoints with a "simple"
        # cross-site request (the preflight has no allow-origin and is blocked).
        if request.method == "POST" and not request.headers.get("X-Requested-With"):
            return "missing X-Requested-With header", 403

    @app.after_request
    def _security_headers(resp):
        resp.headers["Content-Security-Policy"] = _CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return resp

    @app.get("/")
    def index() -> str:
        return render_template_string(_PAGE)

    @app.post("/upload")
    def upload():
        files = request.files.getlist("photos")
        if not files:
            return jsonify(error="No files received."), 400
        dest = Path(tempfile.mkdtemp(prefix="discogser_up_"))
        saved = 0
        for f in files:
            name = secure_filename(f.filename or "")
            if name and Path(name).suffix.lower() in IMAGE_EXTENSIONS:
                f.save(dest / name)
                saved += 1
        if not saved:
            shutil.rmtree(dest, ignore_errors=True)
            return jsonify(error="No image files in that selection."), 400
        upload_id = secrets.token_hex(8)
        uploads[upload_id] = dest
        return jsonify(upload_id=upload_id, count=saved)

    @app.post("/run")
    def start():
        data = request.get_json(silent=True) or {}
        upload_id = data.get("upload_id")
        if upload_id:
            folder = uploads.get(upload_id)
            if folder is None or not folder.is_dir():
                return jsonify(error="Upload expired; please re-add your photos."), 400
        else:
            folder = Path(str(data.get("folder", "")).strip()).expanduser()
            if not folder.is_dir():
                return jsonify(error=f"Folder not found: {folder}"), 400
        try:
            config = Config.load()
        except ConfigError as exc:
            return jsonify(error=str(exc)), 400

        commit = bool(data.get("commit"))
        folder_name = (data.get("folder_name") or "").strip() or None
        cover = not bool(data.get("no_cover"))
        run_id = secrets.token_hex(8)
        events: queue.Queue = queue.Queue()
        runs[run_id] = events
        run_dirs[run_id] = folder

        def worker() -> None:
            images = discover_images(folder)
            heic = heic_unsupported_count(images)
            if heic:
                events.put({"type": "warning", "message": HEIC_HELP})
                if images and heic == len(images):  # nothing is readable
                    events.put({"type": "done", "exit_code": 2})
                    return
            try:
                code = run(
                    folder, config=config, commit=commit, folder_name=folder_name,
                    cover_match=cover,
                    reporter_factory=lambda total: WebReporter(total, commit, events),
                )
            except Exception as exc:  # never let the thread die silently
                logger.exception("web run failed")
                events.put({"type": "fatal", "message": str(exc)})
                code = 1
            events.put({"type": "done", "exit_code": code})

        threading.Thread(target=worker, daemon=True).start()
        return jsonify(run_id=run_id)

    @app.get("/stream/<run_id>")
    def stream(run_id: str):
        events = runs.get(run_id)
        if events is None:
            return "unknown run", 404

        def gen():
            try:
                while True:
                    event = events.get()
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("type") == "done":
                        return
            finally:
                runs.pop(run_id, None)

        return Response(gen(), mimetype="text/event-stream")

    @app.post("/resolve")
    def resolve():
        """Add a user-chosen pressing for a flagged album, and mark the ledger
        so a later run won't re-flag it."""
        data = request.get_json(silent=True) or {}
        try:
            release_id = int(data.get("release_id"))
        except (TypeError, ValueError):
            return jsonify(error="A release id is required."), 400
        try:
            config = Config.load()
        except ConfigError as exc:
            return jsonify(error=str(exc)), 400
        folder_name = (data.get("folder_name") or config.discogs_folder).strip()
        try:
            with DiscogsClient(
                token=config.discogs_token, username=config.discogs_username,
                user_agent=config.user_agent,
            ) as client:
                folder_id = client.resolve_folder_id(folder_name)
                client.add_to_collection(folder_id, release_id)
        except DiscogsError as exc:
            return jsonify(error=str(exc)), 502

        key = data.get("key")
        if key:
            try:
                with Ledger() as ledger:
                    ledger.record(
                        key, status="added", release_id=release_id,
                        title=data.get("title") or "", confidence="MANUAL",
                        signal="resolved in browser", committed=True,
                        data={"manual_resolve": True},
                    )
            except Exception:  # ledger is best-effort; the add already succeeded
                logger.exception("ledger update after resolve failed")

        return jsonify(ok=True, release_id=release_id,
                       url=f"https://www.discogs.com/release/{release_id}")

    @app.get("/download/<run_id>/<name>")
    def download(run_id: str, name: str):
        if name not in ("results.csv", "review.csv"):
            return "not found", 404
        folder = run_dirs.get(run_id)
        if folder is None:
            return "not found", 404
        path = folder / name
        if not path.is_file():
            return "not found", 404
        return send_file(path, as_attachment=True, download_name=name)

    return app


def main() -> int:
    try:
        app = create_app()
    except ImportError:
        print("The web UI needs Flask. Install it with:  pip install 'discogser[web]'")
        return 1
    print(f"discogser web UI on http://{HOST}:{PORT}  (Ctrl-C to stop)")
    app.run(host=HOST, port=PORT, threaded=True)
    return 0


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>discogser</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#0f1115; color:#e6e6e6;
         font:14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  header { padding:18px 24px; border-bottom:1px solid #232733; }
  h1 { margin:0; font-size:20px; letter-spacing:.5px; } h1 span { color:#4fd6d6; }
  .sub { color:#868fa3; font-size:12px; margin-top:2px; }
  .wrap { max-width:1100px; margin:0 auto; padding:24px; }
  .drop { border:2px dashed #2c3340; border-radius:12px; padding:34px; text-align:center;
          color:#9aa4b2; cursor:pointer; transition:.15s; background:#131722; }
  .drop:hover, .drop.over { border-color:#4fd6d6; color:#cdd3dd; background:#15202b; }
  .drop b { color:#e6e6e6; }
  .opts { display:flex; flex-wrap:wrap; gap:14px 20px; align-items:center; margin:16px 2px; }
  label { font-size:12px; color:#9aa4b2; display:flex; align-items:center; gap:6px; }
  input[type=text] { background:#0f1115; border:1px solid #2a2f3d; color:#e6e6e6;
         padding:8px 10px; border-radius:6px; min-width:260px; font:inherit; }
  button { background:#4fd6d6; color:#0f1115; border:0; padding:10px 20px; border-radius:6px;
           font:inherit; font-weight:700; cursor:pointer; }
  button:disabled { opacity:.45; cursor:not-allowed; }
  .pathline { margin-top:10px; color:#868fa3; font-size:12px; }
  .pathline a { color:#4fd6d6; cursor:pointer; }
  .meta { color:#9aa4b2; margin:18px 2px 8px; min-height:20px; }
  table { width:100%; border-collapse:collapse; margin-top:8px; }
  th,td { text-align:left; padding:6px 10px; border-bottom:1px solid #1c2029; white-space:nowrap; }
  th { color:#868fa3; font-weight:500; font-size:12px; }
  td.album { white-space:normal; } td.num,td.val { text-align:right; color:#9aa4b2; }
  a { color:#4fd6d6; text-decoration:none; } a:hover { text-decoration:underline; }
  .badge { font-weight:700; }
  .high{color:#5ad15a;} .cover{color:#4fd6d6;} .medium{color:#e0c64b;}
  .guess{color:#d46fd4;} .review{color:#e25c5c;} .skipped{color:#7c8492;} .error{color:#ff6b6b;}
  .price{color:#5ad15a;}
  .banner { margin-top:18px; padding:14px 16px; border-radius:8px; display:none;
            background:#2a1416; border:1px solid #5a2a2e; color:#ffb4b4; }
  .banner.show { display:block; }
  .summary { margin-top:18px; padding:14px 16px; background:#151823; border:1px solid #232733;
             border-radius:10px; display:none; } .summary.show { display:block; }
  .summary b { font-size:18px; }
  .pill { display:inline-block; padding:1px 8px; border-radius:20px; font-size:11px;
          background:#232733; color:#cdd3dd; margin-left:6px; }
  .resolve { color:#d46fd4; cursor:pointer; } .resolve:hover { text-decoration:underline; }
  .det > td { background:#11141c; }
  .cands { display:flex; flex-wrap:wrap; gap:12px; }
  .cand { width:150px; background:#151823; border:1px solid #232733; border-radius:8px; padding:8px; }
  .cand img { width:100%; height:130px; object-fit:contain; background:#0f1115; border-radius:4px; }
  .cand .cmeta { font-size:11px; color:#9aa4b2; margin:6px 0; min-height:42px; }
  .cand .cmeta b { color:#e6e6e6; }
  .cand button { width:100%; padding:6px; font-size:11px; }
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px;
             overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  :focus-visible { outline:2px solid #4fd6d6; outline-offset:2px; border-radius:4px; }
  @media (prefers-reduced-motion: reduce) {
    * { transition:none !important; animation:none !important; }
  }
</style></head>
<body>
<header><div class="wrap" style="padding:0">
  <h1>discog<span>ser</span></h1>
  <div class="sub">catalog your vinyl into Discogs from photos</div>
</div></header>

<div class="wrap">
  <div class="drop" id="drop" role="button" tabindex="0"
       aria-label="Add photos: drop image files here, or press Enter to choose files">
    <div><b>Drop your photos here</b>, or click to choose</div>
    <div class="sub" id="dropcount" style="margin-top:6px">three shots per record: front, back, side-A runout</div>
  </div>
  <input type="file" id="files" multiple accept="image/*,.heic,.heif,.tif,.tiff,.webp" aria-label="Choose photo files" hidden>
  <div class="pathline">on this machine already? <a id="usepath" role="button" tabindex="0">paste a folder path instead</a></div>
  <div id="pathbox" style="display:none; margin-top:8px">
    <label for="folder" class="sr-only">Photos folder path on this machine</label>
    <input type="text" id="folder" placeholder="/path/to/photos">
  </div>

  <div class="opts">
    <label for="folder_name" class="sr-only">Discogs collection folder name</label>
    <input type="text" id="folder_name" placeholder="Discogs folder (Uncategorized)">
    <label><input type="checkbox" id="commit"> commit (actually add)</label>
    <label><input type="checkbox" id="no_cover"> skip cover match</label>
    <button id="go" disabled>Run</button>
  </div>

  <div class="meta" id="meta" aria-live="polite"></div>
  <div class="banner" id="banner" role="alert"></div>

  <table id="grid" style="display:none">
    <thead><tr><th>#</th><th>conf</th><th>artist - title</th>
      <th>release</th><th class="val">value</th><th>signal</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>

  <div class="summary" id="summary" aria-live="polite"></div>
</div>

<script>
const $ = s => document.querySelector(s);
const BADGE = {high:"HIGH",cover:"COVER",medium:"MEDIUM",guess:"GUESS",review:"LOW",skipped:"DUP",error:"ERR"};
let upload = null, runId = null, currentFolder = "";
const rowData = {};
function esc(s){ return (s||"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function toggleDet(i){ const d=document.getElementById("det-"+i); if(d) d.style.display = d.style.display==="none"?"table-row":"none"; }
async function pick(i, ci){
  const d=rowData[i], c=d.cands[ci];
  const btns=document.querySelectorAll("#det-"+i+" button"); btns.forEach(b=>b.disabled=true);
  try{
    const r=await (await fetch("/resolve",{method:"POST",headers:{"Content-Type":"application/json","X-Requested-With":"discogser"},
      body:JSON.stringify({release_id:c.id, key:d.key, folder_name:currentFolder, title:c.title})})).json();
    if(r.error){ banner(r.error); btns.forEach(b=>b.disabled=false); return; }
    const b=document.getElementById("badge-"+i); b.textContent="ADDED"; b.className="badge high";
    document.getElementById("rel-"+i).innerHTML=`<a href="${r.url}" target="_blank" rel="noopener noreferrer">r${r.release_id}</a>`;
    document.getElementById("det-"+i).style.display="none";
  }catch(err){ banner("Add failed: "+err); btns.forEach(b=>b.disabled=false); }
}
function banner(m){ const b=$("#banner"); b.textContent=m; b.classList.add("show"); }
function ready(){ $("#go").disabled = !(upload || $("#folder").value.trim()); }

const drop=$("#drop"), input=$("#files");
drop.addEventListener("click",()=>input.click());
drop.addEventListener("dragover",e=>{e.preventDefault();drop.classList.add("over");});
drop.addEventListener("dragleave",()=>drop.classList.remove("over"));
drop.addEventListener("drop",e=>{e.preventDefault();drop.classList.remove("over");sendFiles(e.dataTransfer.files);});
input.addEventListener("change",()=>sendFiles(input.files));
drop.addEventListener("keydown",e=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); input.click(); } });
const showPath=()=>{ $("#pathbox").style.display="block"; $("#folder").focus(); };
$("#usepath").addEventListener("click",showPath);
$("#usepath").addEventListener("keydown",e=>{ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); showPath(); } });
$("#folder").addEventListener("input",()=>{ upload=null; ready(); });

async function sendFiles(fileList){
  const files=[...fileList].filter(f=>/\\.(jpe?g|png|heic|heif|tiff?|webp)$/i.test(f.name));
  if(!files.length){ banner("No image files in that selection."); return; }
  $("#banner").classList.remove("show");
  $("#dropcount").textContent="uploading "+files.length+" photos…";
  const fd=new FormData(); files.forEach(f=>fd.append("photos",f));
  try{
    const r=await (await fetch("/upload",{method:"POST",headers:{"X-Requested-With":"discogser"},body:fd})).json();
    if(r.error){ banner(r.error); $("#dropcount").textContent=""; return; }
    upload=r.upload_id;
    $("#dropcount").textContent=r.count+" photos ready  ("+Math.round(r.count/3)+" albums)";
    ready();
  }catch(err){ banner("Upload failed: "+err); }
}

$("#go").addEventListener("click",async()=>{
  $("#rows").innerHTML=""; $("#grid").style.display="none";
  $("#summary").classList.remove("show"); $("#banner").classList.remove("show");
  $("#meta").textContent="Starting…"; $("#go").disabled=true;
  const body={ commit:$("#commit").checked, no_cover:$("#no_cover").checked,
               folder_name:$("#folder_name").value };
  if(upload) body.upload_id=upload; else body.folder=$("#folder").value;
  let res;
  try{ res=await (await fetch("/run",{method:"POST",headers:{"Content-Type":"application/json","X-Requested-With":"discogser"},body:JSON.stringify(body)})).json(); }
  catch(err){ $("#meta").textContent=""; banner("Request failed: "+err); ready(); return; }
  if(res.error){ $("#meta").textContent=""; banner(res.error); ready(); return; }
  runId=res.run_id;
  const es=new EventSource("/stream/"+runId);
  es.onmessage=m=>{
    const ev=JSON.parse(m.data);
    if(ev.type==="header"){
      currentFolder = ev.folder || "";
      $("#meta").textContent=`${ev.commit?"COMMIT":"DRY-RUN"} · ${ev.total} albums · folder ${esc(ev.folder)} · you own ${ev.owned}`;
      $("#grid").style.display="table";
    } else if(ev.type==="album"){
      const rel=ev.url?`<a href="${ev.url}" target="_blank" rel="noopener noreferrer">r${ev.release_id}</a>`:"-";
      const val=(ev.value&&ev.value!=="-")?`<span class="price">${ev.value}</span>`:"-";
      const cands=ev.candidates||[];
      const sigCell=`<td style="color:#8b93a1">${esc(ev.signal)}`+
        (cands.length?` <span class="resolve" onclick="toggleDet(${ev.index})">· pick (${cands.length})</span>`:"")+`</td>`;
      const tr=document.createElement("tr"); tr.id="row-"+ev.index;
      tr.innerHTML=`<td class="num">${ev.index}/${ev.total}</td>`+
        `<td class="badge ${ev.status}" id="badge-${ev.index}">${BADGE[ev.status]||ev.status}</td>`+
        `<td class="album"><b>${esc(ev.artist)}</b>${ev.title?" - "+esc(ev.title):""}</td>`+
        `<td id="rel-${ev.index}">${rel}</td><td class="val">${val}</td>`+sigCell;
      $("#rows").appendChild(tr);
      if(cands.length){
        rowData[ev.index]={key:ev.key, cands};
        const det=document.createElement("tr"); det.id="det-"+ev.index; det.className="det"; det.style.display="none";
        det.innerHTML=`<td colspan="6"><div class="sub" style="margin-bottom:8px">which pressing is yours? click to add it to your collection.</div>`+
          `<div class="cands">`+cands.map((c,i)=>
            `<div class="cand"><img src="${esc(c.thumb)}" alt="${esc(c.title)} cover" loading="lazy" onerror="this.style.visibility='hidden'">`+
            `<div class="cmeta"><b>${esc(c.title)}</b><br>${esc([c.year,c.country,c.format].filter(Boolean).join(' · '))}</div>`+
            `<button onclick="pick(${ev.index},${i})">Add to Discogs</button></div>`).join("")+
          `</div></td>`;
        $("#rows").appendChild(det);
      }
    } else if(ev.type==="warning"){ banner(ev.message); }
    else if(ev.type==="drift"){ banner("Sequence drift at "+ev.names.join(" .. ")+". A shot is missing or extra; fix and re-run."); }
    else if(ev.type==="leftovers"){ banner("Trailing photos that don't complete a set of 3: "+ev.names.join(", ")); }
    else if(ev.type==="fatal"){ banner("Run failed: "+ev.message); }
    else if(ev.type==="summary"){
      const verb=ev.commit?"added":"would add";
      const tok=ev.tokens?`<span class="pill">${ev.tokens[0].toLocaleString()} in / ${ev.tokens[1].toLocaleString()} out tokens</span>`:"";
      const dl=`<a href="/download/${runId}/results.csv">results.csv</a> · <a href="/download/${runId}/review.csv">review.csv</a>`;
      $("#summary").innerHTML=
        `<b class="high">${ev.added}</b> ${verb} <span class="pill">${ev.covers} cover · ${ev.medium} medium</span><br>`+
        `<b class="review">${ev.review}</b> flagged for review <span class="pill">${ev.guesses} hunches</span><br>`+
        `<b class="skipped">${ev.skipped}</b> skipped &nbsp; <b class="error">${ev.errors}</b> errors ${tok}<br>`+
        `<div class="sub" style="margin-top:8px">download ${dl}</div>`;
      $("#summary").classList.add("show");
    } else if(ev.type==="done"){ es.close(); ready();
      if($("#meta").textContent==="Starting…") $("#meta").textContent="Finished (exit "+ev.exit_code+")."; }
  };
  es.onerror=()=>{ es.close(); ready(); };
});
</script>
</body></html>
"""
