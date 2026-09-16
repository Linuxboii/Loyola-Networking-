"""Turn raw Tesseract output into the fields verification actually needs.

The extractor is template-driven: label aliases, the roll-number pattern, the
expected issuer tokens and the known course list all come from a config dict
that the admin console can edit at runtime (``app_settings['ocr_template']``).
That matters because the defaults get corrected against real cards without a
redeploy.

The defaults are now cut to a specimen Loyola Academy card rather than a generic
Indian-college layout. That card reads:

    LOYOLA ACADEMY (Autonomous)
    DEGREE & PG COLLEGE
    A College with Potential for Excellence
    UID NO :111725036019
    Name    : PARAYIL JOHN SHIBU
    Course  : B.Sc. Comp.Sci. & Cog .Sys.
              ACSC 2025 2026
              NCSC 2026 2027
              DCSC 2027 2028
    VALID UPTO APRIL 2028

Four things about that layout are not what the generic defaults assumed, and
each is handled below: the identifier is labelled ``UID NO`` and is a bare
12-digit number; the course carries clumped abbreviations with stray spaces
before full stops; the batch is printed as three year-code rows with
*space-separated* year pairs and no "batch" label at all; and the expiry is a
month and year, not a date.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Default template
# ---------------------------------------------------------------------------

DEFAULT_TEMPLATE: dict[str, Any] = {
    "issuer_tokens": ["LOYOLA", "ACADEMY", "POTENTIAL FOR EXCELLENCE"],
    "issuer_min_hits": 1,
    "roll_regex": r"^[A-Z0-9][A-Z0-9\-/]{4,19}$",
    "roll_must_contain_digit": True,
    "labels": {
        # "uid no" leads because that is what the Loyola card actually prints.
        "roll_number": [
            "uid no", "uid number", "uid", "roll no", "roll number", "rollno",
            "admission no", "admn no", "adm no",
            "admission number", "reg no", "regd no", "register no", "registration no",
            "id no", "student id", "h t no", "hall ticket",
        ],
        "full_name": ["name", "student name", "name of student"],
        "course": ["course", "class", "programme", "program", "degree", "branch"],
        "department": ["department", "dept", "stream", "group"],
        "batch": ["batch", "year", "academic year", "session", "admitted"],
        "expiry": ["valid upto", "valid up to", "valid till", "valid until", "expiry", "expires", "valid"],
        "dob": ["dob", "date of birth", "d o b"],
        "blood_group": ["blood group", "blood"],
    },
    "known_courses": [
        # Loyola prints the course clumped: "B.Sc. Comp.Sci. & Cog .Sys."
        "B.SC. COMP.SCI. & COG.SYS.", "B.SC. COMP.SCI.", "COMP.SCI. & COG.SYS.",
        "B.SC", "B.COM", "B.A", "BBA", "BCA", "B.TECH", "M.SC", "M.COM", "M.A",
        "MBA", "MCA", "B.ED", "BSC", "BCOM", "MSC", "MCOM",
    ],
    # Per-academic-year programme codes printed under the course, each followed
    # by the year pair it covers ("ACSC 2025 2026"). The last one's end year is
    # the graduation year.
    "year_code_regex": r"^[A-Z]{2,6}$",
    "known_year_codes": ["ACSC", "NCSC", "DCSC"],
    # Lines matching these are structural noise, never a person's name.
    "noise_lines": [
        "identity card", "student identity card", "id card", "affiliated",
        "autonomous", "osmania university", "university", "principal", "signature",
        "government of", "accredited", "naac", "re-accredited", "secunderabad",
        "alwal", "old alwal", "telangana", "hyderabad", "www", "http", "phone",
        "email", "estd", "jesuit", "college", "p g college", "campus", "road",
        # Loyola's own banner and crest motto.
        "loyola academy", "degree pg college", "potential for excellence",
        "wisdom", "truth", "service", "valid upto",
    ],
    "min_field_confidence": 0.45,
}

AMBIGUOUS = {
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1", "|": "1",
    "S": "5", "Z": "2", "B": "8", "G": "6", "T": "7",
}
AMBIGUOUS_REVERSE = {"0": "O", "1": "I", "5": "S", "2": "Z", "8": "B", "6": "G"}


@dataclass
class Word:
    text: str
    conf: float
    line: int
    block: int
    left: int
    top: int
    width: int
    height: int


@dataclass
class Line:
    index: int
    text: str
    conf: float
    top: int
    words: list[Word] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _split_by_row(ws: list[Word]) -> list[list[Word]]:
    """Break a Tesseract 'line' that actually spans several printed rows.

    On a two-column card photographed at an angle, layout analysis happily
    merges "Name  Mohammed Irfan Ali" with the Course row below it, producing
    x-interleaved nonsense. Tesseract's grouping is only a hint; vertical
    geometry is the truth.
    """
    if len(ws) < 2:
        return [ws]
    heights = sorted(w.height for w in ws)
    med_h = heights[len(heights) // 2] or 1
    centres = [w.top + w.height / 2 for w in ws]
    if max(centres) - min(centres) <= med_h * 0.85:
        return [ws]

    ordered = sorted(zip(centres, ws), key=lambda t: t[0])
    rows: list[list[Word]] = [[ordered[0][1]]]
    row_centre = ordered[0][0]
    for centre, w in ordered[1:]:
        if abs(centre - row_centre) > med_h * 0.7:
            rows.append([w])
            row_centre = centre
        else:
            rows[-1].append(w)
            row_centre = sum(x.top + x.height / 2 for x in rows[-1]) / len(rows[-1])
    return rows


def build_lines(words: list[Word]) -> list[Line]:
    buckets: dict[tuple[int, int], list[Word]] = {}
    for w in words:
        if not w.text.strip():
            continue
        buckets.setdefault((w.block, w.line), []).append(w)

    groups: list[list[Word]] = []
    for ws in buckets.values():
        groups.extend(_split_by_row(ws))

    lines: list[Line] = []
    for i, ws in enumerate(sorted(groups, key=lambda g: min(w.top for w in g))):
        ws.sort(key=lambda w: w.left)
        text = _squash(" ".join(w.text for w in ws))
        if not text:
            continue
        confs = [w.conf for w in ws if w.conf >= 0]
        lines.append(
            Line(
                index=i,
                text=text,
                conf=(sum(confs) / len(confs) / 100.0) if confs else 0.0,
                top=min(w.top for w in ws),
                words=ws,
            )
        )
    return lines


def _label_hit(line_text: str, aliases: Iterable[str]) -> tuple[bool, str]:
    """Does this line start with one of the aliases? Return the trailing value."""
    norm = _norm(line_text)
    for alias in aliases:
        a = _norm(alias)
        if not a:
            continue
        if norm.startswith(a):
            rest = norm[len(a) :]
            # Strip the separator the label was printed with.
            rest = re.sub(r"^[\s:.\-–—/]+", "", rest)
            return True, rest
        # Label appearing mid-line, e.g. "STUDENT  ROLL NO : 1234".
        pos = norm.find(" " + a + " ")
        if pos >= 0:
            rest = norm[pos + len(a) + 2 :]
            rest = re.sub(r"^[\s:.\-–—/]+", "", rest)
            return True, rest
        # Fuzzy: OCR mangles short labels badly ("Rol No", "Adm N0").
        head = norm[: len(a) + 2]
        if len(a) >= 5 and similar(head[: len(a)], a) >= 0.82:
            rest = re.sub(r"^[\s:.\-–—/]+", "", norm[len(a) :])
            return True, rest
    return False, ""


def _raw_value_for(line: Line, aliases: Iterable[str]) -> str:
    """Same as _label_hit but preserves the original casing of the value."""
    hit, _ = _label_hit(line.text, aliases)
    if not hit:
        return ""
    parts = re.split(r"[:：]", line.text, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return _squash(parts[1])
    # No colon printed — drop the alias words from the front.
    norm_words = line.text.split()
    for alias in sorted(aliases, key=len, reverse=True):
        n = len(alias.split())
        if similar(_norm(" ".join(norm_words[:n])), _norm(alias)) >= 0.8:
            return _squash(" ".join(norm_words[n:]))
    # Tesseract routinely welds a short label together ("UIDNO 111725036019",
    # "VALIDUPTO APRIL 2028"), which defeats the word-count match above.
    if norm_words:
        head = _norm(norm_words[0])
        for alias in sorted(aliases, key=len, reverse=True):
            compact = _norm(alias).replace(" ", "")
            if len(compact) < 4:
                continue
            # The length guard matters: without it "STUDENT IDENTITY CARD" reads
            # as the label "student id" followed by the value "IDENTITY CARD".
            # A welded label is never *shorter* than the alias it welded.
            if len(head) >= len(compact) - 1 and similar(head, compact) >= 0.9:
                return _squash(" ".join(norm_words[1:]))
            # ...or welded to the value itself: "UIDNO111725036019".
            if head.startswith(compact) and len(head) > len(compact):
                return _squash(" ".join([head[len(compact):], *norm_words[1:]]))
    return ""


# ---------------------------------------------------------------------------
# Roll number
# ---------------------------------------------------------------------------


# Roll numbers on Indian college cards overwhelmingly take one of these shapes.
# They are used to *rank* candidate readings, never to reject one — a college
# with an unusual format still verifies, it just leans on the configured regex.
ROLL_SHAPES = [
    (re.compile(r"^\d{12}$"), 3.4),                        # Loyola UID, e.g. 111725036019
    (re.compile(r"^\d{10,13}$"), 3.1),                     # same family, a digit dropped or doubled
    (re.compile(r"^\d{2}[A-Z]{2,6}[-/]?\d{2,6}$"), 3.0),   # 21BSC1042, 22BCA/0317
    (re.compile(r"^\d{4}[A-Z]{2,6}\d{2,6}$"), 2.8),        # 2021BSC042
    (re.compile(r"^[A-Z]{1,5}[-/]?\d{4,10}$"), 2.4),       # BSC21042, LA-100234
    (re.compile(r"^\d{2}[A-Z]\d{2}[A-Z]\d{2,5}$"), 2.2),   # 21A05B1042 style
    (re.compile(r"^\d{6,12}$"), 1.2),                       # plain admission number
]

MAX_AMBIGUOUS_POSITIONS = 12


def _shape_score(cand: str) -> float:
    for pattern, score in ROLL_SHAPES:
        if pattern.match(cand):
            return score
    # Fewer letter/digit alternations is more identifier-like.
    runs = len(re.findall(r"[A-Z]+|\d+", cand))
    return max(0.0, 1.0 - 0.25 * max(0, runs - 2))


def _roll_candidates(token: str) -> list[tuple[str, int]]:
    """Enumerate plausible readings of an alphanumeric token.

    Tesseract confuses O/0, I/1, S/5, Z/2 constantly on ID cards, and it does so
    per character — '21BSC1042' comes back as 'ZIBSCIO4Z', where some positions
    need flipping and some must be left alone. Coarse all-digits / all-letters
    passes cannot express that, so we enumerate the ambiguous positions and
    return each reading with its substitution distance for scoring.
    """
    token = re.sub(r"[^A-Z0-9\-/]", "", token.strip().upper())
    if len(token) < 4:
        return []

    # A long all-digit run is already a legal identifier on this card (the UID is
    # twelve digits). Flipping digits *into* letters there can only invent a
    # worse reading, and enumerating twelve ambiguous positions costs 4096
    # candidates for nothing.
    if len(token) >= 10 and token.isdigit():
        return [(token, 0)]

    positions = [
        i
        for i, c in enumerate(token)
        if c in AMBIGUOUS or c in AMBIGUOUS_REVERSE
    ]
    if len(positions) > MAX_AMBIGUOUS_POSITIONS:
        # Degrade gracefully rather than exploding: coarse readings only.
        coarse = {
            token: 0,
            "".join(AMBIGUOUS.get(c, c) for c in token): len(positions),
            "".join(AMBIGUOUS_REVERSE.get(c, c) for c in token): len(positions),
        }
        return list(coarse.items())

    out: dict[str, int] = {token: 0}
    for mask in range(1, 1 << len(positions)):
        chars = list(token)
        flips = 0
        for bit, idx in enumerate(positions):
            if not (mask >> bit) & 1:
                continue
            c = chars[idx]
            swapped = AMBIGUOUS.get(c) or AMBIGUOUS_REVERSE.get(c)
            if swapped:
                chars[idx] = swapped
                flips += 1
        cand = "".join(chars)
        if cand not in out or flips < out[cand]:
            out[cand] = flips
    return list(out.items())


def extract_roll(lines: list[Line], tpl: dict[str, Any]) -> tuple[str, float, str]:
    """Return (roll, confidence, how)."""
    pattern = re.compile(tpl.get("roll_regex") or DEFAULT_TEMPLATE["roll_regex"])
    need_digit = bool(tpl.get("roll_must_contain_digit", True))
    aliases = tpl["labels"]["roll_number"]

    def ok(cand: str) -> bool:
        if not pattern.match(cand):
            return False
        if need_digit and not any(ch.isdigit() for ch in cand):
            return False
        return True

    def best_of(text: str) -> tuple[float, str] | None:
        """Pick the highest-scoring legal reading of one token."""
        best: tuple[float, str] | None = None
        for cand, flips in _roll_candidates(text):
            if not ok(cand):
                continue
            # A bare four-digit number is a year, not an identifier.
            if cand.isdigit() and len(cand) == 4 and 1950 <= int(cand) <= 2100:
                continue
            # Trust the literal reading, but not blindly: each flip costs a
            # little, a recognised roll-number shape is worth a lot more.
            score = _shape_score(cand) - 0.12 * flips
            if best is None or score > best[0]:
                best = (score, cand)
        return best

    # 1. Label-anchored — by far the most reliable.
    for line in lines:
        # "STUDENT IDENTITY CARD" is a banner, not a labelled identifier; never
        # mine a roll number out of one.
        if is_noise_line(line.text, tpl):
            continue
        value = _raw_value_for(line, aliases)
        if not value:
            continue
        picks = [p for p in (best_of(tok) for tok in value.split()) if p]
        compact = best_of(re.sub(r"\s+", "", value))
        if compact:
            picks.append(compact)
        if picks:
            shape, cand = max(picks)
            # Confidence blends how the text was read with how well the result
            # matches a roll-number shape.
            conf = min(0.98, 0.52 + line.conf * 0.33 + min(shape, 3.0) / 3.0 * 0.15)
            return cand, round(conf, 3), "label"

    # 2. Pattern scan across every token on the card.
    scored: list[tuple[float, str]] = []
    for line in lines:
        for w in line.words:
            pick = best_of(w.text)
            if not pick:
                continue
            shape, cand = pick
            scored.append((shape * 0.25 + (w.conf / 100.0) * 0.45 + min(len(cand), 14) / 40.0, cand))
    if scored:
        scored.sort(reverse=True)
        best_score, best_cand = scored[0]
        return best_cand, max(0.30, min(0.74, best_score)), "pattern"

    return "", 0.0, "none"


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------


def extract_name(lines: list[Line], tpl: dict[str, Any]) -> tuple[str, float, str]:
    aliases = tpl["labels"]["full_name"]
    noise = [_norm(n) for n in tpl.get("noise_lines", [])]
    all_labels = {_norm(a) for group in tpl["labels"].values() for a in group}

    def is_noise(text: str) -> bool:
        n = _norm(text)
        if not n:
            return True
        if any(tok and tok in n for tok in noise):
            return True
        if n in all_labels:
            return True
        return False

    def clean(value: str) -> str:
        value = re.sub(r"[^A-Za-z .'\-]", " ", value)
        value = _squash(value)
        # Drop 1-character fragments Tesseract invents at line edges.
        parts = [p for p in value.split() if len(p) > 1 or p.upper() in {"K", "V", "S", "M", "A", "R"}]
        return _squash(" ".join(parts)).title()

    # 1. Label-anchored.
    for line in lines:
        value = _raw_value_for(line, aliases)
        if value and not is_noise(value):
            cleaned = clean(value)
            if len(cleaned) >= 3 and len(cleaned.split()) <= 6:
                return cleaned, min(0.97, 0.60 + line.conf * 0.37), "label"
        # Label alone on its line → the name is the next line.
        hit, rest = _label_hit(line.text, aliases)
        if hit and not rest.strip():
            nxt = next((l for l in lines if l.index == line.index + 1), None)
            if nxt and not is_noise(nxt.text):
                cleaned = clean(nxt.text)
                if len(cleaned) >= 3:
                    return cleaned, min(0.90, 0.55 + nxt.conf * 0.35), "label-next"

    # 2. Heuristic: the biggest run of alphabetic words in the upper half that
    # isn't the institution's own name.
    if lines:
        page_top = min(l.top for l in lines)
        page_bottom = max(l.top for l in lines) or 1
        span = max(1, page_bottom - page_top)
        best: tuple[float, str] = (0.0, "")
        for line in lines:
            if is_noise(line.text):
                continue
            letters = sum(ch.isalpha() or ch.isspace() for ch in line.text)
            if not line.text or letters / max(1, len(line.text)) < 0.85:
                continue
            words = line.text.split()
            if not (2 <= len(words) <= 5):
                continue
            rel = (line.top - page_top) / span
            positional = 1.0 - abs(rel - 0.42)  # names usually sit mid-card
            score = line.conf * 0.5 + positional * 0.5
            if score > best[0]:
                best = (score, clean(line.text))
        if best[1]:
            return best[1], max(0.25, min(0.66, best[0])), "heuristic"

    return "", 0.0, "none"


# ---------------------------------------------------------------------------
# Course / department / batch / expiry
# ---------------------------------------------------------------------------


def is_noise_line(text: str, tpl: dict[str, Any]) -> bool:
    """Institution banners, accreditation blurbs and addresses are never field
    values — but 'Degree & P.G. College' starts with the word 'Degree', which is
    a perfectly good course label. Guard every heuristic with this."""
    n = _norm(text)
    if not n:
        return True
    return any(tok and _norm(tok) in n for tok in tpl.get("noise_lines", []))


def _plausible_value(value: str, max_words: int = 6, max_chars: int = 48) -> bool:
    v = _squash(value)
    if not (2 <= len(v) <= max_chars):
        return False
    if len(v.split()) > max_words:
        return False
    # A value that is mostly punctuation is a mis-split, not a reading.
    letters = sum(ch.isalnum() for ch in v)
    return letters >= max(2, len(v) * 0.45)


def _is_debris(tok: str) -> bool:
    """A token with no real letters or digits in it is OCR debris, not a word.

    The crest, the card's border and the QR block all bleed into the row the
    course is printed on, and come back as tokens like "—" or "®5¢". Left in
    place they are what turns "B.Sc. Comp.Sci. & Cog.Sys." into the
    "— ®5¢ COMP.SCI. & COG.SYS." a reviewer then has to retype.
    """
    solid = sum(1 for ch in tok if ch.isascii() and ch.isalnum())
    return solid == 0 or solid / len(tok) < 0.5


def canonical_course(value: str, tpl: dict[str, Any]) -> tuple[str, bool]:
    """Snap a course reading onto the known list when it is plainly the same one.

    Compared on letters and digits alone, so the punctuation Loyola scatters
    through "B.Sc. Comp.Sci. & Cog .Sys." — and the dashes OCR puts where the
    full stops were — stop mattering.
    """
    key = re.sub(r"[^A-Z0-9]", "", value.upper())
    if len(key) < 3:
        return value, False
    best, best_key, score = "", "", 0.0
    for k in tpl.get("known_courses", []):
        k = k.upper()
        kk = re.sub(r"[^A-Z0-9]", "", k)
        if not kk:
            continue
        r = similar(key, kk)
        # On a tie prefer the longer entry: "B.SC" is a prefix of
        # "B.SC. COMP.SCI. & COG.SYS." and would otherwise win on brevity.
        if r > score or (r == score and len(kk) > len(best_key)):
            best, best_key, score = k, kk, r
    return (best, True) if score >= 0.78 else (value, False)


def tidy_course(value: str, tpl: dict[str, Any]) -> str:
    """Normalise the clumped abbreviations Loyola prints on the course row.

    "B.Sc. Comp.Sci. & Cog .Sys." is one course, but OCR gives back the stray
    space before ".Sys." verbatim and, on a tight crop, often welds the first
    year-code row onto the end of it.
    """
    v = _squash(value.upper())
    codes = {c.upper() for c in tpl.get("known_year_codes", [])}
    kept: list[str] = []
    for tok in v.split():
        bare = re.sub(r"[^A-Z0-9]", "", tok)
        # The year-code block starts here — everything after it belongs to batch.
        if bare in codes or re.fullmatch(r"(19|20)\d{2}", bare):
            break
        kept.append(tok)
    # Debris only gets trimmed off the ends. A junk token in the middle is
    # usually a mangled real word, and dropping it would silently join two
    # halves of the course name that were never adjacent.
    while kept and _is_debris(kept[0]):
        kept.pop(0)
    while kept and _is_debris(kept[-1]):
        kept.pop()
    v = " ".join(kept)
    v = re.sub(r"\s+\.", ".", v)          # "Cog .Sys." -> "Cog.Sys."
    v = re.sub(r"\.\s*&\s*", ". & ", v)   # keep the ampersand breathing
    v = _squash(v).strip(" ,;:-")
    return v


def extract_course(lines: list[Line], tpl: dict[str, Any]) -> tuple[str, float, str]:
    aliases = tpl["labels"]["course"]
    known = [k.upper() for k in tpl.get("known_courses", [])]

    for line in lines:
        if is_noise_line(line.text, tpl):
            continue
        value = tidy_course(_raw_value_for(line, aliases), tpl)
        if value and _plausible_value(value, max_words=6, max_chars=48) and not is_noise_line(value, tpl):
            canonical, snapped = canonical_course(value, tpl)
            return canonical, min(0.95, 0.58 + line.conf * 0.37), "label-known" if snapped else "label"

    # Longest known course that appears in the text wins, so the specific
    # "B.SC. COMP.SCI. & COG.SYS." beats the bare "B.SC" prefix it contains.
    for line in lines:
        if is_noise_line(line.text, tpl):
            continue
        upper = line.text.upper().replace(" ", "")
        hits = [k for k in known if k.replace(" ", "") in upper]
        if hits:
            return max(hits, key=len), max(0.35, min(0.72, line.conf)), "known-course"
    return "", 0.0, "none"


def extract_department(lines: list[Line], tpl: dict[str, Any]) -> tuple[str, float, str]:
    aliases = tpl["labels"]["department"]
    for line in lines:
        if is_noise_line(line.text, tpl):
            continue
        value = _raw_value_for(line, aliases)
        if value and _plausible_value(value, max_words=6, max_chars=48) and not is_noise_line(value, tpl):
            return _squash(value.upper()), min(0.94, 0.55 + line.conf * 0.38), "label"
    return "", 0.0, "none"


YEAR_RANGE = re.compile(r"\b(19|20)(\d{2})\s*[-–—/to]{1,3}\s*(?:(19|20))?(\d{2})\b", re.I)
SINGLE_YEAR = re.compile(r"\b(19|20)\d{2}\b")
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b"), "dmy2"),
]
MONTH_NAMES = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}
MONTH_DATE = re.compile(r"\b(\d{1,2})?\s*([a-z]{3,9})[,\s\-]+(\d{4})\b", re.I)


# Loyola prints academic years with nothing between them at all — "ACSC 2025
# 2026" — which the punctuated YEAR_RANGE above cannot see.
YEAR_PAIR_SPACE = re.compile(r"\b((?:19|20)\d{2})\s+((?:19|20)\d{2})\b")


def _year_pair(text: str) -> tuple[int, int] | None:
    """Read a year span written either '2025-2026' or '2025 2026'."""
    m = YEAR_RANGE.search(text)
    if m:
        start = int(m.group(1) + m.group(2))
        tail = m.group(4)
        end_prefix = m.group(3) or ("20" if int(tail) < 90 else "19")
        end = int(end_prefix + tail)
        if end < start:
            end = start + (end % 100)
        if 1990 <= start <= 2100 and start <= end <= start + 10:
            return start, end
    m2 = YEAR_PAIR_SPACE.search(text)
    if m2:
        start, end = int(m2.group(1)), int(m2.group(2))
        if 1990 <= start <= 2100 and start < end <= start + 10:
            return start, end
    return None


def extract_year_codes(lines: list[Line], tpl: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the per-year programme rows: ACSC 2025 2026 / NCSC 2026 2027 / ...

    These carry the real course span. Nothing on the card is labelled "batch",
    so without them the graduation year has to be guessed from the expiry.
    """
    known = {c.upper() for c in tpl.get("known_year_codes", [])}
    shape = re.compile(tpl.get("year_code_regex") or r"^[A-Z]{2,6}$")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()

    for line in lines:
        span = _year_pair(line.text)
        if not span:
            continue
        # The code is the first alphabetic token on the row, ahead of the years.
        code = ""
        for tok in line.text.upper().split():
            bare = re.sub(r"[^A-Z]", "", tok)
            if not bare:
                continue
            if bare in known or (not known and shape.match(bare)):
                code = bare
                break
            if shape.match(bare) and len(bare) >= 3:
                # Unknown but code-shaped — accept it fuzzily so a new programme
                # code does not silently drop the whole row.
                if not known or any(similar(bare, k) >= 0.6 for k in known):
                    code = bare
                    break
        if not code:
            continue
        key = (code, span[0], span[1])
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": code, "start": span[0], "end": span[1], "conf": round(line.conf, 3)})

    out.sort(key=lambda r: (r["start"], r["end"]))
    return out


