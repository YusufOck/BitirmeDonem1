"""
ai/explainer.py
---------------
Ollama-backed natural language explainer for route optimization decisions.

Converts structured ML + VRP output into plain-language summaries that
dispatchers can act on immediately — no ML background required.

Deployment modes
~~~~~~~~~~~~~~~~
Local dev (uvicorn):   OLLAMA_BASE_URL=http://localhost:11434  (default)
Docker / VM:           OLLAMA_BASE_URL=http://ollama:11434     (set in .env)

The service name "ollama" is the docker-compose service name you will add
when deploying to a VM.  Locally, Ollama runs on its default port.

Environment variables
~~~~~~~~~~~~~~~~~~~~~
OLLAMA_BASE_URL   Ollama server URL         (default: http://localhost:11434)
OLLAMA_MODEL      Model to use              (default: llama3.2)
OLLAMA_TIMEOUT    Request timeout seconds   (default: 60)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import requests


# ── Configuration — read once at import, can be overridden per-call ───────────

_DEFAULT_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_DEFAULT_MODEL    = os.getenv("OLLAMA_MODEL", "llama3.2")
_TIMEOUT_SECONDS  = int(os.getenv("OLLAMA_TIMEOUT", "60"))


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(route_summary: dict, optimized_route: list[dict], use_p90: bool) -> str:
    """Compose a dispatcher-focused prompt from the optimization result."""

    risk_label  = "worst-case (P90)" if use_p90 else "expected (P50)"
    total_stops = len(optimized_route)

    high_risk  = route_summary.get("high_risk_stop_count", 0)
    severe     = route_summary.get("severe_stop_count", 0)
    route_prob = route_summary.get("route_delay_probability", 0)
    exp_delay  = route_summary.get("expected_total_delay_min", 0)
    wc_delay   = route_summary.get("worst_case_total_delay_min")
    vrp_status = route_summary.get("vrp_status", "unknown")
    dropped    = route_summary.get("dropped_stop_indices", [])
    depart_shift = route_summary.get("recommended_departure_shift_min", exp_delay)

    wc_text      = f"{wc_delay:.1f} min" if wc_delay is not None else "N/A"
    dropped_text = (
        f"{len(dropped)} stop(s) could not be scheduled"
        if dropped
        else "all stops scheduled"
    )

    # Per-stop lines — keep prompt compact
    stop_lines = []
    for s in optimized_route:
        name     = s.get("stop_name") or f"Stop {s.get('optimized_position', 0) + 1}"
        risk     = s.get("risk_level", "low")
        prob     = s.get("delay_probability", 0)
        exp      = s.get("expected_delay_min", 0)
        miss     = s.get("will_miss_window", False)
        severity = s.get("severity", "on-time")
        stop_lines.append(
            f"  - {name}: risk={risk}, delay_prob={prob:.0%}, "
            f"expected_delay={exp:.1f}min, miss_window={miss}, severity={severity}"
        )

    stops_text = "\n".join(stop_lines)

    # Reorder context — which stops moved and why (original index vs optimized position)
    reorder_lines = []
    sorted_by_orig = sorted(optimized_route, key=lambda s: s.get("original_stop_index", 0))
    for s in sorted_by_orig:
        orig    = s.get("original_stop_index", 0)
        opt_pos = s.get("optimized_position", orig)
        vid     = s.get("vehicle_id", 0)
        name    = s.get("stop_name") or f"Stop {orig + 1}"
        slack   = s.get("time_window_slack_min", 0)
        risk    = s.get("risk_level", "low")
        miss    = s.get("will_miss_window", False)
        moved   = orig != opt_pos
        reorder_lines.append(
            f"  - {name}: original position #{orig + 1} → "
            f"vehicle {vid} position #{opt_pos + 1}"
            f"{' [MOVED]' if moved else ''}"
            f" | slack={slack:.0f}min, risk={risk}, miss_window={miss}"
        )

    reorder_text = "\n".join(reorder_lines)

    return f"""You are an AI assistant for logistics dispatchers at a delivery company.
Analyze the following route optimization result and write a clear, actionable summary.

ROUTE OVERVIEW:
- Total stops: {total_stops}
- Route delay probability: {route_prob:.0%}
- Expected total delay: {exp_delay:.1f} min ({risk_label})
- Worst-case total delay: {wc_text}
- Recommended departure shift: depart {depart_shift:.0f} min earlier than planned
- High-risk stops: {high_risk}
- Severe stops: {severe}
- VRP solver status: {vrp_status}
- Scheduling: {dropped_text}

STOP REORDERING (original plan vs optimised):
{reorder_text}

PER-STOP BREAKDOWN (optimised order):
{stops_text}

