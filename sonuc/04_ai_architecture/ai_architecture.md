# AI Architecture Report

## 1. Overview of AI Integration
The logistics dispatch system uses a multi-layered AI approach, dividing responsibilities between predictive numerical Machine Learning (ML) and Generative AI (LLMs) to ensure reliable and verifiable routing decisions.

## 2. Component Roles

### 2.1. Delay Risk Predictor (Machine Learning)
- **Tool**: CatBoostRegressor
- **Purpose**: Provides highly accurate, purely deterministic predictions of delivery delays (in minutes). It analyzes inputs like `traffic_density`, `weather_severity`, `package_load`, and specific geographical features of each stop.
- **Used In**: Optimization Engine (OR-Tools). The ML delay is injected directly into the mathematical cost function to penalize risky routes before they are ever proposed.

### 2.2. Deterministic Grader / Validator (Rules Engine)
- **Purpose**: A hard-coded validation guardrail. It prevents the system from generating unsafe paths or hallucinating numbers.
- **Mechanism**: Extracts the `expected_delay_min` and stop orders from the backend structures, compares the Before vs. After results, and checks if the new route actually saves time.
- **Decision Authority**: The validator has full authority over the route apply process. If the recommended route misses stops or does not mathematically save time, the application is blocked.

### 2.3. Route Explainer Agent (Ollama + Llama3.2)
- **Tool**: Local Llama3.2 accessed via Ollama.
- **Purpose**: Translates the deterministic results into natural, easily understandable language for the dispatcher.
- **Input**: The deterministic metrics (delay before/after, travel time, and conditions) are passed as structured text.
- **RAG Implementation**: The agent pulls past routing knowledge from a local Vector Store/DB. This allows the model to reference why similar routes failed in the past.
- **Outputs**: Natural language explanations like, "The route was changed because traffic density spiked on the main road, making STP-00002 too risky."

### 2.4. Hallucination Control Pipeline
- The Agent is *strictly constrained* and is never allowed to "invent" a route.
- A secondary LLM step (or deterministic step) grades the agent's output against the ground-truth numerical facts provided to it.
- If the agent hallucinates (e.g., claims 50 minutes saved when only 10 were saved), the `hallucination_pass` metric fails, and the system falls back to a purely deterministic text string to ensure zero misinformation reaches the dispatcher.

## 3. Does the AI Make Decisions?
**No.** The Generative AI (LLM) *does not* decide the route.
1. The **ML Model** provides numerical risks.
2. The **OR-Tools Solver** mathematically selects the most optimal route.
3. The **AI Agent** only *explains* the decision that has already been made by the backend. It operates purely as an advisory explainer.

## 4. RAG Implementation Details
The Retrieval-Augmented Generation system provides context about the company's dispatching history.
- The `ai/rag.py` component retrieves the top-K relevant documents from past successful or failed route scenarios.
- This grounds the agent, ensuring it uses correct logistical terminology and refers to valid past decisions to increase faith in the current recommendation.