def extract_batch(lines: list[Line], tpl: dict[str, Any]) -> tuple[str, int | None, float, str]:
    """Return (raw, graduation_year, confidence, how)."""
    aliases = tpl["labels"]["batch"]

    # 1. Year-code rows are the most specific thing on a Loyola card: the span
    # runs from the first row's start year to the last row's end year.
    codes = extract_year_codes(lines, tpl)
    if codes:
        start = min(c["start"] for c in codes)
        end = max(c["end"] for c in codes)
        conf = sum(c["conf"] for c in codes) / len(codes)
        return f"{start}-{end}", end, min(0.93, 0.62 + conf * 0.31), "year-codes"

    def from_text(text: str, conf: float, how: str):
        span = _year_pair(text)
        if span:
            start, end = span
            return f"{start}-{end}", end, min(0.95, 0.6 + conf * 0.35), how
        m2 = SINGLE_YEAR.search(text)
        if m2:
            y = int(m2.group(0))
            if 1990 <= y <= 2100:
                return str(y), y, min(0.75, 0.42 + conf * 0.3), how + "-single"
        return None

    for line in lines:
        value = _raw_value_for(line, aliases)
        if value:
            got = from_text(value, line.conf, "label")
            if got:
                return got

    for line in lines:
        got = from_text(line.text, line.conf * 0.7, "scan")
        if got and "-" in got[0]:
            return got
    return "", None, 0.0, "none"


