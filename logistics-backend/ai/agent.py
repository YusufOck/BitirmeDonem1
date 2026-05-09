from typing import Dict, Any, List
import requests
import json
import os
import logging
from .retriever import retrieve_context
from .graders import grade_retrieval, grade_hallucination, grade_answer

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

def generate_deterministic_explanation(metrics: Dict[str, Any], conditions: Dict[str, Any], order_changed: bool) -> str:
    delay_delta = metrics.get("after", {}).get("expected_delay_min", 0.0) - metrics.get("before", {}).get("expected_delay_min", 0.0)
    order_text = "changed the stop order" if order_changed else "kept the same stop order"
    
    cond_texts = []
    if conditions.get("traffic_density", 0) > 0:
        cond_texts.append(f"traffic density at {conditions['traffic_density']}/100")
    if conditions.get("accident_severity", 0) > 0:
        cond_texts.append(f"accident severity at {conditions['accident_severity']}/100")
    if conditions.get("weather_severity", 0) > 0:
        cond_texts.append(f"weather severity at {conditions['weather_severity']}/100")
        
    cond_str = ", ".join(cond_texts) if cond_texts else "standard conditions"
    
    return (
        f"The scenario was evaluated using ML delay features for {cond_str}. "
        f"OR-Tools {order_text} to minimize total cost. "
        f"Expected delay changed by {delay_delta:+.1f} min."
    )

def generate_ai_explanation(prompt: str) -> Dict[str, Any]:
    """
    Calls local Ollama API. Returns a dict with the generated text and diagnostics.
    """
    url = f"{OLLAMA_BASE_URL}/api/generate"
    result = {
        "text": "",
        "ollama_url_used": url,
        "ollama_model_used": OLLAMA_MODEL,
        "generation_error": "",
        "ai_generation_attempted": True,
    }
    try:
        logger.info(f"Calling Ollama at {url} with model {OLLAMA_MODEL}")
        resp = requests.post(
            url,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1}},
            timeout=60  # 60s to handle cold-start model loading
        )
        if resp.status_code == 200:
            result["text"] = resp.json().get("response", "").strip()
            if not result["text"]:
                result["generation_error"] = "Ollama returned 200 but response was empty."
        else:
            result["generation_error"] = f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}"
    except requests.exceptions.Timeout:
        result["generation_error"] = f"Ollama request timed out after 30s at {url}"
        logger.warning(result["generation_error"])
    except requests.exceptions.ConnectionError as e:
        result["generation_error"] = f"Cannot connect to Ollama at {url}: {e}"
        logger.warning(result["generation_error"])
    except Exception as e:
        result["generation_error"] = f"Ollama generation failed: {type(e).__name__}: {e}"
        logger.warning(result["generation_error"])
    return result

def process_recommendation(
    route_id: int,
    metrics: Dict[str, Any],
    conditions: Dict[str, Any],
    stop_order_before: List[str],
    stop_order_after: List[str],
    user_question: str = ""
) -> Dict[str, Any]:
    """Agent pipeline coordinating tools, RAG, and graders."""
    
    # 1. Deterministic Baseline
    order_changed = stop_order_before != stop_order_after
    backend_facts = {
        "delay_delta_min": metrics.get("after", {}).get("expected_delay_min", 0.0) - metrics.get("before", {}).get("expected_delay_min", 0.0),
        "order_changed": order_changed
    }
    deterministic_exp = generate_deterministic_explanation(metrics, conditions, order_changed)
    
    # 2. Retrieval
    retrieved_context = []
    retriever_grade = {"pass": False, "score": 0.0, "reason": "No query provided", "relevant_chunks": []}
    
    if user_question:
        retrieved_context = retrieve_context(user_question, top_k=3)
        retriever_grade = grade_retrieval(user_question, retrieved_context)
    
    # 3. AI Generation
    context_text = "\n\n".join([c["content"] for c in retriever_grade.get("relevant_chunks", [])])
    
    prompt = f"""You are a logistics dispatch assistant. 
Explain this route change concisely based on the facts provided. Do not hallucinate.

[BACKEND FACTS]
Delay changed by: {backend_facts['delay_delta_min']:+.1f} min.
Stop order changed: {order_changed}
User Scenario Conditions applied: {json.dumps(conditions)}

[KNOWLEDGE BASE CONTEXT]
{context_text if context_text else 'No specific policy context retrieved.'}

[USER QUESTION]
{user_question if user_question else 'Explain the routing decision briefly.'}

Answer directly and concisely in 2-4 sentences:
"""
    gen_result = generate_ai_explanation(prompt)
    ai_exp = gen_result["text"]
    
    # 4. Grading — TASK 4 FIX: Separate generation failure from hallucination
    if ai_exp:
        generation_status = "success"
        hallucination_grade = grade_hallucination(ai_exp, backend_facts)
        answer_grade = grade_answer(ai_exp)
    else:
        generation_status = "failed"
        # Do NOT call this hallucination — it's a generation failure
        hallucination_grade = {
            "hallucination_risk": "not_applicable",
            "should_answer": False,
            "detected_issues": ["AI generation failed or returned empty — cannot evaluate hallucination without text."],
            "safe_fallback_explanation": None,
        }
        answer_grade = {"pass": False, "score": 0.0, "feedback": "No AI text generated to evaluate."}
        
    # 5. Output Contract
    fallback_used = not hallucination_grade["should_answer"] or not answer_grade["pass"]
    final_explanation = deterministic_exp if fallback_used else ai_exp
    
    return {
        "deterministic_explanation": deterministic_exp,
        "ai_explanation": ai_exp,
        "retrieved_context": retrieved_context,
        "retriever_grade": retriever_grade,
        "hallucination_grade": hallucination_grade,
        "answer_grade": answer_grade,
        "final_explanation": final_explanation,
        "should_show_ai": not fallback_used,
        "fallback_used": fallback_used,
        # Diagnostics metadata (TASK 3)
        "generation_status": generation_status,
        "generation_error": gen_result["generation_error"],
        "ollama_url_used": gen_result["ollama_url_used"],
        "ollama_model_used": gen_result["ollama_model_used"],
        "ai_generation_attempted": gen_result["ai_generation_attempted"],
    }
