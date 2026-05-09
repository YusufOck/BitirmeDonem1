# Delay Risk Interpretation
The system uses the v9 machine learning model to predict expected delay minutes at each stop. 
Risk is categorized into visual statuses:
- Green (Low Risk): Delay < 5 min.
- Amber (Medium Risk): Delay between 5 and 15 min. High-risk stop count increments.
- Red (Severe Risk): Delay >= 15 min. Severe-risk stop count increments.
If a route has 2 or more severe stops, or its cumulative delay exceeds 25 minutes, the overall route is marked Red. The dispatcher should review these routes and consider splitting the payload, reassigning it, or accepting an optimized route that avoids the congestion.
