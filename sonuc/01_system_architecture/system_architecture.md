# System Architecture Report

## 1. Project Purpose
The SBTU Logistics Smart Dispatch System is a real-time, AI-powered logistics platform designed to plan, monitor, and adapt courier delivery routes dynamically. It integrates machine learning (ML) for delay prediction, Operations Research (OR) tools for route optimization, Mapbox for geographic data, and a Large Language Model (LLM) powered by Retrieval-Augmented Generation (RAG) to explain optimization decisions to human dispatchers.

## 2. General Architecture Overview
The system follows a microservices-oriented architecture orchestrated via Docker Compose. It leverages an event-driven model using WebSocket and Redis for real-time live monitoring.

The system is composed of five primary containers:
1. **Frontend (React + Vite)**: Provides a responsive, real-time dashboard for the dispatcher.
2. **Backend (FastAPI)**: Serves as the core orchestrator. It manages the REST API, WebSocket streams, ML predictions, route optimizations, and LLM agent interactions.
3. **Database (PostgreSQL)**: Stores static data, historical performance logs, metrics, and route plans.
4. **Cache & Pub/Sub (Redis)**: Manages fast state caching, active route states, and serves as the Pub/Sub broker for real-time WebSocket events.
5. **AI Engine (Ollama)**: Hosts the local Llama3.2 model for generating route explanations.

## 3. Core Components

### 3.1. Frontend
- **Framework**: React, built with Vite.
- **State Management**: Zustand, which manages complex localized state and synchronizes with the backend via REST polling and WebSocket events.
- **Real-Time Map**: Mapbox GL JS is used for rendering the geographic locations, planned routes, and live courier positions.
- **Pages**: Dashboard, Route Planner, Live Monitor (Scenarios), and Admin System Reports.

### 3.2. Backend
- **Framework**: FastAPI (Python 3.12).
- **Core Modules**:
  - `api/routes.py`: REST endpoints.
  - `optimization/vrp_solver.py`: OR-Tools Vehicle Routing Problem formulation.
  - `ml/predict.py`: Delay risk prediction using CatBoost.
  - `ai/agent.py` & `ai/rag.py`: The LLM logic, guardrails, and RAG retrieval.
  - `mapbox/client.py`: External requests to Mapbox Directions and Matrix APIs.
- **WebSocket Manager**: Streams location events (`at_stop`, `moving`) and background LLM response streaming to the UI.

### 3.3. External APIs
- **Mapbox Matrix API**: Fetches NxN travel time matrices.
- **Mapbox Directions API**: Fetches the exact road geometries for routing.

### 3.4. AI & ML Subsystems
- **Machine Learning**: A pre-trained CatBoostRegressor predicts the specific minute-level delay expected at any stop based on weather, traffic, load, and historical probabilities.
- **AI Agent (Ollama)**: Provides deterministic analysis and natural language explanations. It answers "Why was this route chosen?" using RAG on backend data, and is strictly guarded by a hallucination evaluator.

## 4. Data Flow
1. **Dispatch Request**: The dispatcher selects a route and requests optimization.
2. **Matrix Acquisition**: The backend fetches an NxN matrix from Mapbox.
3. **ML Prediction**: The ML model enriches every possible leg in the matrix with delay risks.
4. **Solver Execution**: OR-Tools solves the VRP using the combined cost (Travel Time + ML Delay + Penalty).
5. **Explanation (AI/RAG)**: The deterministic evaluator extracts metrics, while Ollama generates a human-readable justification.
6. **Live Execution**: The frontend displays the new plan. Once dispatched, a simulation loop publishes courier locations to Redis, which FastAPI broadcasts to the React app via WebSockets.

## 5. Architectural Limits
- **CPU Constraints**: Ollama relies on local CPU inference, making it latency-sensitive. Thus, generation is offloaded asynchronously, with a deterministic fallback to prevent UI blocking.
- **VRP Scale**: Mapbox Matrix API supports a maximum of 25 coordinates per request unless paginated. Large scale routing relies on chunking.
