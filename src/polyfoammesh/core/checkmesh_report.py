# -*- coding: utf-8 -*-
"""Structured, honest checkMesh report (W8).

``MeshQualityReport`` already parsed the scalar metrics, but "Failed 3 mesh
checks" lived only in the raw text: the GUI, the quality-fix loop and the
JSON reports could not say *which* checks failed, how many faces/cells were
involved, where their sets were written, or why a failure was accepted.

``CheckMeshReport`` is that missing model.  It is deliberately conservative:
a failed output stays failed unless a caller explicitly calls
:meth:`accept` with a reason/policy.  Parsing never invents a pass.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_F = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"


@dataclass
class CheckMeshDefect:
    """One failed check class, with its count/locations/evidence."""

    kind: str
    count: int = 1
    locations: list[str] = field(default_factory=list)
    evidence: str = ""
    value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "count": int(self.count),
            "locations": list(self.locations),
            "evidence": self.evidence,
            "value": self.value,
        }


@dataclass
class CheckMeshReport:
    """The single model consumed by GUI, JSON reports and the fix loop."""

    cells: int = 0
    faces: int = 0
    points: int = 0
    max_non_ortho: float = 0.0
    avg_non_ortho: float = 0.0
    max_skewness: float = 0.0
    avg_skewness: float = 0.0
    max_aspect_ratio: float = 0.0
    neg_cells: int = 0
    min_volume: float = 0.0
    fatal: bool = False
    mesh_ok: bool = False
    failed_checks: int = 0
    defects: dict[str, CheckMeshDefect] = field(default_factory=dict)
    acceptance: str = "fail"          # pass | fail | accepted
    acceptance_reason: str = ""
    locations: list[str] = field(default_factory=list)
    raw_output: str = ""

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------
    @classmethod
    def from_output(cls, text: str) -> "CheckMeshReport":
        text = str(text or "")
        r = cls(raw_output=text)
        r.fatal = bool(
            re.search(r"FOAM FATAL|FATAL ERROR|--> FOAM FATAL", text, re.IGNORECASE)
        )
        r.mesh_ok = bool(
            re.search(r"^\s*Mesh OK\.", text, re.IGNORECASE | re.MULTILINE)
        )

        m = re.search(r"cells:\s+(\d+)", text, re.IGNORECASE)
        if m:
            r.cells = int(m.group(1))
        m = re.search(r"faces:\s+(\d+)", text, re.IGNORECASE)
        if m:
            r.faces = int(m.group(1))
        m = re.search(r"points:\s+(\d+)", text, re.IGNORECASE)
        if m:
            r.points = int(m.group(1))

        m = re.search(
            rf"(?:Mesh non-orthogonality\s+Max:|Max non-orthogonality\s*=)\s*({_F})"
            rf"(?:[^\n]*?average\s*[:=]\s*({_F}))?",
            text, re.IGNORECASE,
        )
        if m:
            r.max_non_ortho = float(m.group(1))
            if m.group(2) is not None:
                r.avg_non_ortho = float(m.group(2))
        m = re.search(
            rf"Max skewness\s*[:=]\s*({_F})(?:[^\n]*?average\s*[:=]\s*({_F}))?",
            text, re.IGNORECASE,
        )
        if m:
            r.max_skewness = float(m.group(1))
            if m.group(2) is not None:
                r.avg_skewness = float(m.group(2))
        m = re.search(rf"Max aspect ratio\s*[:=]\s*({_F})", text, re.IGNORECASE)
        if m:
            r.max_aspect_ratio = float(m.group(1))

        m = re.search(
            r"There are\s+(\d+)\s+cells[^\n]*?negative volume"
            r"|Writing\s+(\d+)\s+cells with negative volume",
            text, re.IGNORECASE,
        )
        if m:
            r.neg_cells = int(m.group(1) or m.group(2))
        m = re.search(rf"Min volume\s*=\s*({_F})", text, re.IGNORECASE)
        if m:
            r.min_volume = float(m.group(1))

        m = re.search(r"Failed\s+(\d+)\s+mesh\s+checks?", text, re.IGNORECASE)
        if m:
            r.failed_checks = int(m.group(1))

        locations: list[str] = []
        for line in text.splitlines():
            loc = _line_locations(line)
            for item in loc:
                if item not in locations:
                    locations.append(item)
        r.locations = locations

        # Defect classes.  Keep the evidence line for the report; aggregate
        # repeated lines of the same class (count is summed, value is max).
        for line in text.splitlines():
            defect = _classify_line(line)
            if defect is None:
                continue
            defect.locations = _line_locations(line)
            current = r.defects.get(defect.kind)
            if current is None:
                r.defects[defect.kind] = defect
            else:
                # Non-orthogonality is reported both as a max and as a count
                # of errors; keep the largest count instead of summing the
                # same check's echo lines.
                if defect.kind == "non_orthogonality":
                    current.count = max(current.count, defect.count)
                else:
                    current.count += defect.count
                if defect.value is not None:
                    current.value = max(
                        current.value if current.value is not None else defect.value,
                        defect.value,
                    )
                for loc in defect.locations:
                    if loc not in current.locations:
                        current.locations.append(loc)
                if defect.evidence and defect.evidence not in current.evidence:
                    current.evidence = (current.evidence + " | " + defect.evidence).strip(" |")

        # A failed check with no recognised line: keep an explicit generic
        # defect so the report can never say "Failed 3" with zero reasons.
        if r.failed_checks > len(r.defects) and r.failed_checks > 0:
            r.defects.setdefault(
                "other",
                CheckMeshDefect(
                    kind="other",
                    count=max(1, r.failed_checks - len(r.defects)),
                    evidence="checkMesh reported a failed check not classified",
                ),
            )

        if r.fatal:
            r.acceptance = "fail"
            r.acceptance_reason = "FOAM FATAL"
        elif r.mesh_ok and r.failed_checks == 0 and not r.defects:
            r.acceptance = "pass"
            r.acceptance_reason = "Mesh OK"
        else:
            r.acceptance = "fail"
            r.acceptance_reason = _fail_reason(r)
        return r

    # ------------------------------------------------------------------
    # Acceptance policy
    # ------------------------------------------------------------------
    @property
    def accepted(self) -> bool:
        return self.acceptance == "accepted"

    @property
    def passed(self) -> bool:
        return self.acceptance in ("pass", "accepted")

    @property
    def failed(self) -> bool:
        return not self.passed

    def accept(self, reason: str, *, policy: str = "documented") -> None:
        """Explicitly accept a failure with a written justification.

        Only a non-empty reason is accepted.  A caller that does not call
        this leaves the report failed, so a bare ``Failed`` can never become
        silent success.
        """
        clean = str(reason or "").strip()
        if not clean:
            raise ValueError("an accepted failure needs a non-empty reason")
        if self.fatal:
            raise ValueError("a fatal checkMesh output cannot be accepted")
        self.acceptance = "accepted"
        self.acceptance_reason = f"{clean} (policy={policy})"

    def cause(self) -> str:
        if not self.defects:
            return self.acceptance_reason or "no defects"
        return "; ".join(
            f"{kind} x{d.count}" + (f" (max {d.value:g})" if d.value is not None else "")
            for kind, d in sorted(self.defects.items())
        )

    def summary(self) -> str:
        if self.passed:
            return f"checkMesh {self.acceptance}: {self.cells} cells"
        return (
            f"checkMesh FAILED ({self.failed_checks} checks): "
            f"{self.cause()}"
        )

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "metrics": {
                "cells": self.cells,
                "faces": self.faces,
                "points": self.points,
                "max_non_ortho": self.max_non_ortho,
                "avg_non_ortho": self.avg_non_ortho,
                "max_skewness": self.max_skewness,
                "avg_skewness": self.avg_skewness,
                "max_aspect_ratio": self.max_aspect_ratio,
                "neg_cells": self.neg_cells,
                "min_volume": self.min_volume,
            },
            "defects": {
                kind: defect.to_dict() for kind, defect in self.defects.items()
            },
            "failed_checks": self.failed_checks,
            "mesh_ok": self.mesh_ok,
            "fatal": self.fatal,
            "passed": self.passed,
            "acceptance": self.acceptance,
            "acceptance_reason": self.acceptance_reason,
            "cause": self.cause(),
            "locations": list(self.locations),
        }
        if include_raw:
            data["raw_output"] = self.raw_output
        return data


def _fail_reason(report: CheckMeshReport) -> str:
    if report.failed_checks:
        return f"Failed {report.failed_checks} mesh checks: {report.cause()}"
    if report.fatal:
        return "FOAM FATAL"
    return "checkMesh did not print Mesh OK"


def _line_locations(line: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(
        r"(?:to set|set)\s+[\"']?([A-Za-z0-9_.:+-]+)", line, re.IGNORECASE,
    ):
        value = match.group(1)
        if value.lower() not in ("is", "the", "a", "of"):
            out.append(f"set:{value}")
    for match in re.finditer(
        r"(?:at position|near|around)\s*\(([^)]+)\)", line, re.IGNORECASE,
    ):
        out.append(f"at:({match.group(1).strip()})")
    return out


def _number(line: str, *patterns: str) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                pass
    return None


def _classify_line(line: str) -> CheckMeshDefect | None:
    low = line.lower()
    if not low.strip() or "failed" in low and "mesh checks" in low:
        return None
    # These are section headers, not failures ("Checking patch topology for
    # multiply connected surfaces..." appears on a healthy mesh too).
    if low.lstrip().startswith("checking") or "for multiply connected" in low:
        return None
    # Healthy checkMesh lines end in "OK."; they are evidence of a passed
    # check, not a defect (e.g. "Max cell openness = 3.2e-16 OK.").
    if (
        re.search(r"\bOK\b", line)
        and not any(token in low for token in ("fail", "error", "incorrect", "highly skew"))
    ):
        return None
    evidence = line.strip()[:300]

    # Face pyramids (R1: "39 faces are incorrectly oriented").
    if "face pyramids" in low or (
        "incorrectly oriented" in low and "face" in low
    ):
        count = _number(
            line,
            r"(\d+)\s+faces?\s+(?:are\s+)?incorrectly",
            r"face pyramids[^\d]*(\d+)",
            r"(\d+)\s+face",
        ) or 1
        return CheckMeshDefect("face_pyramids", count, evidence=evidence)

    if "wrongly oriented" in low or "wrongorientedfaces" in low:
        count = _number(line, r"(\d+)\s+(?:faces|cells)", r":\s*(\d+)") or 1
        return CheckMeshDefect("wrong_orientation", count, evidence=evidence)

    # Skewness: "Max skewness = 170.893, 5 highly skew faces".
    if "skewness" in low or "highly skew" in low:
        count = _number(line, r"(\d+)\s+highly\s+skew", r"(\d+)\s+skew")
        value = None
        match = re.search(rf"skewness\s*[:=]\s*({_F})", line, re.IGNORECASE)
        if match:
            value = float(match.group(1))
        if value is not None and value <= 4.0 and (count is None or count == 0):
            return None
        return CheckMeshDefect(
            "skewness", count or 1, evidence=evidence, value=value,
        )

    # Non-orthogonality: checkMesh reports the max, not a face count.
    if "non-orthogonality" in low or "nonortho" in low:
        value = None
        match = re.search(
            rf"non-orthogonality\s+Max:\s*({_F})|Max non-orthogonality\s*=\s*({_F})",
            line, re.IGNORECASE,
        )
        if match:
            value = float(match.group(1) or match.group(2))
        if value is not None and value <= 70.0:
            return None
        if value is None and ("ok" in low or "check" in low):
            return None
        count = _number(
            line, r"Number of non-orthogonality errors:\s*(\d+)",
        ) or 1
        return CheckMeshDefect(
            "non_orthogonality", count, evidence=evidence, value=value,
        )

    if "aspect ratio" in low and ("high" in low or "cells" in low):
        value = None
        match = re.search(rf"aspect ratio\s*[:=]\s*({_F})", line, re.IGNORECASE)
        if match:
            value = float(match.group(1))
        count = _number(line, r"number of cells\s+(\d+)", r"(\d+)\s+cells?") or 1
        return CheckMeshDefect(
            "aspect_ratio", count, evidence=evidence, value=value,
        )

    if "negative volume" in low:
        count = _number(line, r"(\d+)\s+cells?", r":\s*(\d+)") or 1
        return CheckMeshDefect("negative_volume", count, evidence=evidence)

    if "multiply connected" in low:
        count = _number(line, r"(\d+)\s+faces?", r":\s*(\d+)") or 1
        return CheckMeshDefect("multiply_connected", count, evidence=evidence)

    if "open cells" in low or "open cell" in low:
        count = _number(line, r"(\d+)\s+cells?", r":\s*(\d+)") or 1
        return CheckMeshDefect("open_cells", count, evidence=evidence)

    if "boundary openness" in low or "openness" in low and "max" in low:
        return CheckMeshDefect("boundary_openness", 1, evidence=evidence)

    if "zero or negative face area" in low or "negative face area" in low:
        count = _number(line, r"(\d+)\s+faces?", r":\s*(\d+)") or 1
        return CheckMeshDefect("face_area", count, evidence=evidence)

    if "not aligned" in low or "edge alignment" in low:
        count = _number(line, r"(\d+)\s+edges?", r":\s*(\d+)") or 1
        return CheckMeshDefect("edge_alignment", count, evidence=evidence)

    if "small volume" in low or "small cells" in low:
        count = _number(line, r"(\d+)\s+cells?", r":\s*(\d+)") or 1
        return CheckMeshDefect("small_volume", count, evidence=evidence)

    return None
