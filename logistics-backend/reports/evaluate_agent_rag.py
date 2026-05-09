"""
RAG Pipeline Evaluation Script
Run from project root: python reports/evaluate_agent_rag.py
"""
import os
import sys

# TASK 1 FIX: Ensure project root is on sys.path BEFORE any project imports
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pandas as pd
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from ai.agent import process_recommendation

DATASET_PATH = os.path.join(ROOT_DIR, "data", "rag_evaluation_dataset.csv")
OUTPUT_DIR = os.path.join(ROOT_DIR, "evaluation_results", "rag")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def evaluate_rag():
    print(f"Loading RAG evaluation dataset from {DATASET_PATH}...")
    if not os.path.exists(DATASET_PATH):
        print("Dataset not found. Run from backend root.")
        return
        
    df = pd.read_csv(DATASET_PATH)
    
    results = []
    
    for idx, row in df.iterrows():
        question = row["question"]
        expected = row["expected_answer"]
        facts = json.loads(row["backend_facts_json"])
        
        # Mocking minimal input required for the agent
        metrics = {"before": {"expected_delay_min": 0.0}, "after": {"expected_delay_min": facts.get("delay_delta_min", 0.0)}}
        conditions = {}
        stop_order_before = ["A", "B"]
        stop_order_after = ["B", "A"] if facts.get("order_changed") else ["A", "B"]
        
        print(f"Evaluating Q{idx+1}: {question}")
        agent_resp = process_recommendation(
            route_id=0,
            metrics=metrics,
            conditions=conditions,
            stop_order_before=stop_order_before,
            stop_order_after=stop_order_after,
            user_question=question
        )
        
        retriever_score = agent_resp.get("retriever_grade", {}).get("score", 0.0)
        
        # TASK 4: Use generation_status instead of hallucination for generation failures
        gen_status = agent_resp.get("generation_status", "unknown")
        hall_grade = agent_resp.get("hallucination_grade", {})
        
        if gen_status == "success":
            hallucination_pass = 1.0 if hall_grade.get("should_answer", False) else 0.0
        else:
            # Generation failed — hallucination grading is not applicable
            hallucination_pass = float("nan")
        
        answer_score = agent_resp.get("answer_grade", {}).get("score", 0.0)
        
        # Context Precision
        required_keywords = [k.strip().lower() for k in str(row["required_context_keywords"]).split(",")]
        retrieved_text = " ".join([c["content"].lower() for c in agent_resp.get("retrieved_context", [])])
        keyword_hits = sum(1 for k in required_keywords if k in retrieved_text)
        context_precision = keyword_hits / len(required_keywords) if required_keywords else 0.0
        
        results.append({
            "Question": question,
            "Category": row["category"],
            "Retriever_Score": retriever_score,
            "Context_Precision": round(context_precision, 4),
            "Hallucination_Pass": hallucination_pass,
            "Answer_Score": answer_score,
            "Fallback_Used": 1 if agent_resp.get("fallback_used") else 0,
            "Generation_Status": gen_status,
            "AI_Explanation_Length": len(agent_resp.get("ai_explanation", "")),
            "Ollama_URL": agent_resp.get("ollama_url_used", ""),
            "Generation_Error": agent_resp.get("generation_error", ""),
        })

    # Save CSV
    res_df = pd.DataFrame(results)
    csv_path = os.path.join(OUTPUT_DIR, "rag_evaluation_results.csv")
    res_df.to_csv(csv_path, index=False)
    print(f"\nSaved results to {csv_path}")
    
    # Summary Statistics (exclude NaN for hallucination when generation failed)
    numeric_cols = ["Retriever_Score", "Context_Precision", "Hallucination_Pass", "Answer_Score", "Fallback_Used"]
    summary = res_df[numeric_cols].mean(skipna=True).to_frame(name="Average Score")
    summary_path = os.path.join(OUTPUT_DIR, "rag_summary_statistics.csv")
    summary.to_csv(summary_path)
    print(f"Saved summary to {summary_path}")
    
    # Print summary table
    print("\n" + "=" * 60)
    print("  RAG Evaluation Summary")
    print("=" * 60)
    for metric, val in summary.iterrows():
        print(f"  {metric:<25s}: {val['Average Score']:.4f}")
    gen_success = sum(1 for r in results if r["Generation_Status"] == "success")
    gen_failed = sum(1 for r in results if r["Generation_Status"] == "failed")
    print(f"\n  Generation Success: {gen_success}/{len(results)}")
    print(f"  Generation Failed:  {gen_failed}/{len(results)}")
    if gen_failed > 0:
        errors = set(r["Generation_Error"] for r in results if r["Generation_Error"])
        for e in errors:
            print(f"    Error: {e}")
    print("=" * 60)
    
    # Graphs
    # 1. Heatmap
    plt.figure(figsize=(10, 6))
    plot_cols = ["Retriever_Score", "Context_Precision", "Hallucination_Pass", "Answer_Score"]
    heatmap_data = res_df.set_index("Question")[plot_cols].fillna(0.0)
    plt.imshow(heatmap_data.values, cmap="YlGnBu", aspect='auto', vmin=0, vmax=1)
    
    for i in range(len(heatmap_data.index)):
        for j in range(len(heatmap_data.columns)):
            val = heatmap_data.values[i, j]
            plt.text(j, i, f'{val:.2f}', ha='center', va='center', color='black', fontsize=9)
            
    plt.colorbar(label='Score')
    plt.xticks(range(len(heatmap_data.columns)), heatmap_data.columns, rotation=45, ha='right')
    plt.yticks(range(len(heatmap_data.index)), [f"Q{i+1}" for i in range(len(heatmap_data.index))])
    plt.title("RAG Evaluation Metric Heatmap")
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "rag_metric_heatmap.png"), dpi=150)
    plt.close()
    
    # 2. Average Scores Bar Chart
    plt.figure(figsize=(8, 5))
    summary.plot(kind="bar", legend=False, color="skyblue", edgecolor="black")
    plt.title("Average RAG Pipeline Scores")
    plt.ylabel("Score (0.0 to 1.0)")
    plt.xticks(rotation=45, ha='right')
    plt.ylim(0, 1.1)
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, "rag_average_scores.png"), dpi=150)
    plt.close()
    
    print("Graphs generated successfully.")

if __name__ == "__main__":
    evaluate_rag()
