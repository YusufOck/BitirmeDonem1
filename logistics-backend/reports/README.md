# Evaluation Reports

Run from `logistics-backend`:

```powershell
python reports/generate_evaluation_reports.py
```

The script saves figures and summary files under `evaluation_results/`.

Generated outputs:

- `actual_vs_predicted_delay.png`
- `delay_error_distribution.png`
- `planned_vs_actual_travel_time.png`
- `evaluation_summary.csv`
- `data_gap_report.md`

The report intentionally avoids fake RAG or hallucination scores. If labeled RAG evaluation data is added later, use the format described in `data_gap_report.md`.
