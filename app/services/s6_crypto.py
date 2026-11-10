"""S6 Comms Crypto — shackle code, one-time pad, and trench code logic + PDF builders.

All randomness uses the stdlib secrets module (cryptographically secure). All
functions are pure where possible so the encode/decode helpers stay testable.
"""
from __future__ import annotations

import io
import secrets

# ─── Shackle code ────────────────────────────────────────────────────────────

# 10-letter isograms (real words, no repeated letters). The generator filters to
# valid ones defensively, so a bad entry here is simply skipped.
SHACKLE_WORDS = [
    "BLACKHORSE", "SCUBADIVER", "BLACKSMITH", "BACKGROUND", "PLAYWRIGHT",
    "BANKRUPTCY", "PALINDROME", "TAMBOURINE", "TRAMPOLINE", "BINOCULARS",
    "SHOPLIFTER", "THUMBSCREW", "LUMBERJACK", "PAINTBRUSH", "MISCOUNTED",
    "HANDWRITES", "UNPROMPTED", "DIPLOMATIC", "COMPUTERS", "GOLDFINCH",
]

DIGIT_ORDER = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]

NATO = {"A": "ALPHA", "B": "BRAVO", "C": "CHARLIE", "D": "DELTA", "E": "ECHO",
        "F": "FOXTROT", "G": "GOLF", "H": "HOTEL", "I": "INDIA", "J": "JULIETT",
        "K": "KILO", "L": "LIMA", "M": "MIKE", "N": "NOVEMBER", "O": "OSCAR",
        "P": "PAPA", "Q": "QUEBEC", "R": "ROMEO", "S": "SIERRA", "T": "TANGO",
        "U": "UNIFORM", "V": "VICTOR", "W": "WHISKEY", "X": "XRAY", "Y": "YANKEE",
        "Z": "ZULU"}


def _valid_word(word: str) -> bool:
    w = (word or "").strip().upper()
    return len(w) == 10 and w.isalpha() and len(set(w)) == 10


def valid_words() -> list[str]:
    return [w for w in SHACKLE_WORDS if _valid_word(w)]


def new_shackle_key() -> tuple[str, int]:
    """Return a (word, offset) pair for a fresh shackle key."""
    words = valid_words()
    word = secrets.choice(words) if words else "BLACKHORSE"
    offset = secrets.randbelow(10) + 1  # 1..10
    return word, offset


def shackle_label(word: str, offset: int) -> str:
    return f"{word.upper()}{offset}"


def shackle_map(word: str, offset: int) -> dict[str, str]:
    """digit ('0'..'9') -> letter, per the offset + wrap rule."""
    w = word.upper()
    out: dict[str, str] = {}
    for i, d in enumerate(DIGIT_ORDER):
        out[d] = w[(offset - 1 + i) % 10]
    return out


def shackle_encode(word: str, offset: int, number: str) -> str:
    m = shackle_map(word, offset)
    return "".join(m[d] for d in number if d in m)


def shackle_decode(word: str, offset: int, letters: str) -> str:
    m = shackle_map(word, offset)
    rev = {v: k for k, v in m.items()}
    return "".join(rev.get(ch.upper(), "?") for ch in letters if ch.isalpha())


# ─── One-time pad ────────────────────────────────────────────────────────────

OTP_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def otp_generate_page(groups: int, group_size: int = 5) -> str:
    """One page worth of random pad letters as space-separated groups."""
    letters = [secrets.choice(OTP_ALPHABET) for _ in range(groups * group_size)]
    rows = []
    for i in range(0, len(letters), group_size):
        rows.append("".join(letters[i:i + group_size]))
    return " ".join(rows)


def _letter_value(ch: str) -> int:
    return OTP_ALPHABET.index(ch.upper())


def otp_encrypt(plaintext: str, key: str) -> str:
    """Modular addition of plaintext + key (mod 26)."""
    out = []
    ki = 0
    for ch in plaintext.upper():
        if ch not in OTP_ALPHABET:
            out.append(ch)
            continue
        k = key[ki % len(key)]
        out.append(OTP_ALPHABET[(_letter_value(ch) + _letter_value(k)) % 26])
        ki += 1
    return "".join(out)


def otp_decrypt(ciphertext: str, key: str) -> str:
    """Modular subtraction of ciphertext - key (mod 26)."""
    out = []
    ki = 0
    for ch in ciphertext.upper():
        if ch not in OTP_ALPHABET:
            out.append(ch)
            continue
        k = key[ki % len(key)]
        out.append(OTP_ALPHABET[(_letter_value(ch) - _letter_value(k)) % 26])
        ki += 1
    return "".join(out)


# ─── Trench code ─────────────────────────────────────────────────────────────

