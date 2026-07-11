"""Guardrail L4 (dari llm_guardrails di action_rules.yaml):
- numbers_must_exist_in_payload: semua angka di output LLM harus bisa ditelusuri
  ke payload input — angka asing = output ditolak.
- forbidden_words: kata terlarang ("pasti", "dijamin", ...).
- LLM tidak pernah menulis/mengubah level Action Bias — level dihitung engine.
"""
from __future__ import annotations

import re
from typing import Any

# token angka: 63.966 | 0,05 | 5,2 | 100 | 2.5
_NUM_RE = re.compile(r"\d[\d.,]*")


def _looks_thousands(tok: str, sep: str) -> bool:
    """'63,966' / '1.234.567' = pemisah ribuan; '0,052' / '11,2' = desimal."""
    head, *groups = tok.split(sep)
    return (1 <= len(head) <= 3 and head != "0"
            and all(len(g) == 3 for g in groups))


def _normalize_token(tok: str) -> float | None:
    """'63.966'->63966 (ribuan gaya ID), '0,05'->0.05, '2.5'->2.5, '1,234.5'->1234.5."""
    tok = tok.strip(".,")
    if not tok:
        return None
    has_dot, has_comma = "." in tok, "," in tok
    try:
        if has_dot and has_comma:
            if tok.rfind(".") > tok.rfind(","):      # 1,234.5 (gaya EN)
                return float(tok.replace(",", ""))
            return float(tok.replace(".", "").replace(",", "."))  # 1.234,5 (gaya ID)
        if has_comma:
            if _looks_thousands(tok, ","):
                return float(tok.replace(",", ""))   # 63,966 ribuan EN
            return float(tok.replace(",", "."))      # 0,05 / 11,2 desimal ID
        if has_dot:
            if _looks_thousands(tok, "."):
                # ambigu: 63.966 bisa ribuan ID atau desimal — kembalikan ribuan,
                # varian desimalnya dicakup _variants() di sisi payload
                return float(tok.replace(".", ""))
            return float(tok)
        return float(tok)
    except ValueError:
        return None


def extract_numbers(text: str) -> set[float]:
    out: set[float] = set()
    for tok in _NUM_RE.findall(text):
        v = _normalize_token(tok)
        if v is not None:
            out.add(v)
    return out


def _variants(x: float) -> set[float]:
    """Varian yang sah dari 1 angka payload: pembulatan & nilai absolut.
    (LLM boleh membulatkan 63966.23 -> 63.966, -11.2 -> 11)"""
    vs = {x, abs(x)}
    for nd in (0, 1, 2, 3, 4):
        vs.add(round(x, nd))
        vs.add(round(abs(x), nd))
    if x != 0:
        for div in (1e3, 1e6, 1e9):   # "5,2jt", "1,5M" -> 5.2 / 1.5
            for nd in (0, 1, 2):
                vs.add(round(abs(x) / div, nd))
    return vs


def payload_numbers(payload: Any) -> set[float]:
    """Kumpulkan semua angka dari payload (rekursif) + varian pembulatannya."""
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.update(_variants(float(node)))
        elif isinstance(node, str):
            for v in extract_numbers(node):
                found.update(_variants(v))
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    walk(payload)
    return found


def validate_output(text: str, payload: Any, guardrails_cfg: dict[str, Any],
                    check_footer: bool = False) -> tuple[bool, list[str]]:
    """Return (ok, daftar pelanggaran)."""
    errors: list[str] = []

    lower = text.lower()
    for word in guardrails_cfg.get("forbidden_words", []):
        if word.lower() in lower:
            errors.append(f"kata terlarang: '{word}'")

    if guardrails_cfg.get("numbers_must_exist_in_payload", True):
        allowed = payload_numbers(payload)
        for n in extract_numbers(text):
            if n in allowed:
                continue
            # toleransi float kecil
            if any(abs(n - a) <= max(1e-9, abs(a) * 1e-9) for a in allowed):
                continue
            errors.append(f"angka asing tidak ada di payload: {n}")

    if check_footer:
        footer = guardrails_cfg.get("required_footer", "")
        if footer and footer.lower() not in lower:
            errors.append(f"footer wajib hilang: '{footer}'")

    return (not errors), errors
