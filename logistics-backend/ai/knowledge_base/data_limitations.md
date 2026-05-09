# Data Limitations
The system operates with explicit limitations:
1. **Synthetic Training Data**: The CSV features used to train the v9 ML model (`merged_features_v2.csv`) contain synthetic stress-test data. This data was generated to simulate extreme edge cases (e.g., severe weather, accidents).
2. **Missed-Window Rate**: The historical data has an artificially high missed-window rate (~85%) due to this stress-testing. 
3. **Geometric Mismatch**: The historical route durations in the CSV are based on straight-line distances and do not perfectly align with real-world Mapbox road network durations.
4. **Visual Truth**: Because of the CSV geometric mismatch, the system NEVER uses CSV data for visual road geometry. All map routing and live durations are sourced exclusively from the Mapbox Directions API.
5. **No Live Traffic Feed**: The current MVP does not have a real-time live traffic API hook. Dispatchers manually input "Scenario Conditions" to simulate traffic for the Agent to process.
