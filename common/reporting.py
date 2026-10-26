"""Small dependency-free Markdown -> HTML renderer and shared report page template."""
import html
import re
from pathlib import Path

_CSS = """
:root{--bg:#0d0f11;--surface:#1a1a19;--ink:#f0efec;--muted:#a2a59f;--line:#2c2c2a;--blue:#3987e5;--good:#3fbf3f;--bad:#e66767}
@media (prefers-color-scheme: light){:root{--bg:#fafaf8;--surface:#fff;--ink:#1a1a19;--muted:#5d605a;--line:#dcdcd6}}
body{background:var(--bg);color:var(--ink);font-family:"IBM Plex Sans",system-ui,sans-serif;margin:0;padding:32px 16px;line-height:1.6}
.container{max-width:1000px;margin:0 auto}
h1,h2,h3{font-weight:600}h1{border-bottom:1px solid var(--line);padding-bottom:12px}
table{width:100%;border-collapse:collapse;margin:16px 0;background:var(--surface);font-size:13.5px;display:block;overflow-x:auto}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-size:12px;text-transform:uppercase}
code{font-family:"IBM Plex Mono",monospace;background:var(--surface);padding:1px 5px;border-radius:4px;font-size:.92em}
pre{background:var(--surface);padding:12px;border-radius:8px;overflow-x:auto}pre code{padding:0}
blockquote{border-left:3px solid var(--blue);margin:12px 0;padding:2px 14px;color:var(--muted)}
a{color:var(--blue)}hr{border:0;border-top:1px solid var(--line);margin:24px 0}
.pass{color:var(--good);font-weight:600}.fail{color:var(--bad);font-weight:600}
"""


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"\bPASS\b", '<span class="pass">PASS</span>', text)
    text = re.sub(r"\bFAIL\b", '<span class="fail">FAIL</span>', text)
    return text


def md_to_html_body(md: str) -> str:
    out: list[str] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            j = i + 1
            block = []
            while j < len(lines) and not lines[j].startswith("```"):
                block.append(lines[j])
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(block)) + "</code></pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            anchor = re.sub(r"[^a-z0-9]+", "-", m.group(2).lower()).strip("-")
            out.append(f'<h{lvl} id="{anchor}">{_inline(m.group(2))}</h{lvl}>')
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|\s*:?-{2,}", lines[i + 1]):
            header = [c.strip() for c in line.strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            t = "<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr></thead><tbody>"
            for r in rows:
                t += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
            out.append(t + "</tbody></table>")
            i = j
            continue
        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ol>")
            continue
        if line.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].startswith(">"):
                quote.append(lines[i].lstrip("> "))
                i += 1
            out.append("<blockquote>" + _inline(" ".join(quote)) + "</blockquote>")
            continue
        if line.strip() in ("---", "***"):
            out.append("<hr>")
            i += 1
            continue
        if not line.strip():
            i += 1
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||```|>|\s*[-*]\s|\s*\d+\.\s|---)", lines[i]):
            para.append(lines[i])
            i += 1
        out.append("<p>" + _inline(" ".join(p.strip() for p in para)) + "</p>")
    return "\n".join(out)


def write_report(run_dir: Path, stem: str, md: str, title: str) -> tuple[Path, Path]:
    """Write <stem>.md and <stem>.html (rendered from the same Markdown, so they never diverge)."""
    md_path = run_dir / f"{stem}.md"
    html_path = run_dir / f"{stem}.html"
    md_path.write_text(md, encoding="utf-8")
    page = (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f'<body><div class="container">{md_to_html_body(md)}</div></body></html>'
    )
    html_path.write_text(page, encoding="utf-8")
    return md_path, html_path
