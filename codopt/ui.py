from __future__ import annotations

import json
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _seconds_between(start: str | None, end: str | None) -> float | None:
    start_dt = _parse_timestamp(start)
    end_dt = _parse_timestamp(end)
    if start_dt is None or end_dt is None:
        return None
    return (end_dt - start_dt).total_seconds()


def _load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _parse_agent_log(log_file: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    raw_events: list[dict[str, Any]] = []
    latest_diff: str | None = None
    turn_status: str | None = None
    token_usage: dict[str, Any] | None = None

    if not log_file.exists():
        return {
            "entries": entries,
            "raw_event_count": 0,
            "latest_diff": None,
            "turn_status": None,
            "token_usage": None,
        }

    for raw_line in log_file.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            entries.append({"kind": "raw_text", "text": raw_line})
            continue

        raw_events.append(event)
        method = event.get("method")
        payload = event.get("payload", {})

        if method == "item/completed":
            item = payload.get("item", {})
            item_type = item.get("type")
            if item_type == "agentMessage":
                text = item.get("text", "").strip()
                if text:
                    entries.append(
                        {
                            "kind": "message",
                            "phase": item.get("phase", "message"),
                            "text": text,
                        }
                    )
            elif item_type == "commandExecution":
                entries.append(
                    {
                        "kind": "command",
                        "command": item.get("command", ""),
                        "status": item.get("status"),
                        "exit_code": item.get("exit_code"),
                        "cwd": item.get("cwd"),
                        "output": item.get("aggregated_output", ""),
                    }
                )
            elif item_type == "reasoning":
                summary = item.get("summary") or []
                if summary:
                    entries.append({"kind": "reasoning", "summary": summary})
        elif method == "turn/diff/updated":
            latest_diff = payload.get("diff")
            if latest_diff:
                entries.append({"kind": "diff", "diff": latest_diff})
        elif method == "turn/completed":
            turn = payload.get("turn", {})
            turn_status = turn.get("status")
            error = turn.get("error")
            if turn_status or error:
                entries.append(
                    {
                        "kind": "turn",
                        "status": turn_status,
                        "error": error,
                    }
                )
        elif method == "thread/status/changed":
            status = payload.get("status", {})
            entries.append(
                {
                    "kind": "thread_status",
                    "status": status.get("type"),
                    "flags": status.get("active_flags", []),
                }
            )
        elif method == "thread/tokenUsage/updated":
            token_usage = payload.get("token_usage")

    return {
        "entries": entries,
        "raw_event_count": len(raw_events),
        "latest_diff": latest_diff,
        "turn_status": turn_status,
        "token_usage": token_usage,
    }


def create_app(state_file: Path, control_dir: Path | None, *, read_only: bool = False) -> "FastAPI":
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

    app = FastAPI(title="codopt")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>codopt</title>
  <style>
    :root {
      --ink: #1f1c18;
      --muted: #6c655b;
      --paper: #f6efe2;
      --panel: #fff9ee;
      --line: #d5cab4;
      --accent: #8a2f22;
      --accent-soft: #f3dfd7;
      --good: #225d3b;
      --good-soft: #d9eee0;
      --bad: #8b1e2d;
      --bad-soft: #f5d7dc;
      --warn: #996f12;
      --warn-soft: #f6e9c9;
      --blue: #1e5a88;
      --blue-soft: #dbe8f2;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Palatino, serif;
      background:
        radial-gradient(circle at top left, #fff6e8 0%, #f6efe2 42%, #efe6d4 100%);
      color: var(--ink);
    }
    header {
      padding: 18px 22px 16px;
      border-bottom: 1px solid rgba(31, 28, 24, 0.12);
      background: rgba(255, 249, 238, 0.86);
      backdrop-filter: blur(8px);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .header-row {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
    }
    .title-block h1 {
      margin: 0 0 6px;
      font-size: 28px;
      line-height: 1.05;
      letter-spacing: 0.01em;
    }
    .subtle {
      color: var(--muted);
      font-size: 13px;
    }
    .badge-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 6px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      border: 1px solid rgba(31, 28, 24, 0.1);
      background: rgba(255, 255, 255, 0.55);
    }
    .badge.live { background: var(--good-soft); color: var(--good); }
    .badge.done { background: var(--blue-soft); color: var(--blue); }
    .badge.readonly { background: var(--warn-soft); color: var(--warn); }
    main {
      display: grid;
      grid-template-rows: 340px minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      min-height: calc(100vh - 92px);
    }
    .panel {
      background: rgba(255, 249, 238, 0.9);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 18px 60px rgba(74, 52, 28, 0.08);
      overflow: hidden;
    }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 16px 10px;
      border-bottom: 1px solid rgba(31, 28, 24, 0.08);
    }
    .panel-head h2, .panel-head h3 {
      margin: 0;
      font-size: 15px;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }
    .graph-wrap {
      height: calc(100% - 48px);
      padding: 8px 12px 12px;
    }
    #graph {
      width: 100%;
      height: 100%;
      display: block;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.75), rgba(249,242,229,0.98));
      border-radius: 12px;
      border: 1px solid rgba(31, 28, 24, 0.08);
    }
    .graph-axis {
      font-size: 11px;
      fill: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .graph-label {
      font-size: 12px;
      fill: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .graph-edge {
      stroke: rgba(31, 28, 24, 0.18);
      stroke-width: 1.5;
      fill: none;
    }
    .graph-point {
      cursor: pointer;
      transition: transform 0.12s ease;
    }
    .graph-point:hover { transform: scale(1.08); }
    .layout {
      display: grid;
      grid-template-columns: 360px minmax(0, 1fr);
      gap: 16px;
      min-height: 0;
    }
    .node-list {
      height: 100%;
      overflow: auto;
      padding: 10px 14px 14px;
    }
    .node-card {
      border: 1px solid rgba(31, 28, 24, 0.08);
      border-radius: 14px;
      padding: 12px;
      margin-bottom: 10px;
      cursor: pointer;
      background: rgba(255,255,255,0.58);
    }
    .node-card:hover {
      border-color: rgba(138, 47, 34, 0.35);
      background: rgba(255,255,255,0.82);
    }
    .node-card.selected {
      border-color: var(--accent);
      box-shadow: inset 0 0 0 1px rgba(138, 47, 34, 0.18);
      background: linear-gradient(180deg, #fff9f5, #fff3ec);
    }
    .node-card strong {
      display: block;
      font-size: 14px;
      margin-bottom: 4px;
    }
    .node-meta {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .detail {
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      min-height: 0;
    }
    .detail-body {
      padding: 16px;
      overflow: auto;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .stat {
      border: 1px solid rgba(31, 28, 24, 0.08);
      border-radius: 14px;
      padding: 12px;
      background: rgba(255,255,255,0.6);
    }
    .stat-label {
      display: block;
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-bottom: 8px;
    }
    .stat-value {
      font-size: 18px;
      line-height: 1.2;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .meta-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 8px 18px;
      padding: 14px 0 8px;
      border-top: 1px solid rgba(31, 28, 24, 0.08);
    }
    .meta-grid div {
      font-size: 13px;
      line-height: 1.45;
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      word-break: break-word;
    }
    .tabs {
      display: flex;
      gap: 8px;
      padding: 0 16px 12px;
      border-bottom: 1px solid rgba(31, 28, 24, 0.08);
    }
    .tab {
      appearance: none;
      border: 1px solid rgba(31, 28, 24, 0.12);
      border-radius: 999px;
      background: rgba(255,255,255,0.72);
      color: var(--ink);
      padding: 8px 12px;
      font-size: 12px;
      cursor: pointer;
    }
    .tab.active {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    .tab-panel {
      display: none;
      padding: 16px;
      overflow: auto;
      min-height: 0;
    }
    .tab-panel.active { display: block; }
    .log-entry {
      border: 1px solid rgba(31, 28, 24, 0.08);
      border-radius: 14px;
      padding: 12px;
      margin-bottom: 12px;
      background: rgba(255,255,255,0.62);
    }
    .log-kind {
      display: inline-block;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .log-entry.message.commentary { border-left: 5px solid #bf6d2f; }
    .log-entry.message.final_answer { border-left: 5px solid var(--good); }
    .log-entry.command { border-left: 5px solid var(--blue); }
    .log-entry.diff { border-left: 5px solid var(--accent); }
    .log-entry.turn { border-left: 5px solid var(--warn); }
    .log-entry.thread_status { border-left: 5px solid #7a6d58; }
    .output, .raw-log, .metric-box {
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      line-height: 1.5;
      margin: 0;
    }
    .output {
      margin-top: 10px;
      padding: 10px 12px;
      border-radius: 10px;
      background: #f7f1e5;
      border: 1px solid rgba(31, 28, 24, 0.08);
      max-height: 320px;
      overflow: auto;
    }
    .diff-box {
      border-radius: 10px;
      background: #171512;
      color: #f5ecda;
      padding: 10px 0;
      overflow: auto;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      line-height: 1.45;
    }
    .diff-line {
      display: block;
      padding: 0 12px;
      white-space: pre;
    }
    .diff-add { background: rgba(52, 128, 79, 0.28); color: #dcf4df; }
    .diff-del { background: rgba(147, 41, 59, 0.28); color: #ffdbe2; }
    .diff-hunk { color: #f4d28d; }
    .diff-meta { color: #9ec5f0; }
    .diff-plain { color: #f5ecda; }
    .empty {
      color: var(--muted);
      font-style: italic;
    }
    .actions {
      display: flex;
      gap: 10px;
      align-items: center;
      margin-top: 10px;
    }
    button.action {
      background: var(--accent);
      color: white;
      border: 0;
      padding: 10px 12px;
      border-radius: 10px;
      cursor: pointer;
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    button.action:disabled {
      background: #c5b6a5;
      cursor: default;
    }
    @media (max-width: 980px) {
      main { grid-template-rows: 280px minmax(0, 1fr); }
      .layout { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-row">
      <div class="title-block">
        <h1 id="title">codopt</h1>
        <div class="subtle" id="subtitle"></div>
        <div class="badge-row" id="badges"></div>
      </div>
      <div class="subtle" id="summary"></div>
    </div>
  </header>
  <main>
    <section class="panel">
      <div class="panel-head">
        <h2>Run Graph</h2>
        <div class="subtle" id="graph-summary"></div>
      </div>
      <div class="graph-wrap">
        <svg id="graph" viewBox="0 0 1000 320" preserveAspectRatio="none"></svg>
      </div>
    </section>
    <section class="layout">
      <section class="panel">
        <div class="panel-head">
          <h3>Agents</h3>
          <div class="subtle" id="list-summary"></div>
        </div>
        <div class="node-list" id="nodes"></div>
      </section>
      <section class="panel detail">
        <div class="panel-head">
          <h3 id="detail-title">Run</h3>
          <div class="subtle" id="detail-status"></div>
        </div>
        <div class="detail-body" id="detail-summary"></div>
        <div class="tabs">
          <button class="tab active" data-tab="parsed">Parsed Log</button>
          <button class="tab" data-tab="raw">Raw Logs</button>
        </div>
        <div class="tab-panel active" id="tab-parsed"></div>
        <div class="tab-panel" id="tab-raw"></div>
      </section>
    </section>
  </main>
<script>
const READ_ONLY = __READ_ONLY__;
let stateCache = null;
let selectedNodeId = null;
let activeTab = 'parsed';
let parsedLogCache = new Map();
let rawLogCache = new Map();

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'n/a';
  const numeric = Number(value);
  if (Math.abs(numeric) >= 1000) return numeric.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return numeric.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function formatSeconds(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'n/a';
  return `${Number(value).toFixed(1)}s`;
}

function elapsedSeconds(iso, startIso) {
  if (!iso || !startIso) return null;
  const delta = (new Date(iso).getTime() - new Date(startIso).getTime()) / 1000;
  return Number.isFinite(delta) ? delta : null;
}

function statusBadge(meta) {
  const badges = [];
  const status = meta?.status || 'unknown';
  badges.push(`<span class="badge ${status === 'completed' ? 'done' : 'live'}">status ${escapeHtml(status)}</span>`);
  if (READ_ONLY) badges.push('<span class="badge readonly">viewer mode</span>');
  return badges.join('');
}

function getSelectedNode() {
  const nodes = stateCache?.nodes || [];
  return nodes.find((node) => node.node_id === selectedNodeId) || null;
}

function computeGraphData(nodes, runStartIso) {
  const graphNodes = [];
  const edges = [];
  const scores = [];
  const times = [];
  for (const node of nodes) {
    const anchorTime = node.started_at || node.created_at || runStartIso;
    const elapsed = elapsedSeconds(anchorTime, runStartIso) ?? 0;
    const score = typeof node.score === 'number' ? node.score : null;
    graphNodes.push({
      node_id: node.node_id,
      parent_id: node.parent_id,
      score,
      elapsed,
      status: node.status,
      surviving: node.surviving,
      pruned: node.pruned,
      depth: node.depth
    });
    times.push(elapsed);
    if (score !== null) scores.push(score);
  }
  const byId = Object.fromEntries(graphNodes.map((node) => [node.node_id, node]));
  for (const node of graphNodes) {
    if (node.parent_id && byId[node.parent_id]) {
      edges.push({ from: node.parent_id, to: node.node_id });
    }
  }
  return {
    nodes: graphNodes,
    edges,
    minScore: scores.length ? Math.min(...scores) : 0,
    maxScore: scores.length ? Math.max(...scores) : 1,
    maxTime: times.length ? Math.max(...times) : 1
  };
}

function pointColor(node) {
  if (node.node_id === 'baseline') return '#1e5a88';
  if (node.pruned) return '#8b1e2d';
  if (node.surviving) return '#225d3b';
  if (node.status === 'failed') return '#8b1e2d';
  return '#8a2f22';
}

function renderGraph() {
  const svg = document.getElementById('graph');
  const state = stateCache || {};
  const nodes = state.nodes || [];
  const runStart = state.meta?.created_at;
  const graph = computeGraphData(nodes, runStart);
  const width = 1000;
  const height = 320;
  const margin = { top: 18, right: 22, bottom: 36, left: 88 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const scoreSpan = Math.max(graph.maxScore - graph.minScore, 1);
  const timeSpan = Math.max(graph.maxTime, 1);
  const xFor = (elapsed) => margin.left + (elapsed / timeSpan) * innerWidth;
  const yFor = (score) => {
    if (score === null || score === undefined) return margin.top + innerHeight;
    return margin.top + innerHeight - ((score - graph.minScore) / scoreSpan) * innerHeight;
  };
  const nodesById = Object.fromEntries(graph.nodes.map((node) => [node.node_id, node]));
  const parts = [];

  for (let i = 0; i < 5; i += 1) {
    const y = margin.top + (innerHeight / 4) * i;
    parts.push(`<line x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}" stroke="rgba(31,28,24,0.08)" />`);
  }
  for (let i = 0; i < 5; i += 1) {
    const x = margin.left + (innerWidth / 4) * i;
    parts.push(`<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${height - margin.bottom}" stroke="rgba(31,28,24,0.08)" />`);
  }

  for (const edge of graph.edges) {
    const from = nodesById[edge.from];
    const to = nodesById[edge.to];
    if (!from || !to) continue;
    const x1 = xFor(from.elapsed);
    const y1 = yFor(from.score);
    const x2 = xFor(to.elapsed);
    const y2 = yFor(to.score);
    parts.push(`<path class="graph-edge" d="M ${x1} ${y1} C ${x1 + 28} ${y1}, ${x2 - 28} ${y2}, ${x2} ${y2}" />`);
  }

  for (const node of graph.nodes) {
    const x = xFor(node.elapsed);
    const y = yFor(node.score);
    const radius = selectedNodeId === node.node_id ? 7.5 : 5.2;
    const stroke = selectedNodeId === node.node_id ? '#171512' : 'rgba(255,255,255,0.92)';
    parts.push(
      `<circle class="graph-point" cx="${x}" cy="${y}" r="${radius}" fill="${pointColor(node)}" stroke="${stroke}" stroke-width="2" data-node-id="${escapeHtml(node.node_id)}">` +
      `<title>${escapeHtml(node.node_id)} score=${escapeHtml(formatNumber(node.score))} t=${escapeHtml(formatSeconds(node.elapsed))}</title></circle>`
    );
  }

  const minScoreLabel = formatNumber(graph.minScore);
  const maxScoreLabel = formatNumber(graph.maxScore);
  const maxTimeLabel = formatSeconds(graph.maxTime);
  parts.push(`<text x="${margin.left}" y="${height - 10}" class="graph-label">elapsed time</text>`);
  parts.push(`<text x="18" y="${margin.top + 10}" class="graph-label">score</text>`);
  parts.push(`<text x="10" y="${margin.top + innerHeight}" class="graph-axis">${escapeHtml(minScoreLabel)}</text>`);
  parts.push(`<text x="10" y="${margin.top + 12}" class="graph-axis">${escapeHtml(maxScoreLabel)}</text>`);
  parts.push(`<text x="${width - margin.right - 28}" y="${height - 10}" class="graph-axis">${escapeHtml(maxTimeLabel)}</text>`);

  svg.innerHTML = parts.join('');
  svg.querySelectorAll('.graph-point').forEach((point) => {
    point.addEventListener('click', () => {
      selectedNodeId = point.getAttribute('data-node-id');
      render();
    });
  });

  document.getElementById('graph-summary').textContent =
    `${graph.nodes.length} points, ${graph.edges.length} edges, score range ${minScoreLabel} to ${maxScoreLabel}`;
}

function renderNodeList() {
  const nodes = [...(stateCache?.nodes || [])];
  nodes.sort((a, b) => {
    if (a.depth !== b.depth) return a.depth - b.depth;
    const aScore = typeof a.score === 'number' ? a.score : -Infinity;
    const bScore = typeof b.score === 'number' ? b.score : -Infinity;
    return bScore - aScore;
  });
  const container = document.getElementById('nodes');
  document.getElementById('list-summary').textContent = `${nodes.length} nodes`;
  container.innerHTML = nodes.map((node) => {
    const selected = node.node_id === selectedNodeId ? 'selected' : '';
    return `
      <div class="node-card ${selected}" data-node-id="${escapeHtml(node.node_id)}">
        <strong>${escapeHtml(node.node_id)}</strong>
        <div class="node-meta">status=${escapeHtml(node.status)} depth=${escapeHtml(node.depth)} score=${escapeHtml(formatNumber(node.score))}</div>
        <div class="node-meta">branch=${escapeHtml(node.branch_name)} parent=${escapeHtml(node.parent_id || 'root')}</div>
      </div>
    `;
  }).join('');
  container.querySelectorAll('.node-card').forEach((card) => {
    card.addEventListener('click', () => {
      selectedNodeId = card.getAttribute('data-node-id');
      render();
    });
  });
}

function summaryStats(node, baselineScore, runStart) {
  const parent = (stateCache?.nodes || []).find((candidate) => candidate.node_id === node.parent_id) || null;
  const parentScore = parent?.score ?? null;
  const baselineDelta = typeof node.score === 'number' && typeof baselineScore === 'number'
    ? node.score - baselineScore
    : null;
  const parentDelta = typeof node.score === 'number' && typeof parentScore === 'number'
    ? node.score - parentScore
    : null;
  const runtime = node.finished_at && node.started_at
    ? (new Date(node.finished_at).getTime() - new Date(node.started_at).getTime()) / 1000
    : null;
  const sinceStart = elapsedSeconds(node.started_at || node.created_at, runStart);
  return `
    <div class="stats">
      <div class="stat"><span class="stat-label">Score</span><div class="stat-value">${escapeHtml(formatNumber(node.score))}</div></div>
      <div class="stat"><span class="stat-label">Vs Baseline</span><div class="stat-value">${escapeHtml(formatNumber(baselineDelta))}</div></div>
      <div class="stat"><span class="stat-label">Vs Parent</span><div class="stat-value">${escapeHtml(formatNumber(parentDelta))}</div></div>
      <div class="stat"><span class="stat-label">Started At</span><div class="stat-value">${escapeHtml(formatSeconds(sinceStart))}</div></div>
      <div class="stat"><span class="stat-label">Node Runtime</span><div class="stat-value">${escapeHtml(formatSeconds(runtime))}</div></div>
      <div class="stat"><span class="stat-label">Thread</span><div class="stat-value">${escapeHtml(node.thread_id || 'n/a')}</div></div>
    </div>
    <div class="meta-grid">
      <div><strong>Branch</strong><div class="mono">${escapeHtml(node.branch_name)}</div></div>
      <div><strong>Parent</strong><div class="mono">${escapeHtml(node.parent_id || 'root')}</div></div>
      <div><strong>Status</strong><div class="mono">${escapeHtml(node.status)}</div></div>
      <div><strong>Test Passed</strong><div class="mono">${escapeHtml(node.test_passed)}</div></div>
      <div><strong>Surviving</strong><div class="mono">${escapeHtml(node.surviving)}</div></div>
      <div><strong>Pruned</strong><div class="mono">${escapeHtml(node.pruned)}</div></div>
      <div><strong>Commit</strong><div class="mono">${escapeHtml(node.commit_sha || node.trusted_commit || 'n/a')}</div></div>
      <div><strong>Changed Files</strong><div class="mono">${escapeHtml((node.changed_files || []).join(', ') || 'none')}</div></div>
      <div><strong>Worktree</strong><div class="mono">${escapeHtml(node.worktree_path || 'n/a')}</div></div>
      <div><strong>Metric</strong><pre class="metric-box">${escapeHtml(node.metric_text || '')}</pre></div>
    </div>
  `;
}

function renderDiff(diff) {
  const lines = String(diff || '').split('\\n');
  if (!lines.length || !diff) return '<div class="empty">No diff recorded.</div>';
  const rendered = lines.map((line) => {
    let className = 'diff-plain';
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git')) className = 'diff-meta';
    else if (line.startsWith('@@')) className = 'diff-hunk';
    else if (line.startsWith('+')) className = 'diff-add';
    else if (line.startsWith('-')) className = 'diff-del';
    return `<span class="diff-line ${className}">${escapeHtml(line)}</span>`;
  }).join('');
  return `<div class="diff-box">${rendered}</div>`;
}

function renderParsedEntries(parsed) {
  const entries = parsed?.entries || [];
  if (!entries.length) {
    return '<div class="empty">No parsed events yet.</div>';
  }
  return entries.map((entry) => {
    if (entry.kind === 'message') {
      return `<div class="log-entry message ${escapeHtml(entry.phase)}"><div class="log-kind">${escapeHtml(entry.phase)}</div><div>${escapeHtml(entry.text)}</div></div>`;
    }
    if (entry.kind === 'command') {
      return `
        <div class="log-entry command">
          <div class="log-kind">command execution</div>
          <div class="mono">${escapeHtml(entry.command || '')}</div>
          <div class="subtle">status=${escapeHtml(entry.status)} exit_code=${escapeHtml(entry.exit_code)} cwd=${escapeHtml(entry.cwd || '')}</div>
          <pre class="output">${escapeHtml(entry.output || '')}</pre>
        </div>
      `;
    }
    if (entry.kind === 'diff') {
      return `<div class="log-entry diff"><div class="log-kind">diff</div>${renderDiff(entry.diff)}</div>`;
    }
    if (entry.kind === 'turn') {
      return `<div class="log-entry turn"><div class="log-kind">turn</div><div class="mono">status=${escapeHtml(entry.status || 'unknown')} error=${escapeHtml(entry.error || 'none')}</div></div>`;
    }
    if (entry.kind === 'thread_status') {
      return `<div class="log-entry thread_status"><div class="log-kind">thread status</div><div class="mono">status=${escapeHtml(entry.status || 'unknown')} flags=${escapeHtml((entry.flags || []).join(', ') || 'none')}</div></div>`;
    }
    if (entry.kind === 'reasoning') {
      return `<div class="log-entry"><div class="log-kind">reasoning summary</div><pre class="output">${escapeHtml(JSON.stringify(entry.summary, null, 2))}</pre></div>`;
    }
    return `<div class="log-entry"><div class="log-kind">${escapeHtml(entry.kind || 'event')}</div><pre class="output">${escapeHtml(JSON.stringify(entry, null, 2))}</pre></div>`;
  }).join('');
}

async function loadParsedLog(nodeId) {
  if (parsedLogCache.has(nodeId)) return parsedLogCache.get(nodeId);
  const res = await fetch(`/api/node/${nodeId}/parsed-log`);
  const data = await res.json();
  parsedLogCache.set(nodeId, data);
  return data;
}

async function loadRawLog(nodeId) {
  if (rawLogCache.has(nodeId)) return rawLogCache.get(nodeId);
  const res = await fetch(`/api/node/${nodeId}/log`);
  const text = await res.text();
  rawLogCache.set(nodeId, text);
  return text;
}

async function renderDetails() {
  const node = getSelectedNode();
  const summary = document.getElementById('detail-summary');
  const parsedPanel = document.getElementById('tab-parsed');
  const rawPanel = document.getElementById('tab-raw');
  if (!node) {
    document.getElementById('detail-title').textContent = 'Run';
    document.getElementById('detail-status').textContent = '';
    summary.innerHTML = '<div class="empty">No node selected.</div>';
    parsedPanel.innerHTML = '';
    rawPanel.innerHTML = '';
    return;
  }
  document.getElementById('detail-title').textContent = node.node_id;
  document.getElementById('detail-status').textContent = `${node.status} | depth ${node.depth}`;
  summary.innerHTML = summaryStats(node, stateCache?.meta?.baseline_score, stateCache?.meta?.created_at) +
    `<div class="actions"><button class="action" ${READ_ONLY || stateCache?.meta?.status === 'completed' ? 'disabled' : ''} onclick="pruneNode('${node.node_id}')">Prune Node</button></div>`;

  const parsed = await loadParsedLog(node.node_id);
  parsedPanel.innerHTML = renderParsedEntries(parsed);
  if (activeTab === 'raw') {
    const rawText = await loadRawLog(node.node_id);
    rawPanel.innerHTML = `<pre class="raw-log">${escapeHtml(rawText)}</pre>`;
  } else {
    rawPanel.innerHTML = '<div class="empty">Raw log hidden until you open the tab.</div>';
  }
}

function setActiveTab(tabName) {
  activeTab = tabName;
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.tab === tabName);
  });
  document.getElementById('tab-parsed').classList.toggle('active', tabName === 'parsed');
  document.getElementById('tab-raw').classList.toggle('active', tabName === 'raw');
  if (tabName === 'raw' && selectedNodeId) {
    rawLogCache.delete(selectedNodeId);
    renderDetails();
  }
}

async function pruneNode(nodeId) {
  if (READ_ONLY) return;
  await fetch(`/api/node/${nodeId}/prune`, { method: 'POST' });
  await refresh();
}

function renderHeader() {
  const meta = stateCache?.meta || {};
  const config = stateCache?.config || {};
  document.getElementById('title').textContent = `codopt ${meta.run_id || ''}`.trim();
  document.getElementById('subtitle').textContent = config.run_root || config.repo_clone || '';
  document.getElementById('badges').innerHTML = statusBadge(meta);
  document.getElementById('summary').textContent =
    `round=${meta.current_round ?? 'n/a'} baseline=${formatNumber(meta.baseline_score)} final=${(meta.final_branches || []).length} winner=${meta.winner_node_id || 'n/a'}`;
}

async function render() {
  renderHeader();
  renderGraph();
  renderNodeList();
  await renderDetails();
}

async function refresh() {
  const res = await fetch('/api/state');
  stateCache = await res.json();
  const nodes = stateCache?.nodes || [];
  if (!selectedNodeId && nodes.length) {
    selectedNodeId = nodes[0].node_id;
  } else if (selectedNodeId && !nodes.some((node) => node.node_id === selectedNodeId)) {
    selectedNodeId = nodes[0]?.node_id || null;
  }
  parsedLogCache = new Map();
  await render();
}

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => setActiveTab(tab.dataset.tab));
});

setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>""".replace("__READ_ONLY__", "true" if read_only else "false")

    @app.get("/api/state")
    def api_state() -> dict[str, Any]:
        return _load_state(state_file)

    @app.get("/api/node/{node_id}/parsed-log", response_class=JSONResponse)
    def api_parsed_log(node_id: str) -> dict[str, Any]:
        state = _load_state(state_file)
        for node in state.get("nodes", []):
            if node["node_id"] == node_id:
                log_file = node.get("log_file")
                if log_file and Path(log_file).exists():
                    return _parse_agent_log(Path(log_file))
                return {
                    "entries": [],
                    "raw_event_count": 0,
                    "latest_diff": None,
                    "turn_status": None,
                    "token_usage": None,
                }
        raise HTTPException(status_code=404, detail="unknown node")

    @app.get("/api/node/{node_id}/log", response_class=PlainTextResponse)
    def api_log(node_id: str) -> str:
        state = _load_state(state_file)
        for node in state.get("nodes", []):
            if node["node_id"] == node_id:
                log_file = node.get("log_file")
                if log_file and Path(log_file).exists():
                    return Path(log_file).read_text(encoding="utf-8")
                return ""
        raise HTTPException(status_code=404, detail="unknown node")

    @app.post("/api/node/{node_id}/prune")
    def api_prune(node_id: str) -> dict[str, str]:
        if read_only or control_dir is None:
            raise HTTPException(status_code=409, detail="viewer is read-only")
        control_dir.mkdir(parents=True, exist_ok=True)
        (control_dir / f"{node_id}.prune").write_text("1\n", encoding="utf-8")
        return {"status": "queued"}

    return app


def start_ui_server(
    state_file: Path,
    control_dir: Path | None,
    port: int,
    *,
    open_browser: bool = True,
    read_only: bool = False,
) -> threading.Thread:
    import uvicorn

    app = create_app(state_file, control_dir, read_only=read_only)
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


def serve_ui_forever(
    state_file: Path,
    control_dir: Path | None,
    port: int,
    *,
    open_browser: bool = True,
    read_only: bool = False,
) -> None:
    start_ui_server(state_file, control_dir, port, open_browser=open_browser, read_only=read_only)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return
