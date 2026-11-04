"""Byte-nivå JSON-hjelpere for store OSDM-filer (unngår å bygge hele objekttreet)."""
import decimal
import io
import json

import ijson
from fastapi import HTTPException


def check_fare_delivery(content: bytes) -> str | None:
    """Rask struktursjekk uten full parsing. Returnerer feilmelding, eller None hvis OK."""
    try:
        for prefix, event, _ in ijson.parse(io.BytesIO(content)):
            if prefix == "fareDelivery.fareStructure" and event == "start_map":
                return None
    except Exception:
        return "Filen er ikke gyldig JSON"
    return "Filen mangler fareDelivery.fareStructure"


def require_fare_delivery(content: bytes) -> None:
    """Som check_fare_delivery, men kaster HTTP 400 ved feil."""
    error = check_fare_delivery(content)
    if error:
        raise HTTPException(status_code=400, detail=error)


def read_delivery(content: bytes) -> dict:
    """Les `fareDelivery.delivery` (lite objekt) uten å parse resten av filen."""
    for item in ijson.items(io.BytesIO(content), "fareDelivery.delivery"):
        return item
    return {}


def _find_json_value_end(content: bytes, start: int) -> int:
    """Return end index of the JSON value whose first byte is at start."""
    c = content[start]
    if c in (ord('{'), ord('[')):
        depth = 0
        i = start
        in_string = False
        while i < len(content):
            b = content[i]
            if in_string:
                if b == ord('\\'):
                    i += 2
                    continue
                if b == ord('"'):
                    in_string = False
            else:
                if b == ord('"'):
                    in_string = True
                elif b in (ord('{'), ord('[')):
                    depth += 1
                elif b in (ord('}'), ord(']')):
                    depth -= 1
                    if depth == 0:
                        return i + 1
            i += 1
    elif c == ord('"'):
        i = start + 1
        while i < len(content):
            b = content[i]
            if b == ord('\\'):
                i += 2
                continue
            if b == ord('"'):
                return i + 1
            i += 1
    else:
        i = start
        while i < len(content) and content[i:i+1] not in (b',', b'}', b']', b' ', b'\n', b'\r', b'\t'):
            i += 1
        return i
    return len(content)


def _find_section_span(content: bytes, key: str) -> tuple[int, int]:
    """
    Find (value_start, value_end) byte offsets for the first "key":VALUE in content.
    Raises KeyError if not found.
    """
    search = f'"{key}":'.encode()
    idx = content.find(search)
    if idx == -1:
        raise KeyError(key)
    val_start = idx + len(search)
    while val_start < len(content) and content[val_start:val_start+1] in (b' ', b'\t', b'\n', b'\r'):
        val_start += 1
    return val_start, _find_json_value_end(content, val_start)


def _apply_byte_replacements(content: bytes, replacements: list) -> bytes:
    """Apply multiple (start, end, new_bytes) replacements at once (must be non-overlapping)."""
    replacements = sorted(replacements)
    parts: list[bytes] = []
    pos = 0
    for start, end, new_bytes in replacements:
        parts.append(content[pos:start])
        parts.append(new_bytes)
        pos = end
    parts.append(content[pos:])
    return b"".join(parts)


def _append_to_json_array(content: bytes, key: str, new_items: list) -> bytes:
    """Append new_items to a JSON array identified by key in content bytes."""
    if not new_items:
        return content
    val_start, val_end = _find_section_span(content, key)
    new_bytes = (b", " + b", ".join(json.dumps(item, ensure_ascii=False, default=_ijson_default).encode() for item in new_items))
    return content[:val_end - 1] + new_bytes + content[val_end - 1:]


def _ijson_default(obj):
    if isinstance(obj, decimal.Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")
