"""L4 — LLM hanya menulis narasi 'Arti' + ringkasan. Level Action Bias, angka,
dan invalidation datang dari engine (payload). Output divalidasi guardrails;
gagal 2x regenerasi -> fallback template deterministik tanpa LLM."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from app.config import ActionRules, Settings
from app.engine.events import SignalGroup
from app.llm.guardrails import validate_output

logger = logging.getLogger(__name__)

MAX_REGENERATE = 2
MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """Kamu analis crypto untuk laporan internal berbahasa Indonesia.
ATURAN KERAS:
1. Gunakan HANYA angka yang ada di payload JSON. Dilarang menambah angka apa pun
   yang tidak ada di payload (termasuk perkiraan, tahun, persentase).
2. Label Action Bias sudah dihitung engine — jangan menyebut atau mengubah level.
3. Nada faktual, tanpa hype. Dilarang kata: pasti, dijamin, guaranteed,
   beli sekarang, jual sekarang.
4. Jawab HANYA dalam JSON valid: {"summary": "<=3 kalimat konteks pasar",
   "arti": {"<entity>": "1-2 kalimat interpretasi 'Arti' untuk sinyal entitas itu"}}
   Key 'arti' memuat persis entitas yang ada di payload signals."""


def build_payload(groups: list[SignalGroup], regime: tuple[int, str, dict],
                  snapshot: dict[str, Any]) -> dict[str, Any]:
    """Payload = satu-satunya sumber angka yang boleh dipakai LLM."""
    score, label, components = regime
    return {
        "regime": {"score": score, "label": label,
                   "components": {k: round(v, 1) for k, v in components.items()}},
        "market_snapshot": snapshot,
        "signals": [{
            "entity": g.entity, "level": g.level, "direction": g.direction,
            "touches_position": g.touches_position,
            "invalidation": g.invalidation,
            "events": [e.as_dict() for e in g.events],
        } for g in groups],
    }


def template_arti(group: SignalGroup) -> str:
    """Fallback deterministik — interpretasi mekanis dari data event."""
    parts = []
    for e in group.events:
        if e.delta_pct is not None:
            parts.append(f"{e.metric} {e.entity} berubah {e.delta_pct:+.2f}%")
        elif e.new_value is not None:
            parts.append(f"{e.metric} {e.entity} = {e.new_value}")
    arah = {"bearish": "risiko meningkat", "bullish": "kondisi membaik",
            "neutral": "perlu observasi"}[group.direction]
    n = len({ev.module for ev in group.events})
    konfirmasi = f"{n} modul independen searah" if n > 1 else "trigger tunggal"
    return f"{'; '.join(parts)} — {konfirmasi}, {arah}."


def template_narrative(payload: dict[str, Any]) -> dict[str, Any]:
    sigs = payload["signals"]
    r = payload["regime"]
    summary = (f"Regime {r['label']} dengan skor {r['score']}/100. "
               f"{len(sigs)} sinyal aktif dalam 24 jam terakhir."
               if sigs else
               f"Regime {r['label']} dengan skor {r['score']}/100. "
               f"Tidak ada sinyal yang menembus threshold.")
    return {"summary": summary, "arti": {}}


def _call_anthropic(settings: Settings, payload: dict[str, Any]) -> str:
    import anthropic  # import lokal: dependensi opsional
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=MODEL, max_tokens=1024, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    return msg.content[0].text


def narrate(payload: dict[str, Any], rules: ActionRules, settings: Settings,
            llm_call: Callable[[dict], str] | None = None) -> tuple[dict[str, Any], bool]:
    """Return (narasi {'summary','arti'}, llm_used).
    llm_call bisa diinjeksi untuk test; default Anthropic API."""
    cfg = rules.llm_guardrails
    if llm_call is None:
        if not settings.anthropic_api_key:
            return template_narrative(payload), False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            logger.warning("paket 'anthropic' tidak terpasang — pakai template")
            return template_narrative(payload), False
        llm_call = lambda p: _call_anthropic(settings, p)  # noqa: E731

    for attempt in range(1 + MAX_REGENERATE):
        try:
            raw = llm_call(payload)
            parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            text_all = parsed.get("summary", "") + " " + " ".join(
                str(v) for v in parsed.get("arti", {}).values())
            ok, errors = validate_output(text_all, payload, cfg)
            if ok:
                return {"summary": parsed.get("summary", ""),
                        "arti": dict(parsed.get("arti", {}))}, True
            logger.warning("output LLM ditolak (attempt %d): %s", attempt + 1, errors)
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
            logger.warning("output LLM tidak valid (attempt %d): %s", attempt + 1, e)
        except Exception:
            logger.exception("panggilan LLM gagal — fallback template")
            break
    return template_narrative(payload), False
