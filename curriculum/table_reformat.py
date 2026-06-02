"""table_reformat — convert ugly markdown tables to clean prose-bullets.

Most "tables" in the textbook output are 2-3 column lookups (term→meaning,
defect→cause→prevention) that look cheap and misalign when rendered. This
module post-processes the final textbook to convert them into:

  - 2 columns → `**col1** — col2`
  - 3 columns → `**col1** — col2. *<col3 header>:* col3`
  - 4+ columns → leave as-is (genuine tabular data deserves a table)

Padding is normalised on any table that's kept so columns align cleanly.
"""

import re
import logging

log = logging.getLogger(__name__)

_TABLE_BLOCK = re.compile(
    r'(?m)^(\|.+\|)\s*\n^(\|[\s\-:|]+\|)\s*\n((?:\|.+\|\s*\n?)+)'
)


def _split_row(line: str) -> list[str]:
    parts = line.strip().strip('|').split('|')
    return [p.strip() for p in parts]


def _clean_cell(s: str) -> str:
    # Strip surrounding bold markers — we'll re-add them on lead column
    s = s.strip()
    if s.startswith('**') and s.endswith('**') and len(s) >= 4:
        s = s[2:-2].strip()
    return s


def reformat_tables(md: str) -> tuple[str, int]:
    """Return (modified_markdown, tables_converted_count)."""
    converted = 0

    def replace(m):
        nonlocal converted
        header_line, sep_line, body_block = m.group(1), m.group(2), m.group(3)
        headers = _split_row(header_line)
        col_count = len(headers)

        rows = []
        for line in body_block.strip().split('\n'):
            if line.strip().startswith('|'):
                rows.append(_split_row(line))

        # 4+ columns: render as "cards" — one block per row, lead cell as
        # heading, remaining fields as bullets. No |---| anywhere.
        if col_count >= 4:
            converted += 1
            out = []
            for r in rows:
                if not any(c.strip() for c in r):
                    continue
                lead = _clean_cell(r[0]) if r else ""
                out.append(f"\n**{lead}**")
                for i in range(1, min(len(r), col_count)):
                    label = _clean_cell(headers[i]).rstrip(':')
                    val = r[i].strip()
                    if val:
                        out.append(f"- *{label}:* {val}")
            return '\n'.join(out) + '\n'

        # 1 column: just bullets
        if col_count == 1:
            converted += 1
            return '\n'.join(f"- {_clean_cell(r[0])}" for r in rows) + '\n'

        # 2 columns: definition-list style
        if col_count == 2:
            converted += 1
            out = []
            for r in rows:
                if len(r) < 2:
                    continue
                lead, rest = _clean_cell(r[0]), r[1].strip()
                if not lead and not rest:
                    continue
                out.append(f"- **{lead}** — {rest}")
            return '\n'.join(out) + '\n'

        # 3 columns: lead — second. *<3rd header>:* third
        if col_count == 3:
            converted += 1
            third_label = _clean_cell(headers[2]).rstrip(':')
            out = []
            for r in rows:
                if len(r) < 3:
                    continue
                lead = _clean_cell(r[0])
                mid = r[1].strip()
                third = r[2].strip()
                if not (lead or mid or third):
                    continue
                # Trailing period management
                if mid and not mid.endswith(('.', '!', '?', ';')):
                    mid = mid + '.'
                if third:
                    out.append(f"- **{lead}** — {mid} *{third_label}:* {third}")
                else:
                    out.append(f"- **{lead}** — {mid}")
            return '\n'.join(out) + '\n'

        # Fallback: untouched
        return m.group(0)

    new_md = _TABLE_BLOCK.sub(replace, md)
    return new_md, converted


def _normalise_table(headers: list[str], rows: list[list[str]]) -> str:
    """Pad columns to consistent width so the table at least renders aligned."""
    col_count = len(headers)
    widths = [len(h) for h in headers]
    for r in rows:
        for i in range(min(len(r), col_count)):
            widths[i] = max(widths[i], len(r[i]))

    def fmt_row(cells: list[str]) -> str:
        padded = [c.ljust(widths[i]) for i, c in enumerate(cells[:col_count])]
        while len(padded) < col_count:
            padded.append(' ' * widths[len(padded)])
        return '| ' + ' | '.join(padded) + ' |'

    sep = '| ' + ' | '.join('-' * w for w in widths) + ' |'
    lines = [fmt_row(headers), sep]
    for r in rows:
        lines.append(fmt_row(r))
    return '\n'.join(lines) + '\n'
