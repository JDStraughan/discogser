"""A small local web UI for people who would rather not live in a terminal.

`discogser-web` starts a server on http://127.0.0.1:8765. You paste the path to
your photos folder, pick dry-run or commit, and watch the same matching engine
stream results into a live table in the browser. Requires the `web` extra:

    pip install "discogser[web]"

The server binds to localhost only, because it uses your Discogs and Anthropic
tokens. Do not expose it to a network.
"""

from __future__ import annotations

import json
import logging
import queue
import secrets
import threading
from pathlib import Path
from typing import Any

from .config import Config, ConfigError
from .pipeline import run

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765


# Status -> the bucket(s) it increments, mirroring the terminal tally.
def _bump(counts: dict[str, int], status: str) -> None:
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
    from flask import Flask, Response, jsonify, render_template_string, request

    app = Flask(__name__)
    runs: dict[str, queue.Queue] = {}

    @app.get("/")
    def index() -> str:
        return render_template_string(_PAGE)

    @app.post("/run")
    def start():
        data = request.get_json(silent=True) or {}
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
  body { margin: 0; background:#0f1115; color:#e6e6e6;
         font:14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  header { padding:18px 24px; border-bottom:1px solid #232733; }
  h1 { margin:0; font-size:20px; letter-spacing:.5px; }
  h1 span { color:#4fd6d6; }
  .wrap { max-width:1100px; margin:0 auto; padding:24px; }
  form { display:flex; flex-wrap:wrap; gap:12px 18px; align-items:end;
         background:#151823; padding:16px; border:1px solid #232733; border-radius:10px; }
  label { display:flex; flex-direction:column; gap:4px; font-size:12px; color:#9aa4b2; }
  input[type=text] { background:#0f1115; border:1px solid #2a2f3d; color:#e6e6e6;
         padding:8px 10px; border-radius:6px; min-width:340px; font:inherit; }
  .row { display:flex; gap:16px; align-items:center; }
  button { background:#4fd6d6; color:#0f1115; border:0; padding:10px 18px;
           border-radius:6px; font:inherit; font-weight:700; cursor:pointer; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .meta { color:#9aa4b2; margin:18px 2px 8px; min-height:20px; }
  table { width:100%; border-collapse:collapse; margin-top:8px; }
  th, td { text-align:left; padding:6px 10px; border-bottom:1px solid #1c2029; white-space:nowrap; }
  th { color:#6b7383; font-weight:500; font-size:12px; }
  td.album { white-space:normal; }
  td.num, td.val { text-align:right; color:#9aa4b2; }
  a { color:#4fd6d6; text-decoration:none; } a:hover { text-decoration:underline; }
  .badge { font-weight:700; }
  .high { color:#5ad15a; } .cover { color:#4fd6d6; } .medium { color:#e0c64b; }
  .guess { color:#d46fd4; } .review { color:#e25c5c; } .skipped { color:#7c8492; }
  .error { color:#ff6b6b; } .price { color:#5ad15a; }
  .banner { margin-top:18px; padding:14px 16px; border-radius:8px; display:none; }
  .banner.show { display:block; }
  .banner.bad { background:#2a1416; border:1px solid #5a2a2e; color:#ffb4b4; }
  .summary { margin-top:18px; padding:14px 16px; background:#151823;
             border:1px solid #232733; border-radius:10px; display:none; }
  .summary.show { display:block; }
  .summary b { font-size:18px; }
  .pill { display:inline-block; padding:1px 8px; border-radius:20px; font-size:11px;
          background:#232733; color:#cdd3dd; margin-left:6px; }
</style></head>
<body>
<header><div class="wrap" style="padding:0"><h1>discog<span>ser</span></h1></div></header>
<div class="wrap">
  <form id="f">
    <label>Photos folder
      <input type="text" id="folder" placeholder="/path/to/photos" required>
    </label>
    <label>Discogs folder (optional)
      <input type="text" id="folder_name" placeholder="Uncategorized">
    </label>
    <div class="row">
      <label style="flex-direction:row; align-items:center; gap:6px">
        <input type="checkbox" id="commit"> commit (actually add)
      </label>
      <label style="flex-direction:row; align-items:center; gap:6px">
        <input type="checkbox" id="no_cover"> skip cover match
      </label>
    </div>
    <button type="submit" id="go">Run</button>
  </form>

  <div class="meta" id="meta"></div>
  <div class="banner bad" id="banner"></div>

  <table id="grid" style="display:none">
    <thead><tr><th>#</th><th>conf</th><th>artist - title</th>
      <th>release</th><th class="val">value</th><th>signal</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>

  <div class="summary" id="summary"></div>
</div>

<script>
const $ = s => document.querySelector(s);
const BADGE = {high:"HIGH",cover:"COVER",medium:"MEDIUM",guess:"GUESS",
               review:"LOW",skipped:"DUP",error:"ERR"};
function banner(msg){ const b=$("#banner"); b.textContent=msg; b.classList.add("show"); }

$("#f").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#rows").innerHTML=""; $("#grid").style.display="none";
  $("#summary").classList.remove("show"); $("#banner").classList.remove("show");
  $("#meta").textContent="Starting…"; $("#go").disabled=true;

  const body = {
    folder: $("#folder").value, folder_name: $("#folder_name").value,
    commit: $("#commit").checked, no_cover: $("#no_cover").checked,
  };
  let res;
  try { res = await (await fetch("/run",{method:"POST",
        headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json(); }
  catch (err) { $("#meta").textContent=""; banner("Request failed: "+err); $("#go").disabled=false; return; }
  if (res.error){ $("#meta").textContent=""; banner(res.error); $("#go").disabled=false; return; }

  const es = new EventSource("/stream/"+res.run_id);
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type==="header"){
      $("#meta").textContent =
        `${ev.commit?"COMMIT":"DRY-RUN"} · ${ev.total} albums · folder ${ev.folder} · you own ${ev.owned}`;
      $("#grid").style.display="table";
    } else if (ev.type==="album"){
      const tr=document.createElement("tr");
      const rel = ev.url ? `<a href="${ev.url}" target="_blank">r${ev.release_id}</a>` : "-";
      const val = (ev.value && ev.value!=="-") ? `<span class="price">${ev.value}</span>` : "-";
      tr.innerHTML =
        `<td class="num">${ev.index}/${ev.total}</td>`+
        `<td class="badge ${ev.status}">${BADGE[ev.status]||ev.status}</td>`+
        `<td class="album"><b>${esc(ev.artist)}</b>${ev.title?" - "+esc(ev.title):""}</td>`+
        `<td>${rel}</td><td class="val">${val}</td>`+
        `<td style="color:#8b93a1">${esc(ev.signal)}</td>`;
      $("#rows").appendChild(tr);
    } else if (ev.type==="drift"){
      banner("Sequence drift at "+ev.names.join(" .. ")+". A shot is missing or extra; fix the folder and re-run.");
    } else if (ev.type==="leftovers"){
      banner("Trailing photos that don't complete a set of 3: "+ev.names.join(", "));
    } else if (ev.type==="fatal"){
      banner("Run failed: "+ev.message);
    } else if (ev.type==="summary"){
      const verb = ev.commit ? "added" : "would add";
      const tok = ev.tokens ? `<span class="pill">${ev.tokens[0].toLocaleString()} in / ${ev.tokens[1].toLocaleString()} out tokens</span>` : "";
      $("#summary").innerHTML =
        `<b class="high">${ev.added}</b> ${verb} <span class="pill">${ev.covers} cover · ${ev.medium} medium</span><br>`+
        `<b class="review">${ev.review}</b> flagged for review <span class="pill">${ev.guesses} hunches</span><br>`+
        `<b class="skipped">${ev.skipped}</b> skipped &nbsp; <b class="error">${ev.errors}</b> errors ${tok}`;
      $("#summary").classList.add("show");
    } else if (ev.type==="done"){
      es.close(); $("#go").disabled=false;
      if (!$("#meta").textContent || $("#meta").textContent==="Starting…")
        $("#meta").textContent="Finished (exit "+ev.exit_code+").";
    }
  };
  es.onerror = () => { es.close(); $("#go").disabled=false; };
});
function esc(s){ return (s||"").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }
</script>
</body></html>
"""
