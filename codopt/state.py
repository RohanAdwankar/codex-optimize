from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import NodeRecord, RunConfig, RunEvent, utc_now


class StateStore:
    def __init__(self, config: RunConfig) -> None:
        self._config = config
        self._state_file = Path(config.state_file)
        self._event_log = Path(config.event_log)
        self._lock = threading.Lock()
        self._nodes: dict[str, NodeRecord] = {}
        self._events: list[RunEvent] = []
        self._meta: dict[str, Any] = {
            "run_id": config.run_id,
            "status": "initializing",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "current_round": 0,
            "ui_port": config.ui_port,
            "ui_url": f"http://127.0.0.1:{config.ui_port}",
            "final_branches": [],
            "baseline_score": None,
            "winner_node_id": None,
        }
        self.flush()

    def add_node(self, node: NodeRecord) -> None:
        with self._lock:
            self._nodes[node.node_id] = node
            self._meta["updated_at"] = utc_now()
            self.flush_locked()

    def update_node(self, node_id: str, **changes: Any) -> None:
        with self._lock:
            node = self._nodes[node_id]
            for key, value in changes.items():
                setattr(node, key, value)
            self._meta["updated_at"] = utc_now()
            self.flush_locked()

    def set_meta(self, **changes: Any) -> None:
        with self._lock:
            self._meta.update(changes)
            self._meta["updated_at"] = utc_now()
            self.flush_locked()

    def add_event(self, event: RunEvent) -> None:
        with self._lock:
            self._events.append(event)
            self._event_log.parent.mkdir(parents=True, exist_ok=True)
            with self._event_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict()) + "\n")
            self._meta["updated_at"] = utc_now()
            self.flush_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self.snapshot_locked()

    def flush(self) -> None:
        with self._lock:
            self.flush_locked()

    def snapshot_locked(self) -> dict[str, Any]:
        return {
            "config": self._config.to_dict(),
            "meta": dict(self._meta),
            "nodes": [node.to_dict() for node in sorted(self._nodes.values(), key=lambda n: (n.depth, n.node_id))],
            "events": [event.to_dict() for event in self._events[-100:]],
        }

    def flush_locked(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(self.snapshot_locked(), indent=2) + "\n",
            encoding="utf-8",
        )
