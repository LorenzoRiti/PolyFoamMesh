"""Join hard-wrapped Markdown lines so GitHub release notes use the full width.

GitHub renders a single newline inside a release body as a line break (unlike
a .md file in the repo), so text wrapped at ~80 columns shows up as a narrow,
ragged column. This joins the continuation lines of each paragraph and list
item into one line and leaves headings, tables, code fences, blank lines and
list markers untouched.

Usage: python unwrap_md.py FILE   (rewrites FILE in place)
"""
import re
import sys

LIST_RE = re.compile(r"^\s*([-*+]|\d+[.)])\s+")
HEAD_RE = re.compile(r"^\s{0,3}#{1,6}\s")
TABLE_RE = re.compile(r"^\s*\|")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def unwrap(text: str) -> str:
    out: list[str] = []
    in_fence = False
    prev_is_text = False  # previous output line can take a continuation
    for line in text.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            out.append(line)
            prev_is_text = False
            continue
        if in_fence or not line.strip():
            out.append(line)
            prev_is_text = False
            continue
        if HEAD_RE.match(line) or TABLE_RE.match(line):
            out.append(line)
            prev_is_text = False
            continue
        if LIST_RE.match(line):
            out.append(line.rstrip())
            prev_is_text = True
            continue
        if prev_is_text:
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line.rstrip())
            prev_is_text = True
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(unwrap(src))
