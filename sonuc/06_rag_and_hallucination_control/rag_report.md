# RAG & Hallucination Prevention Architecture

## 1. Objective
To provide human dispatchers with natural language explanations for complex algorithmic route changes, without ever providing factually incorrect or unsafe operational data.

## 2. The Retrieval-Augmented Generation (RAG) Flow
The RAG pipeline sits strictly *after* the optimization process is completed.

1. **Information Extraction**: The backend (`agent.py`) packages the current deterministic results (Before/After delay, travel times, condition changes).
2. **Retrieval**: `rag.py` uses FAISS/ChromaDB to search a vector database of *past routing scenarios* (e.g., "What happens when traffic is 100 on this specific route?").
3. **Context Injection**: The LLM prompt is constructed: 
   *System Prompt* + *Deterministic Math Facts* + *Retrieved Past Context*.
4. **Generation**: Ollama (Llama3.2) generates a response summarizing why the route was changed.

## 3. RAG Evaluation Metrics (Ragas Framework)
The system's RAG pipeline is evaluated using standard industry metrics:
- **Context Precision**: Did the retriever pull relevant past scenarios, or unrelated ones?
- **Faithfulness**: Is the LLM's explanation strictly faithful to the context and deterministic math provided? (E.g., if math says 15 mins saved, does the LLM say 15 mins?)
- **Answer Relevancy**: Did the LLM actually answer the dispatcher's question, or ramble?
- **Hallucination Pass Rate**: A strict Boolean metric. Does the LLM output contain any numerical value that contradicts the `Deterministic Math Facts`?

## 4. Hallucination Grader Logic
LLMs are inherently probabilistic and prone to hallucinating numbers. In logistics, telling a driver "Your route takes 40 minutes" when it actually takes 120 is critically dangerous.

### The Grader Architecture:
1. The LLM produces an output text.
2. A deterministic regex/NER function scans the output for numerical quantities (minutes, distances, stop counts).
3. The function cross-references these numbers against the `optimization_delta` JSON provided by OR-Tools.
4. **Decision**: If a number in the text is not present in the JSON, the output is flagged as a hallucination (`hallucination_pass = False`).

### The Fallback Mechanism
If `hallucination_pass == False`, the system entirely discards the LLM's response. It falls back to a hardcoded deterministic string: 
*"Deterministic analysis: Route reoptimized under updated conditions. Expected saving: X minutes."*
This ensures 100% safety for the dispatcher UI.

## 5. Penalty / Loss in RAG
While RAG isn't "trained" with a loss function in real-time, the *evaluation pipeline* penalizes the system if Faithfulness drops below 0.90. The "penalty" in production is the discarding of the AI response (fallback usage), ensuring zero cost to operational safety.
