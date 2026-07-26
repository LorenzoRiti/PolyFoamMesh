"""Dump OpenFOAM binary file header to diagnose format detection."""
from pathlib import Path
import re

p = Path("C:/cfmesh_bench/diag/case_binary_on/constant/polyMesh/owner")
raw = p.read_bytes()
text = raw[:1024].decode("ascii", errors="replace")

m = re.search(r"format\s+(\w+)", text)
print(f"Format declaration: {m.group(1) if m else 'NOT FOUND'}")

# Check exact bytes around "format"
idx = text.find("format")
if idx >= 0:
    print(f"Context around 'format': {repr(text[idx:idx+50])}")

# Check for binary substring
if "binary;" in text[:512]:
    print("'binary;' FOUND in header")
elif '"binary"' in text[:512]:
    print('"binary" FOUND in header')
else:
    print("NEITHER 'binary;' nor 'binary' found in header")
    print(text[:500])
