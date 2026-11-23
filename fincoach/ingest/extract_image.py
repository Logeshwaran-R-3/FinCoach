"""Image extraction: OCR with Pillow + pytesseract, then line/screenshot parse."""

from __future__ import annotations

import io
import re
from datetime import date

from fincoach.ingest.amounts import MARKED_RE, direction_hint, find_amount
from fincoach.ingest.dates import find_date
from fincoach.ingest.errors import IngestError
from fincoach.ingest.extract_pdf import txns_from_lines


def _screenshot_description(lines: list, value: str) -> str:
    """Best human line for a payment screenshot: first line with letters
    that is not just the amount or the date."""
    plain = value.lstrip("+-").replace(",", "")
    for ln in lines:
        if not re.search(r"[A-Za-z]", ln):
            continue
        nospace = re.sub(r"\s+", "", ln).replace(",", "")
        if plain and plain in nospace and len(nospace) < len(plain) + 8:
            continue
        if find_date(ln) and len(ln) < 16:
            continue
        return ln[:120]
    return "Screenshot payment"


def txns_from_screenshot(text: str) -> list:
    """One transaction from payment-app OCR (Paytm/GPay layout).

    Screenshots spread the amount, payee, and date across lines, so the
    same-line statement parser finds nothing; aggregate instead.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    iso = find_date(text) or date.today().isoformat()
    value = None
    m = MARKED_RE.search(text)
    if m:
        value = m.group(1).replace(",", "")
    if value is None:
        verb_lines = [ln for ln in lines if direction_hint(ln)]
        for ln in verb_lines + [ln for ln in lines if ln not in verb_lines]:
            found = find_amount(ln)
            if found:
                value = found[0]
                break
    if value is None:
        return []
    txn = {"date": iso,
           "description": _screenshot_description(lines, value),
           "amount": value}
    hint = direction_hint(text)
    if hint:
        txn["dr_cr"] = hint
    return [txn]


def extract_image(data: bytes, filename: str = "") -> list:
    try:
        from PIL import Image
    except ImportError as exc:
        raise IngestError(
            "image extraction needs the 'Pillow' pip package; run: pip install Pillow"
        ) from exc
    try:
        import pytesseract
    except ImportError as exc:
        raise IngestError(
            "image extraction needs the 'pytesseract' pip package;"
            " run: pip install pytesseract (plus a Tesseract binary)"
        ) from exc
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise IngestError(
            f"{filename!r} is not a readable image (PNG/JPG expected)."
        ) from exc
    try:
        text = pytesseract.image_to_string(img)
    except Exception as exc:
        raise IngestError(
            f"OCR could not read {filename!r}: {exc}. Install the Tesseract"
            " program (the 'pytesseract' pip package alone is not enough)."
        ) from exc
    lines = (text or "").splitlines()
    txns = txns_from_lines(lines)
    if txns:
        return txns
    return txns_from_screenshot(text or "")
