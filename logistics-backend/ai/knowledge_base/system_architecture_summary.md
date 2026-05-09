# System Architecture Summary
The SMART LOGISTICS AI Agent MVP operates on a 4-layer architecture:
1. **Data Layer**: Mapbox provides live road geometry and baseline travel durations. CSV files provide historical training data for the ML model.
2. **Prediction & Optimization Layer**: The v9 ML model predicts stop-level delay risk. OR-Tools computes the optimal stop sequence minimizing travel time and delay penalties.
3. **Agent & RAG Layer**: A coordination agent accesses backend metrics and retrieves context from the knowledge base to generate grounded, dispatcher-friendly explanations. Graders (Retriever, Hallucination, Answer) ensure safety.
4. **Frontend Lifecycle UI**: Displays explicit route states (`planned`, `optimized_available`, `dispatched`, `recommendation_available`) to the user.
