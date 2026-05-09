# Dispatch Policy
A route follows an explicit lifecycle. It begins as `planned`, which represents the original stop assignment before mathematical optimization. Once OR-Tools evaluates the Mapbox matrix and ML delay scores, it proposes a new sequence, transitioning the state to `optimized_available`. When a dispatcher approves the vehicle to depart, the state becomes `dispatched` or `in_progress`.
During transit, if traffic, weather, or road closures occur, the Agent may propose an alternative sequence. The state becomes `recommendation_available`. If accepted, the state returns to `in_progress` until completion.
Dispatchers must always prioritize safety and avoid dispatching routes with a `red` or `critical` risk status without manual override.
