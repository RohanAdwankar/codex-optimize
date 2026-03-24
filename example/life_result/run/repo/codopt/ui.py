from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app(state_file: Path, control_dir: Path) -> "FastAPI":
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, PlainTextResponse

    app = FastAPI(title="codopt")

    def load_state() -> dict:
        if not state_file.exists():
            return {}
        return json.loads(state_file.read_text(encoding="utf-8"))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>codopt</title>
  <style>
    body { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: 0; background: #f4f0e8; color: #1c1a17; }
    header { padding: 16px 20px; background: #1c1a17; color: #f4f0e8; }
    main { display: grid; grid-template-columns: 380px 1fr; gap: 16px; padding: 16px; }
    .panel { background: #fffaf1; border: 1px solid #d6ccb8; border-radius: 10px; padding: 12px; overflow: auto; }
    .node { border-bottom: 1px solid #e4dac8; padding: 8px 0; cursor: pointer; }
    .node:last-child { border-bottom: 0; }
    pre { white-space: pre-wrap; word-break: break-word; }
    button { background: #8f2d21; color: white; border: 0; padding: 8px 10px; border-radius: 8px; cursor: pointer; }
    .meta { color: #6a6256; font-size: 12px; }
  </style>
</head>
<body>
  <header>
    <div id="title">codopt</div>
    <div class="meta" id="summary"></div>
  </header>
  <main>
    <section class="panel">
      <h3>Nodes</h3>
      <div id="nodes"></div>
    </section>
    <section class="panel">
      <h3 id="detail-title">Run</h3>
      <div id="detail"></div>
      <h4>Agent Log</h4>
      <pre id="log"></pre>
    </section>
  </main>
<script>
let selectedNodeId = null;
async function refresh() {
  const res = await fetch('/api/state');
  const data = await res.json();
  document.getElementById('title').textContent = 'codopt run ' + (data.meta?.run_id || '');
  document.getElementById('summary').textContent = `status=${data.meta?.status} round=${data.meta?.current_round} baseline=${data.meta?.baseline_score ?? 'n/a'} final=${(data.meta?.final_branches || []).length}`;
  const nodes = document.getElementById('nodes');
  nodes.innerHTML = '';
  for (const node of data.nodes || []) {
    const div = document.createElement('div');
    div.className = 'node';
    div.innerHTML = `<strong>${node.node_id}</strong><div class="meta">${node.status} score=${node.score ?? 'n/a'} branch=${node.branch_name}</div>`;
    div.onclick = () => { selectedNodeId = node.node_id; renderDetail(node); };
    nodes.appendChild(div);
    if (selectedNodeId === node.node_id) renderDetail(node);
  }
  if (!selectedNodeId && (data.nodes || []).length) {
    selectedNodeId = data.nodes[0].node_id;
    renderDetail(data.nodes[0]);
  }
}
async function renderDetail(node) {
  document.getElementById('detail-title').textContent = node.node_id;
  document.getElementById('detail').innerHTML = `
    <div class="meta">branch=${node.branch_name} depth=${node.depth} parent=${node.parent_id || 'root'}</div>
    <div>status=${node.status}</div>
    <div>score=${node.score ?? 'n/a'}</div>
    <div>test_passed=${node.test_passed}</div>
    <div>surviving=${node.surviving}</div>
    <div>metric_text=${(node.metric_text || '').replaceAll('<', '&lt;')}</div>
    <div style="margin-top:8px"><button onclick="pruneNode('${node.node_id}')">Prune Node</button></div>
  `;
  const logRes = await fetch(`/api/node/${node.node_id}/log`);
  document.getElementById('log').textContent = await logRes.text();
}
async function pruneNode(nodeId) {
  await fetch(`/api/node/${nodeId}/prune`, { method: 'POST' });
  await refresh();
}
setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>"""

    @app.get("/api/state")
    def api_state() -> dict:
        return load_state()

    @app.get("/api/node/{node_id}/log", response_class=PlainTextResponse)
    def api_log(node_id: str) -> str:
        state = load_state()
        for node in state.get("nodes", []):
            if node["node_id"] == node_id:
                log_file = node.get("log_file")
                if log_file and Path(log_file).exists():
                    return Path(log_file).read_text(encoding="utf-8")
                return ""
        raise HTTPException(status_code=404, detail="unknown node")

    @app.post("/api/node/{node_id}/prune")
    def api_prune(node_id: str) -> dict[str, str]:
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / f"{node_id}.prune").write_text("1\n", encoding="utf-8")
        return {"status": "queued"}

    return app


def start_ui_server(state_file: Path, control_dir: Path, port: int, *, open_browser: bool = True) -> threading.Thread:
    import uvicorn

    app = create_app(state_file, control_dir)
    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if open_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass
    return thread
