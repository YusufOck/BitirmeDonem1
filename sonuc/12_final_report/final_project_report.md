# Logistics Dashboard & AI Optimization: Final Project Report

## 1. Executive Summary
The SBTU Logistics Smart Dispatch System is a comprehensive platform designed to modernize delivery fleet management. By combining Operations Research, Machine Learning, and Generative AI, the system solves the dynamic Vehicle Routing Problem (VRP). It allows dispatchers to plan routes, track couriers in real-time, and dynamically re-optimize remaining stops when real-world conditions (weather, traffic, accidents) change, while receiving natural-language explanations for those changes.

## 2. Problem Definition
Traditional routing engines rely solely on static distance or travel time matrices. They fail to account for:
- Historical stop-level delays (e.g., a specific building always takes 15 minutes longer to deliver to).
- Real-time condition shifts (e.g., sudden snowfall during an active dispatch).
- The "Black Box" problem: Dispatchers do not trust algorithmically altered routes if they cannot understand *why* the sequence was changed.

## 3. Proposed Solution
This system introduces:
- **CatBoost ML Model**: Predicts stop-level operational delays based on contextual features.
- **OR-Tools Solver**: Incorporates both Mapbox travel times and the ML delay predictions into a unified cost penalty function.
- **Explainable AI (RAG + Llama3.2)**: Explains route recalculations using local, secure Generative AI, guarded by a strict hallucination prevention mechanism.

## 4. System Architecture
The platform is containerized via Docker Compose.
- **Frontend**: React + Vite + Zustand + Mapbox GL JS.
- **Backend**: FastAPI (Python) orchestrating REST APIs and WebSocket Pub/Sub.
- **Data & Cache**: PostgreSQL (Persistent storage) and Redis (State caching and WebSocket broker).
- **AI/ML**: Local Ollama execution for Llama3.2, ensuring zero external data leakage for corporate logistics.

## 5. Mathematical Background (Routing)
The routing engine seeks to minimize the objective function `C(S)`.
The cost between two nodes is heavily modified by active scenarios:
- **Travel Time Inflation**: `Multiplier = 1.0 + Traffic% + Weather% + Accident% + Disruption%`
- **Time Window Enforcement**: `Schedule Delay = max(0, Arrival_ETA - Slack_Time)`. Placing a stop too late in the route causes this penalty to grow exponentially, forcing the solver to prioritize tight-window deliveries.

## 6. AI Architecture & Hallucination Prevention
A critical innovation in this project is the separation of AI duties:
1. **The Math Decides**: The LLM does *not* route vehicles. OR-Tools selects the mathematical optimal sequence.
2. **The LLM Explains**: The LLM receives the numerical differences (e.g., "15 minutes saved, 3 stops reordered") and explains them contextually.
3. **The Validator Guards**: A deterministic regex-based grader scans the LLM's output. If the LLM invents a numerical value that isn't in the math (Hallucination), the response is destroyed and replaced with a safe, hardcoded string.

## 7. Testing & Verification
The system was validated across multiple dimensions:
- **Backend**: 21 PyTest cases verify endpoint stability, data serialization, and edge cases.
- **RAG Pipeline**: Evaluated for Context Precision, Faithfulness, and Hallucination Pass Rate.
- **Logical Verification**: Custom python scripts verify that applied scenarios strictly preserve completed stops and only optimize remaining segments.

## 8. Limitations and Future Work
- **CPU Bottlenecks**: Running Llama3.2 locally on CPU introduces latency. The architecture mitigates this by running the explanation asynchronously without blocking the UI.
- **Mapbox API Limits**: The Mapbox Matrix API caps requests at 25x25. Future implementations should chunk matrices or use OSRM locally.
- **Synthetic Data**: The ML model was trained on generated scenarios. Future work requires real-world company data for production-level delay accuracy.

## 9. Conclusion
This project successfully demonstrates that advanced AI capabilities can be safely integrated into mission-critical logistics pipelines. By strictly enforcing deterministic guardrails, the system benefits from the conversational clarity of LLMs without inheriting their risks of hallucination.
