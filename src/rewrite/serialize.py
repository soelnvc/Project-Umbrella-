"""Turn a parsed file back into bytes in its original format, as UTF-8."""
from __future__ import annotations

import csv
import io

from src.ingest import jsontree
from src.ingest.parsers import ParsedFile


def serialize(parsed: ParsedFile) -> bytes:
    if parsed.fmt in ("csv", "tsv"):
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=parsed.delimiter, lineterminator=parsed.newline)
        if parsed.header:
            writer.writerow(parsed.header)
        writer.writerows(parsed.rows)
        text = buf.getvalue()
        if not parsed.trailing_newline and text.endswith(parsed.newline):
            text = text[: -len(parsed.newline)]
    elif parsed.fmt in ("json", "fhir"):
        text = jsontree.dumps(parsed.data, parsed.pretty)
        if parsed.trailing_newline:
            text += parsed.newline
    else:
        text = parsed.text
    data = text.encode("utf-8")
    return b"\xef\xbb\xbf" + data if parsed.bom else data