INSTRUCTIONS:
Return ONLY a valid JSON object (no markdown, no code fences, no extra text) with exactly these fields:
{{
  "overall_assessment": "<2-3 sentences summarizing the route risk and main concern>",
  "risk_factors": ["<top risk 1>", "<top risk 2>", "<top risk 3>"],
  "recommendations": ["<specific dispatcher action 1>", "<specific dispatcher action 2>"],
  "reorder_rationale": [
    "<one sentence per MOVED stop explaining why it was repositioned, e.g. 'Stop X moved earlier because its delivery window closes in Y min and it had Z% delay risk'>"
  ],
  "stop_alerts": [
    {{"stop_name": "<stop name>", "message": "<1-sentence alert>"}}
  ]
}}

Rules:
- overall_assessment: plain language, no ML jargon, written for a non-technical dispatcher
- risk_factors: maximum 3 items, each under 15 words
- recommendations: 1-3 concrete steps the dispatcher should take right now, include the departure shift if non-zero
- reorder_rationale: one entry per stop marked [MOVED]; empty list if no stops moved
- stop_alerts: include ONLY stops with risk_level=high OR will_miss_window=true; empty list if none
"""


# ── JSON parser ────────────────────────────────────────────────────────────────

def _parse_llm_json(text: str) -> dict[str, Any]:
    """
    Extract and parse the JSON object from LLM output.

    Strategy:
    1. Direct json.loads() — works when the model behaves
    2. Find first { … } block — handles markdown fences or leading text
    3. Fallback — return raw text as overall_assessment with a warning
    """
    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Extract first {...} block
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

    # 3. Fallback — preserve the raw text so dispatchers still see something
    return {
        "overall_assessment": text,
        "risk_factors": [],
        "recommendations": [],
        "stop_alerts": [],
        "parse_warning": (
            "Ollama did not return valid JSON. "
            "Raw model output is included as overall_assessment."
        ),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_explanation(
    route_summary: dict,
    optimized_route: list[dict],
    use_p90: bool = False,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """
    Call Ollama and return a dispatcher-friendly explanation of the optimized route.

    Parameters
    ----------
    route_summary    : RouteSummary dict from the optimization pipeline
    optimized_route  : list of OptimizedStop dicts in delivery order
    use_p90          : whether the pipeline ran in P90 (worst-case) mode
    base_url         : Ollama server URL override (falls back to OLLAMA_BASE_URL env var)
    model            : model name override (falls back to OLLAMA_MODEL env var)

    Returns
    -------
    dict with keys:
        overall_assessment  str
        risk_factors        list[str]
        recommendations     list[str]
        stop_alerts         list[{stop_name, message}]
        model_used          str
        generation_time_ms  int

    On Ollama unavailability or error, returns a dict with an ``error`` key
    instead of raising — the caller decides whether to treat this as fatal.
    """
    resolved_url   = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    resolved_model = model or _DEFAULT_MODEL

    prompt = _build_prompt(route_summary, optimized_route, use_p90)

    payload: dict[str, Any] = {
        "model": resolved_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {
            "temperature": 0.3,  # low temp → consistent, predictable dispatcher output
            "num_predict": 600,  # enough for the JSON response
        },
    }

    t0 = time.monotonic()

    try:
        resp = requests.post(
            f"{resolved_url}/api/chat",
            json=payload,
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        return {
            "error": (
                f"Ollama is not reachable at {resolved_url}. "
                "For local dev: run `ollama serve`. "
                "For Docker/VM: check OLLAMA_BASE_URL and that the ollama service is up."
            )
        }
    except requests.exceptions.Timeout:
        return {"error": f"Ollama request timed out after {_TIMEOUT_SECONDS}s. Try a smaller model or increase OLLAMA_TIMEOUT."}
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:300] if exc.response is not None else ""
        return {"error": f"Ollama returned HTTP {exc.response.status_code}: {body}"}

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    try:
        raw_text = resp.json()["message"]["content"].strip()
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        return {"error": f"Unexpected Ollama response format: {exc}"}

    parsed = _parse_llm_json(raw_text)
    parsed["model_used"]          = resolved_model
    parsed["generation_time_ms"]  = elapsed_ms
    return parsed


def check_ollama_health(base_url: str | None = None) -> dict[str, Any]:
    """
    Lightweight health check — calls GET /api/tags to verify Ollama is reachable
    and returns available models.

    Used by the /api/v1/explain/health endpoint.
    """
    resolved_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    try:
        resp = requests.get(f"{resolved_url}/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        return {
            "status": "ok",
            "ollama_url": resolved_url,
            "configured_model": _DEFAULT_MODEL,
            "available_models": models,
            "model_ready": _DEFAULT_MODEL in models or any(
                m.startswith(_DEFAULT_MODEL.split(":")[0]) for m in models
            ),
        }
    except requests.exceptions.ConnectionError:
        return {
            "status": "unreachable",
            "ollama_url": resolved_url,
            "configured_model": _DEFAULT_MODEL,
            "hint": (
                "Local: run `ollama serve` and `ollama pull llama3.2`. "
                "Docker/VM: set OLLAMA_BASE_URL=http://ollama:11434 in .env."
            ),
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
