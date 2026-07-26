"""Diagnose binary OpenFOAM file format written by cartesianMesh."""
import shutil, subprocess, sys, time, re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from cfmesh_autogui.config import OFConfig
from cfmesh_autogui.core.meshdict_gen import write_meshdict
import trimesh

WORK = Path("C:/cfmesh_bench/diag")


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    stl_path = WORK / "geom.stl"
    trimesh.creation.cylinder(radius=0.5, height=2.0, sections=64).export(str(stl_path))

    cfg = OFConfig()
    env_quoted = f"source {cfg._quoted_linux_path(cfg.env_script)} 2>/dev/null"

    for label, fmt, comp in [
        ("ascii_off", "ascii", "off"),
        ("binary_on", "binary", "on"),
    ]:
        case_dir = WORK / f"case_{label}"
        tri = case_dir / "constant" / "triSurface"
        sysd = case_dir / "system"
        tri.mkdir(parents=True)
        sysd.mkdir(parents=True)
        shutil.copy2(stl_path, tri / "surface.stl")
        (sysd / "controlDict").write_text(
            "FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }\n"
            "application cartesianMesh;\nstartFrom startTime; startTime 0;\n"
            "stopAt endTime; endTime 1000;\ndeltaT 1;\n"
            "writeControl timeStep; writeInterval 1; purgeWrite 0;\n"
            f"writeFormat {fmt};\nwritePrecision 6; writeCompression {comp};\n"
            "timeFormat general; timePrecision 6;\nrunTimeModifiable true;\n",
            encoding="ascii",
        )
        write_meshdict(case_dir, 0.05, 0.01,
                       surface_file="constant/triSurface/surface.stl",
                       patch_names=["wall"])

        linux_case = cfg._quoted_linux_path(case_dir)
        t0 = time.time()
        r = subprocess.run(
            cfg._build_wsl_cmd(f"{env_quoted}; cd {linux_case} && cartesianMesh > log.mesh 2>&1"),
            capture_output=True, text=True, timeout=120,
        )
        elapsed = time.time() - t0
        print(f"\n=== {label} === rc={r.returncode} time={elapsed:.1f}s")

        poly_dir = case_dir / "constant" / "polyMesh"
        for fname in ("points", "faces", "owner", "neighbour", "boundary"):
            fp = poly_dir / fname
            if not fp.exists():
                print(f"  {fname}: MISSING")
                continue
            raw = fp.read_bytes()
            is_gzip = raw[:2] == b"\x1f\x8b"
            text_header = raw[:2048].decode("ascii", errors="replace")
            fmt_in_header = "binary;" in text_header[:512]
            n_cells = re.search(r"nCells:\s*(\d+)", text_header)

            print(f"  {fname}: {len(raw):>8,} B  gzip={is_gzip}  "
                  f"binary={fmt_in_header}  nCells={n_cells.group(1) if n_cells else '?'}")

        # Try of_reader parsing
        from cfmesh_autogui.core.of_reader import of_ncells_from_header, of_label_list
        owner = poly_dir / "owner"
        n1 = of_ncells_from_header(owner)
        nl = of_label_list(owner)
        print(f"  of_ncells_from_header={n1}  of_label_list(len={len(nl)})  max+1={max(nl)+1 if nl else 0}")

        # Debug binary parsing
        if fmt == "binary":
            raw = owner.read_bytes()
            text = raw.decode("ascii", errors="replace")
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
            text = re.sub(r"//[^\n]*", "", text)
            header_end = text.find("}")
            binary_body = raw[header_end + 1:]
            m = re.search(rb"(\d+)\s*\(", binary_body)
            if m:
                count = int(m.group(1))
                print(f"  Declared count in body: {count}")
                start = m.end()
                if start < len(binary_body) and binary_body[start:start + 1] == b"(":
                    start += 1
                data = binary_body[start:start + count * 4]
                if len(data) >= count * 4:
                    import struct
                    vals = list(struct.unpack(f"<{count}i", data[:count * 4]))
                    print(f"  Parsed {len(vals)} ints, max+1={max(vals)+1}")
                else:
                    print(f"  Not enough data: need {count*4} have {len(data)}")
                    print(f"  Next 100 bytes hex: {binary_body[start:start+100].hex()}")
            else:
                print(f"  No count pattern found in binary body")
                print(f"  Body start (repr): {binary_body[:200]!r}")


if __name__ == "__main__":
    main()