def _month_end(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _parse_date(text: str) -> dt.date | None:
    for pat, order in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            if order == "dmy":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif order == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                d, mo, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
                y = 2000 + y2 if y2 < 90 else 1900 + y2
            if mo > 12 and d <= 12:  # printed month-first
                d, mo = mo, d
            return dt.date(y, mo, d)
        except ValueError:
            continue
    # finditer, not search: "VALID UPTO APRIL 2028" offers "UPTO 2028" first,
    # and bailing on the first non-month word would lose the real date.
    for m in MONTH_DATE.finditer(text):
        mon = MONTH_NAMES.get(m.group(2)[:3].lower())
        if not mon:
            continue
        year = int(m.group(3))
        try:
            # "VALID UPTO APRIL 2028" means the card is good *through* April, so
            # a month with no day printed resolves to the last of that month.
            day = int(m.group(1)) if m.group(1) else _month_end(year, mon)
            return dt.date(year, mon, day)
        except ValueError:
            continue
    return None


def extract_expiry(lines: list[Line], tpl: dict[str, Any], batch_end: int | None) -> tuple[dt.date | None, float, str]:
    aliases = tpl["labels"]["expiry"]

    for line in lines:
        value = _raw_value_for(line, aliases)
        if not value:
            continue
        parsed = _parse_date(value)
        if parsed:
            return parsed, min(0.95, 0.6 + line.conf * 0.35), "label-date"
        m = SINGLE_YEAR.search(value)
        if m:
            year = int(m.group(0))
            if 1990 <= year <= 2100:
                # Academic years end mid-year; 30 June is the conventional cutoff.
                return dt.date(year, 6, 30), min(0.8, 0.5 + line.conf * 0.3), "label-year"

    # No explicit expiry printed: infer from the batch's graduating year. Many
    # college cards only carry the batch range.
    if batch_end and 1990 <= batch_end <= 2100:
        return dt.date(batch_end, 6, 30), 0.45, "inferred-from-batch"
    return None, 0.0, "none"


def check_issuer(lines: list[Line], tpl: dict[str, Any]) -> tuple[bool, int, list[str]]:
    """Does the card actually name the institution we gate on?"""
    tokens = [t.upper() for t in tpl.get("issuer_tokens", [])]
    need = int(tpl.get("issuer_min_hits", 1))
    blob = " ".join(l.text for l in lines).upper()
    blob_norm = _norm(blob)
    hits = []
    for t in tokens:
        tn = _norm(t)
        if tn and tn in blob_norm:
            hits.append(t)
            continue
        # Fuzzy: allow one or two mangled characters in a long token.
        if len(tn) >= 6 and any(similar(tn, w) >= 0.8 for w in blob_norm.split()):
            hits.append(t)
    return (len(hits) >= need), len(hits), hits


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def extract_fields(words: list[Word], tpl: dict[str, Any] | None = None) -> dict[str, Any]:
    template = {**DEFAULT_TEMPLATE, **(tpl or {})}
    if tpl and "labels" in tpl:
        template["labels"] = {**DEFAULT_TEMPLATE["labels"], **tpl["labels"]}

    lines = build_lines(words)
    text = "\n".join(l.text for l in lines)

    roll, roll_c, roll_how = extract_roll(lines, template)
    name, name_c, name_how = extract_name(lines, template)
    course, course_c, course_how = extract_course(lines, template)
    dept, dept_c, dept_how = extract_department(lines, template)
    batch_raw, batch_end, batch_c, batch_how = extract_batch(lines, template)
    expiry, expiry_c, expiry_how = extract_expiry(lines, template, batch_end)
    issuer_ok, issuer_hits, issuer_found = check_issuer(lines, template)
    year_codes = extract_year_codes(lines, template)

    fields = {
        "roll_number": roll,
        "full_name": name,
        "course": course,
        "department": dept,
        "batch": batch_raw,
        "batch_year": batch_end,
        "expiry": expiry.isoformat() if expiry else None,
        # ["ACSC 2025-2026", "NCSC 2026-2027", "DCSC 2027-2028"]
        "year_codes": [f"{c['code']} {c['start']}-{c['end']}" for c in year_codes],
    }
    confidence = {
        "roll_number": round(roll_c, 3),
        "full_name": round(name_c, 3),
        "course": round(course_c, 3),
        "department": round(dept_c, 3),
        "batch": round(batch_c, 3),
        "expiry": round(expiry_c, 3),
        "year_codes": round(
            (sum(c["conf"] for c in year_codes) / len(year_codes)) if year_codes else 0.0, 3
        ),
    }
    how = {
        "roll_number": roll_how,
        "full_name": name_how,
        "course": course_how,
        "department": dept_how,
        "batch": batch_how,
        "expiry": expiry_how,
        "year_codes": "scan" if year_codes else "none",
    }

    return {
        "fields": fields,
        "confidence": confidence,
        "method": how,
        "issuer_ok": issuer_ok,
        "issuer_hits": issuer_hits,
        "issuer_found": issuer_found,
        "lines": [{"text": l.text, "conf": round(l.conf, 3)} for l in lines],
        "text": text,
    }


def overall_confidence(result: dict[str, Any]) -> float:
    """Weighted blend. The roll number dominates because it is the unique key —
    a wrong name is an annoyance, a wrong roll number is a stolen identity."""
    c = result["confidence"]
    weights = {"roll_number": 0.45, "full_name": 0.25, "expiry": 0.12, "batch": 0.10, "course": 0.08}
    total = sum(weights.values())
    score = sum(c.get(k, 0.0) * w for k, w in weights.items()) / total
    if not result.get("issuer_ok"):
        score *= 0.55  # card does not name the institution — big penalty
    return round(min(1.0, max(0.0, score)), 3)
