"""Run the OCR pipeline over the synthetic cards and print a scorecard."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ocr import read_id_card, tesseract_available  # noqa: E402

EXPECTED = {
    "card_clean": {"roll_number": "21BSC1042", "full_name": "Ananya Rajesh Kumar"},
    "card_perspective": {"roll_number": "21BSC1042", "full_name": "Ananya Rajesh Kumar"},
    "card_phone": {"roll_number": "21BSC1042", "full_name": "Ananya Rajesh Kumar"},
    "card_rotated": {"roll_number": "21BSC1042", "full_name": "Ananya Rajesh Kumar"},
    "card_glare": {"roll_number": "21BSC1042", "full_name": "Ananya Rajesh Kumar"},
    "card_dim": {"roll_number": "21BSC1042", "full_name": "Ananya Rajesh Kumar"},
    "card_alt": {"roll_number": "22BCA/0317", "full_name": "Mohammed Irfan Ali"},
    # The real Loyola layout: UID label, 12-digit identifier, clumped course.
    "card_loyola_clean": {"roll_number": "111725039001", "full_name": "Parayil John Shibu"},
    "card_loyola_phone": {"roll_number": "111725039001", "full_name": "Parayil John Shibu"},
    "card_loyola_rotated": {"roll_number": "111725039001", "full_name": "Parayil John Shibu"},
}


def main(folder: str = "/tmp/loyola-cards") -> int:
    ok, ver = tesseract_available()
    print(f"tesseract: {'OK ' + ver if ok else 'MISSING — ' + ver}")
    if not ok:
        return 2

    files = sorted(Path(folder).glob("card_*.jpg"))
    if not files:
        print(f"no cards in {folder}; run make_test_card.py first")
        return 2

    passed = 0
    for f in files:
        res = read_id_card(f.read_bytes())
        stem = f.stem
        fields = res.get("fields", {})
        exp = EXPECTED.get(stem, {})
        roll_ok = fields.get("roll_number") == exp.get("roll_number")
        name_ok = (fields.get("full_name") or "").lower() == (exp.get("full_name") or "").lower()
        verdict = "PASS" if (roll_ok and name_ok) else "FAIL"
        if verdict == "PASS":
            passed += 1
        print(
            f"\n=== {stem} [{verdict}] {res.get('elapsed_ms')}ms passes={res.get('passes')} "
            f"rot={res.get('rotation')}({res.get('rotation_method')}) variant={res.get('best_variant')}"
        )
        print(f"    overall={res.get('overall')} issuer_ok={res.get('issuer_ok')} face={res.get('face_found')}")
        print(f"    quality={json.dumps(res.get('quality', {}))}")
        for k, v in fields.items():
            c = res.get("confidence", {}).get(k)
            m = res.get("method", {}).get(k)
            mark = ""
            if k in exp:
                mark = "  <-- expected " + repr(exp[k]) if str(v).lower() != str(exp[k]).lower() else "  ok"
            print(f"    {k:<13} = {v!r:<28} conf={c} via={m}{mark}")
        if res.get("error"):
            print(f"    error={res['error']}")

    print(f"\n{passed}/{len(files)} cards fully correct (roll + name)")
    return 0 if passed == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/loyola-cards"))
