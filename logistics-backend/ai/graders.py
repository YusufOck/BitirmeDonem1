import re
from typing import List, Dict, Any

def grade_retrieval(query: str, retrieved_chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Retriever Grader: Checks if the retrieved context is relevant.
    MVP rule-based logic: Requires at least one chunk to share keywords with the query.
    """
    if not retrieved_chunks:
        return {"pass": False, "score": 0.0, "reason": "No context retrieved.", "relevant_chunks": []}
    
    query_words = set(re.findall(r'\w+', query.lower()))
    relevant_chunks = []
    
    for chunk in retrieved_chunks:
        chunk_words = set(re.findall(r'\w+', chunk["content"].lower()))
        overlap = query_words.intersection(chunk_words)
        # simple heuristic: if > 1 meaningful keyword overlaps, consider it relevant
        if len(overlap) >= 1 or chunk["score"] > 0.15:
            relevant_chunks.append(chunk)
            
    passed = len(relevant_chunks) > 0
    score = len(relevant_chunks) / len(retrieved_chunks) if retrieved_chunks else 0.0
    
    return {
        "pass": passed,
        "score": round(score, 2),
        "reason": "Found overlapping terms in context." if passed else "Context lacks relevance to the query.",
        "relevant_chunks": relevant_chunks
    }

def grade_hallucination(ai_text: str, backend_facts: Dict[str, Any]) -> Dict[str, Any]:
    """
    Hallucination Grader: Checks if AI contradicts backend facts.
    """
    ai_text_lower = ai_text.lower()
    issues = []
    risk = "low"
    
    # 1. Delay contradiction check
    delay_delta = backend_facts.get("delay_delta_min", 0.0)
    if delay_delta > 0 and ("decreased" in ai_text_lower or "reduced" in ai_text_lower) and "delay" in ai_text_lower:
        issues.append("AI claims delay decreased, but backend shows it increased.")
        risk = "high"
    elif delay_delta < 0 and ("increased" in ai_text_lower) and "delay" in ai_text_lower:
        issues.append("AI claims delay increased, but backend shows it decreased.")
        risk = "high"

    # 2. Stop order check
    order_changed = backend_facts.get("order_changed", False)
    if not order_changed and ("reordered" in ai_text_lower or "new sequence" in ai_text_lower or "changed the stop order" in ai_text_lower):
        issues.append("AI claims order changed, but backend shows it remained the same.")
        risk = "high"
        
    should_answer = risk != "high"
    
    safe_fallback = None
    if not should_answer:
        safe_fallback = (
            f"The scenario resulted in a delay change of {delay_delta:+.1f} min. "
            f"Stop order was {'changed' if order_changed else 'kept the same'} based on mathematical optimization."
        )

    return {
        "hallucination_risk": risk,
        "should_answer": should_answer,
        "detected_issues": issues,
        "safe_fallback_explanation": safe_fallback
    }

def grade_answer(ai_text: str) -> Dict[str, Any]:
    """
    Answer Grader: Checks if answer is concise and dispatcher-friendly.
    """
    words = ai_text.split()
    feedback = []
    passed = True
    
    if len(words) < 5:
        feedback.append("Answer is too short.")
        passed = False
    if len(words) > 100:
        feedback.append("Answer is too long and verbose.")
        passed = False
        
    ai_text_lower = ai_text.lower()
    if not ("delay" in ai_text_lower or "time" in ai_text_lower or "order" in ai_text_lower or "stop" in ai_text_lower):
        feedback.append("Answer does not mention key operational metrics (delay, order, stop).")
        passed = False
        
    return {
        "pass": passed,
        "score": 1.0 if passed else 0.5,
        "feedback": " ".join(feedback) if feedback else "Answer is appropriate."
    }
