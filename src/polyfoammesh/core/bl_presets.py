# -*- coding: utf-8 -*-
"""BL presets, stored by the shared :class:`PresetStore`.

A preset is a snapshot of :class:`polyfoammesh.core.bl_config.BLConfig`, not
a second settings language.  The file format, tolerant loading and atomic
write are owned by ``core.preset_store``; W10 added ``MeshPresetStore`` on
the same format, so there is one preset language.
"""
from __future__ import annotations

from pathlib import Path

from polyfoammesh._paths import app_data_dir
from polyfoammesh.core.bl_config import BLConfig
from polyfoammesh.core.preset_store import (
    PresetLoadResult,
    PresetStore,
)

DEFAULT_PRESET_FILENAME = "bl_presets.json"


class BLPresetStore(PresetStore):
    """Load/save/delete named BL presets with a versioned schema."""

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = app_data_dir() / "presets" / DEFAULT_PRESET_FILENAME
        super().__init__(
            path,
            kind="bl",
            decoder=BLConfig.from_mapping_tolerant,
            encoder=lambda cfg: cfg.to_mapping(),
        )

    def get(self, name: str) -> BLConfig | None:
        return super().get(name)


__all__ = ["BLPresetStore", "PresetLoadResult", "DEFAULT_PRESET_FILENAME"]
