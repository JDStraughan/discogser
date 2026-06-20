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
from .pipeline import IMAGE_EXTENSIONS, run

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB total per upload


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
        signal: str, committed: bool, value: str = "-",
    ) -> None:
        self._done += 1
        _bump(self.counts, status)
        url = f"https://www.discogs.com/release/{release_id}" if release_id else ""
        self._q.put({
            "type": "album", "index": self._done, "total": self.total,
            "status": status, "artist": artist, "title": title,
            "release_id": release_id, "url": url, "signal": signal,
            "value": value, "committed": committed,
        })

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
  .sub { color:#6b7383; font-size:12px; margin-top:2px; }
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
  .pathline { margin-top:10px; color:#6b7383; font-size:12px; }
  .pathline a { color:#4fd6d6; cursor:pointer; }
  .meta { color:#9aa4b2; margin:18px 2px 8px; min-height:20px; }
  table { width:100%; border-collapse:collapse; margin-top:8px; }
  th,td { text-align:left; padding:6px 10px; border-bottom:1px solid #1c2029; white-space:nowrap; }
  th { color:#6b7383; font-weight:500; font-size:12px; }
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
</style></head>
<body>
<header><div class="wrap" style="padding:0">
  <h1>discog<span>ser</span></h1>
  <div class="sub">catalog your vinyl into Discogs from photos</div>
</div></header>

<div class="wrap">
  <div class="drop" id="drop">
    <div><b>Drop your photos here</b>, or click to choose</div>
    <div class="sub" id="dropcount" style="margin-top:6px">three shots per record: front, back, side-A runout</div>
  </div>
  <input type="file" id="files" multiple accept="image/*,.heic,.heif,.tif,.tiff,.webp" hidden>
  <div class="pathline">on this machine already? <a id="usepath">paste a folder path instead</a></div>
  <div id="pathbox" style="display:none; margin-top:8px">
    <input type="text" id="folder" placeholder="/path/to/photos">
  </div>

  <div class="opts">
    <input type="text" id="folder_name" placeholder="Discogs folder (Uncategorized)">
    <label><input type="checkbox" id="commit"> commit (actually add)</label>
    <label><input type="checkbox" id="no_cover"> skip cover match</label>
    <button id="go" disabled>Run</button>
  </div>

  <div class="meta" id="meta"></div>
  <div class="banner" id="banner"></div>

  <table id="grid" style="display:none">
    <thead><tr><th>#</th><th>conf</th><th>artist - title</th>
      <th>release</th><th class="val">value</th><th>signal</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>

  <div class="summary" id="summary"></div>
</div>

<script>
const $ = s => document.querySelector(s);
const BADGE = {high:"HIGH",cover:"COVER",medium:"MEDIUM",guess:"GUESS",review:"LOW",skipped:"DUP",error:"ERR"};
let upload = null, runId = null;
function esc(s){ return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
function banner(m){ const b=$("#banner"); b.textContent=m; b.classList.add("show"); }
function ready(){ $("#go").disabled = !(upload || $("#folder").value.trim()); }

const drop=$("#drop"), input=$("#files");
drop.addEventListener("click",()=>input.click());
drop.addEventListener("dragover",e=>{e.preventDefault();drop.classList.add("over");});
drop.addEventListener("dragleave",()=>drop.classList.remove("over"));
drop.addEventListener("drop",e=>{e.preventDefault();drop.classList.remove("over");sendFiles(e.dataTransfer.files);});
input.addEventListener("change",()=>sendFiles(input.files));
$("#usepath").addEventListener("click",()=>{ $("#pathbox").style.display="block"; $("#folder").focus(); });
$("#folder").addEventListener("input",()=>{ upload=null; ready(); });

async function sendFiles(fileList){
  const files=[...fileList].filter(f=>/\\.(jpe?g|png|heic|heif|tiff?|webp)$/i.test(f.name));
  if(!files.length){ banner("No image files in that selection."); return; }
  $("#banner").classList.remove("show");
  $("#dropcount").textContent="uploading "+files.length+" photos…";
  const fd=new FormData(); files.forEach(f=>fd.append("photos",f));
  try{
    const r=await (await fetch("/upload",{method:"POST",body:fd})).json();
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
  try{ res=await (await fetch("/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json(); }
  catch(err){ $("#meta").textContent=""; banner("Request failed: "+err); ready(); return; }
  if(res.error){ $("#meta").textContent=""; banner(res.error); ready(); return; }
  runId=res.run_id;
  const es=new EventSource("/stream/"+runId);
  es.onmessage=m=>{
    const ev=JSON.parse(m.data);
    if(ev.type==="header"){
      $("#meta").textContent=`${ev.commit?"COMMIT":"DRY-RUN"} · ${ev.total} albums · folder ${esc(ev.folder)} · you own ${ev.owned}`;
      $("#grid").style.display="table";
    } else if(ev.type==="album"){
      const rel=ev.url?`<a href="${ev.url}" target="_blank">r${ev.release_id}</a>`:"-";
      const val=(ev.value&&ev.value!=="-")?`<span class="price">${ev.value}</span>`:"-";
      const tr=document.createElement("tr");
      tr.innerHTML=`<td class="num">${ev.index}/${ev.total}</td>`+
        `<td class="badge ${ev.status}">${BADGE[ev.status]||ev.status}</td>`+
        `<td class="album"><b>${esc(ev.artist)}</b>${ev.title?" - "+esc(ev.title):""}</td>`+
        `<td>${rel}</td><td class="val">${val}</td><td style="color:#8b93a1">${esc(ev.signal)}</td>`;
      $("#rows").appendChild(tr);
    } else if(ev.type==="drift"){ banner("Sequence drift at "+ev.names.join(" .. ")+". A shot is missing or extra; fix and re-run."); }
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
