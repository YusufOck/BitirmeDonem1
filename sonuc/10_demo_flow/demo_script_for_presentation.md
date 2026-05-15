# Demo Flow & Presentation Script

This script outlines the exact steps to demonstrate the logistics system to the evaluation committee.

## 1. Dashboard Overview
**Speaker Action**: Open the application at `http://localhost:5173`.
**Script**: "Welcome to the SBTU Logistics Smart Dispatch System. Here on the Dashboard, you see a high-level overview of our fleet. We have two couriers waiting for dispatch. Notice the metrics on the left: the system aggregates total planned stops and estimated delays."

## 2. Route Planner (Pre-Dispatch Optimization)
**Speaker Action**: Click "Route Planner" in the sidebar. Select "Courier 0".
**Script**: "Before we dispatch a courier, we must plan the route. On the right, you see the 'Delay Drivers'—these are stops with the highest Machine Learning predicted delay risks. The ML model predicts delay based on historical weather, traffic, and stop data."
**Speaker Action**: Click "Run Pre-dispatch Optimization".
**Script**: "We just asked the OR-Tools engine to find a better sequence. It combines Mapbox travel times with our ML delay risks. Notice how the 'Predicted delay' decreases as it finds a safer path avoiding those high-risk nodes."

## 3. Dispatch & Live Tracking
**Speaker Action**: Click "Start Dispatch". Then click "Live Monitor" in the sidebar.
**Script**: "The courier is now live. In a real-world scenario, the driver's app would send GPS pings. Here, our Redis/WebSocket simulation is acting as the GPS. You can see the progress bar updating as stops are marked 'Completed'."

## 4. Scenario Testing (Adapting to the Real World)
**Speaker Action**: On the Live Monitor page, adjust the "Weather severity" slider to 100 (e.g., Heavy Snow) and "Traffic density" to 100.
**Script**: "A blizzard just hit the city, and traffic is gridlocked. We need to adapt the remaining unvisited stops. The completed stops are locked in—we cannot optimize the past."
**Speaker Action**: Click "Recalculate for X remaining stops".
**Script**: "The backend just requested a new travel matrix, inflated it heavily for the snowstorm, asked the ML model for new delays, and OR-Tools calculated the new best sequence."

## 5. RAG & AI Explanation
**Speaker Action**: Scroll down to the "Recommendation explanation" panel.
**Script**: "Here is our AI Agent. The optimization math is complex, so we use a Llama3.2 model via Ollama to explain *why* the route changed. The agent uses RAG to pull similar past blizzards from the database to ground its explanation. Crucially, the AI is guarded by a deterministic hallucination evaluator—if it makes up fake times, the UI rejects it."

## 6. Validation and Application
**Speaker Action**: Show the "Delay improvement" panel.
**Script**: "Before we apply the new route, the system mathematically proves it saves time. Here we see the delay saved. If the scenario was impossible (no better route), the 'Apply' button would be blocked by our Validator."
**Speaker Action**: Click "Apply Recommendation".
**Script**: "The recommendation is applied. The map updates, and the courier continues seamlessly on the new optimal path."
