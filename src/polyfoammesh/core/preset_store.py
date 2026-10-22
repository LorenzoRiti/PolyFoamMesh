# -*- coding: utf-8 -*-
"""One versioned preset file format for every configuration scope.

W7 wrote ``bl_presets.json`` with a small ad-hoc reader.  W10 needs mesh
presets too; copying that reader would create a second preset language.
``PresetStore`` owns the file format, atomic write, tolerant decode and
last-used tracking; ``BLPresetStore`` and ``MeshPresetStore`` only supply
their config codec.

File format::

    {
      "schema_version": 1,
      "kind": "bl" | "mesh",
      "last_used": "name",
      "presets": {
        "name": {"saved_at": "...", "config": {...}}
      }
    }

A missing ``kind`` is accepted as the legacy BL format (W7 files); a wrong
kind is reported and ignored rather than decoded as the wrong object.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class PresetLoadResult:
    """Outcome of reading a preset file (never raises)."""

    presets: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    last_used: str = ""


class PresetStore:
    """Generic named-config store with tolerant, versioned loading."""

    def __init__(
        self,
        path: Path | str,
        *,
        kind: str,
        decoder: Callable[[Any], tuple[Any, list[str]]],
        encoder: Callable[[Any], dict[str, Any]],
    ) -> None:
        self.path = Path(path)
        self.kind = str(kind)
        self._decoder = decoder
        self._encoder = encoder
        self._result = PresetLoadResult()
        self.load_all()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def load_all(self) -> PresetLoadResult:
        result = PresetLoadResult()
        if not self.path.exists():
            self._result = result
            return result
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result.errors.append(f"cannot read {self.path}: {exc}")
            self._result = result
            return result
        if not isinstance(raw, dict):
            result.errors.append(
                f"preset file {self.path} must contain a JSON object"
            )
            self._result = result
            return result

        file_kind = raw.get("kind")
        if file_kind is not None and str(file_kind) != self.kind:
            result.errors.append(
                f"preset file {self.path} is kind {file_kind!r}, "
                f"expected {self.kind!r}; ignored"
            )
            self._result = result
            return result

        schema = raw.get("schema_version", 0)
        try:
            schema_i = int(schema)
        except (TypeError, ValueError):
            schema_i = 0
            result.warnings.append(
                f"preset schema_version {schema!r} is not an integer; "
                "reading with the legacy adapter"
            )
        if schema_i > SCHEMA_VERSION:
            result.warnings.append(
                f"preset schema_version {schema_i} is newer than supported "
                f"{SCHEMA_VERSION}; unknown fields are ignored"
            )

        presets_raw = raw.get("presets")
        if not isinstance(presets_raw, dict):
            if presets_raw is not None:
                result.errors.append("'presets' must be an object")
            self._result = result
            return result

        for name, entry in presets_raw.items():
            if not isinstance(name, str) or not name.strip():
                result.errors.append(f"invalid preset name {name!r}")
                continue
            if not isinstance(entry, dict):
                result.errors.append(f"preset {name!r} is not an object")
                continue
            cfg_raw = entry.get("config", entry)
            try:
                cfg, warnings = self._decoder(cfg_raw)
            except Exception as exc:  # noqa: BLE001 - a preset must never crash
                result.errors.append(f"preset {name!r}: {exc}")
                continue
            for warning in warnings:
                result.warnings.append(f"preset {name!r}: {warning}")
            result.presets[name] = cfg

        last_used = str(raw.get("last_used", "") or "")
        result.last_used = last_used
        if last_used and last_used not in result.presets:
            result.warnings.append(
                f"last_used preset {last_used!r} is no longer present"
            )
        self._result = result
        return result

    @property
    def last_used(self) -> str:
        return self._result.last_used

    @property
    def warnings(self) -> list[str]:
        return list(self._result.warnings)

    @property
    def errors(self) -> list[str]:
        return list(self._result.errors)

    def names(self) -> list[str]:
        return sorted(self._result.presets, key=str.lower)

    def get(self, name: str):
        return self._result.presets.get(name)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def save(self, name: str, config) -> None:
        clean = str(name).strip()
        if not clean:
            raise ValueError("preset name must not be empty")
        if hasattr(config, "raise_if_invalid"):
            config.raise_if_invalid()
        presets = dict(self._result.presets)
        presets[clean] = config
        self._write(presets, last_used=clean)

    def delete(self, name: str) -> bool:
        presets = dict(self._result.presets)
        if name not in presets:
            return False
        del presets[name]
        last = self._result.last_used if self._result.last_used != name else ""
        self._write(presets, last_used=last)
        return True

    def set_last_used(self, name: str) -> None:
        if name and name not in self._result.presets:
            raise ValueError(f"unknown preset {name!r}")
        self._write(dict(self._result.presets), last_used=name)

    def _write(self, presets: dict[str, Any], *, last_used: str) -> None:
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "kind": self.kind,
            "last_used": last_used,
            "presets": {
                name: {
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "config": self._encoder(cfg),
                }
                for name, cfg in sorted(
                    presets.items(), key=lambda kv: kv[0].lower()
                )
            },
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            raise ValueError(f"cannot write preset file {self.path}: {exc}") from exc

        self._result = PresetLoadResult(
            presets=presets,
            warnings=list(self._result.warnings),
            errors=list(self._result.errors),
            last_used=last_used,
        )
        logger.debug("%s presets saved to %s (%d entries)",
                     self.kind, self.path, len(presets))
