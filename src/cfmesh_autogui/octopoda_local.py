"""Lightweight local Octopoda runtime — zero external dependencies."""
from __future__ import annotations
import json, os, datetime
from datetime import timezone
from pathlib import Path

_OCTO_DIR = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "cfmesh-autogui" / "octopoda"
_MAX_EVENTS = 1000  # trim events.jsonl to this many lines after each append


class OctopodaRuntime:
    def __init__(self, app: str = "cfmesh-autogui"):
        self._app = app
        _OCTO_DIR.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("\\", "_").replace(" ", "_")
        return _OCTO_DIR / f"{safe}.json"

    def remember(self, key: str, value: dict) -> None:
        p = self._path(key)
        p.write_text(json.dumps({"key": key, "value": value, "ts": datetime.datetime.now(timezone.utc).isoformat()}, indent=2))

    def recall(self, key: str) -> dict | None:
        p = self._path(key)
        if p.exists():
            return json.loads(p.read_text())["value"]
        return None

    def log_event(self, agent: str, step: str, details: str) -> None:
        log = _OCTO_DIR / "events.jsonl"
        with open(log, "a") as f:
            f.write(json.dumps({"agent": agent, "step": step, "details": details, "ts": datetime.datetime.now(timezone.utc).isoformat()}) + "\n")
        self._trim_events(log)

    def _trim_events(self, log: Path) -> None:
        """Keep only the most recent ``_MAX_EVENTS`` lines."""
        try:
            lines = log.read_text().strip().splitlines()
            if len(lines) > _MAX_EVENTS:
                log.write_text("\n".join(lines[-_MAX_EVENTS:]) + "\n")
        except OSError:
            pass

    def detect_loop(self, agent: str, step: str, max_repeat: int = 3) -> bool:
        log = _OCTO_DIR / "events.jsonl"
        if not log.exists():
            return False
        lines = log.read_text().strip().splitlines()
        # Only scan the most recent N*2 lines to avoid O(n) over unbounded history
        tail = lines[-(_MAX_EVENTS * 2):] if len(lines) > _MAX_EVENTS * 2 else lines
        events = [json.loads(l) for l in tail if l]
        count = sum(1 for e in events if e.get("agent") == agent and e.get("step") == step)
        return count >= max_repeat

octo = OctopodaRuntime()