TRENCH_PHRASES = [
    "REPORT", "ENEMY", "FRIENDLY", "CONTACT", "AMBUSH", "PATROL", "BASE",
    "FOB", "RALLY POINT", "EXTRACT", "REINFORCE", "MEDIC", "CASUALTY",
    "WOUNDED", "PRISONER", "RETREAT", "ADVANCE", "HOLD POSITION", "FLANK",
    "REAR", "NORTH", "SOUTH", "EAST", "WEST", "GRID", "COORDINATES", "MAP",
    "COMPASS", "DISTANCE", "METERS", "KILOMETERS", "TIME NOW", "LATER",
    "ONE HOUR", "RADIO", "FREQUENCY", "CHANNEL", "NET", "CALL SIGN", "OVER",
    "OUT", "ROGER", "NEGATIVE", "AFFIRMATIVE", "REPEAT", "SAY AGAIN",
    "CEASE FIRE", "OPEN FIRE", "COVER", "MOVE", "STOP", "GO", "ATTACK",
    "DEFEND", "DIG IN", "ASSEMBLE", "DISPERSE", "RESUPPLY", "AMMUNITION",
    "WATER", "FOOD", "FUEL", "VEHICLE", "TRUCK", "HELICOPTER", "ARTILLERY",
    "MORTAR", "SNIPER", "IED", "MINEFIELD", "ROADBLOCK", "CHECKPOINT",
    "RIVER", "BRIDGE", "HILL", "RIDGE", "VALLEY", "WOODS", "BUILDING",
    "ROOM", "ENTRY", "BREACH", "FLASHBANG", "GRENADE", "SMOKE", "SIGNAL",
    "FLARE", "NIGHT", "DAY", "DAWN", "DUSK", "WEATHER", "RAIN", "SNOW",
    "WIND", "VISIBILITY", "SITREP", "REQUEST", "PERMISSION", "EXECUTE",
    "STAND BY", "HOLD FIRE", "DANGER CLOSE", "LOST", "FOUND", "RECOVER",
    "DESTROY", "CAPTURE", "EVACUATE", "SECURE", "CLEAR", "OBSERVE",
    "OVERWATCH", "FIRE TEAM", "SQUAD", "PLATOON", "LEADER", "ELEMENT",
    "HEADQUARTERS", "COMMAND",
]


def trench_entries() -> list[tuple[str, str]]:
    """(phrase, code_group) pairs with unique random 3-digit codes."""
    n = len(TRENCH_PHRASES)
    codes = secrets.SystemRandom().sample(range(1000), n)
    return [(p, f"{c:03d}") for p, c in zip(TRENCH_PHRASES, codes)]


def trench_encode(entries: list[tuple[str, str]], message: str) -> str:
    m = {phrase.upper(): code for phrase, code in entries}
    out = []
    for word in message.upper().split():
        out.append(m.get(word, f"<{word}>"))
    return " ".join(out)


def trench_decode(entries: list[tuple[str, str]], codes: str) -> str:
    m = {code: phrase for phrase, code in entries}
    out = []
    for tok in codes.split():
        out.append(m.get(tok, f"?{tok}?"))
    return " ".join(out)


# ─── PDF builders ────────────────────────────────────────────────────────────

def _safe(text: str) -> str:
    return (text or "").encode("latin-1", "replace").decode("latin-1")


def _header(pdf, title: str, subtitle: str) -> None:
    pdf.set_fill_color(10, 12, 24)
    pdf.rect(0, 0, 210, 22, "F")
    pdf.set_xy(10, 6)
    pdf.set_font("Helvetica", "B", 15)
    pdf.set_text_color(212, 165, 55)
    pdf.cell(0, 10, _safe(title))
    pdf.set_xy(10, 16)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(180, 180, 180)
    pdf.cell(0, 6, _safe(subtitle))
    pdf.set_y(28)
    pdf.set_text_color(30, 30, 30)


def shackle_pdf_bytes(word: str, offset: int) -> bytes:
    from fpdf import FPDF
    m = shackle_map(word, offset)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    _header(pdf, "13th Legion — Shackle Code", "S6 Communications | DFW Area of Operations")
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 8, _safe(f"Key: {shackle_label(word, offset)}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(0, 5, _safe(
        "Transmit a number: key 'SHACKLE', speak one word beginning with the letter "
        "for each digit, key 'UNSHACKLE'. Offsets wrap; after the last letter the "
        "sequence continues from the start of the word."))
    pdf.ln(4)
    # table header
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(22, 26, 46)
    pdf.set_text_color(212, 165, 55)
    pdf.cell(20, 9, "DIGIT", border=1, fill=True, align="C")
    pdf.cell(30, 9, "LETTER", border=1, fill=True, align="C")
    pdf.cell(0, 9, "EXAMPLE WORD", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(30, 30, 30)
    for d in DIGIT_ORDER:
        letter = m[d]
        pdf.cell(20, 8, d, border=1, align="C")
        pdf.cell(30, 8, letter, border=1, align="C")
        pdf.cell(0, 8, _safe(NATO.get(letter, "")), border=1, align="L", new_x="LMARGIN", new_y="NEXT")
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def otp_pdf_bytes(serial: str, pages: list[str]) -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    for i, page in enumerate(pages, start=1):
        pdf.add_page()
        _header(pdf, f"13th Legion — One-Time Pad {serial}", "S6 Communications | DFW Area of Operations")
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 8, _safe(f"Page {i} of {len(pages)}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font("Courier", "", 11)
        groups = page.split()
        for g in range(0, len(groups), 5):
            pdf.cell(0, 7, _safe("  ".join(groups[g:g + 5])), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(140, 140, 140)
        pdf.multi_cell(0, 4, _safe("Use once, then burn. Keep both copies matched; never reuse a page."))
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def trench_pdf_bytes(title: str, entries: list[tuple[str, str]]) -> bytes:
    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    _header(pdf, f"13th Legion — Trench Code: {title}", "S6 Communications | DFW Area of Operations")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(0, 5, _safe(
        "Encode: replace each word/phrase with its code group. Decode: reverse. "
        "Changed on rotation; treat the book as sensitive."))
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(22, 26, 46)
    pdf.set_text_color(212, 165, 55)
    pdf.cell(0, 8, "PHRASE", border=1, fill=True)
    pdf.cell(0, 8, "CODE", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(30, 30, 30)
    col_w = pdf.w - pdf.l_margin - pdf.r_margin
    for phrase, code in entries:
        pdf.cell(col_w * 0.7, 6.5, _safe(phrase), border=1)
        pdf.cell(col_w * 0.3, 6.5, _safe(code), border=1, align="C", new_x="LMARGIN", new_y="NEXT")
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()
