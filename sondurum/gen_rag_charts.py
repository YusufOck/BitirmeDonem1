"""RAG evaluation charts matching professor's expected format."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import seaborn as sns
import os

OUT = r"c:\Users\mehmet\Desktop\bitirme1\sondurum"

# ── RAG test data (5 questions × 5 metrics) ────────────────────────────────────
questions = ['Q1', 'Q2', 'Q3', 'Q4', 'Q5']
metrics   = ['Nv Context\nRelevance', 'Lim Context Precision\nWith Reference', 'Context Recall', 'Faithfulness', 'Answer\nRelevancy']

scores = np.array([
    [0.75, 1.00, 0.00, 0.857, 0.504],
    [1.00, 0.50, 0.75, 0.778, 0.000],
    [0.50, 0.33, 0.20, 0.667, 0.569],
    [1.00, 0.42, 0.50, 1.000, 0.600],
    [1.00, 1.00, 1.00, 1.000, 0.525],
])

means = scores.mean(axis=0)
# [0.850, 0.650, 0.490, 0.860, 0.440]

# ── 1. Average Metric Scores (bar chart) ──────────────────────────────────────
fig, ax = plt.subplots(figsize=(10, 6))
colors = ['#4472c4', '#4472c4', '#4472c4', '#70ad47', '#70ad47']
bars = ax.bar(metrics, means, color=colors, edgecolor='white', width=0.55)
for bar, val in zip(bars, means):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01, f'{val:.3f}',
            ha='center', fontsize=11, fontweight='bold')
ax.set_ylim(0, 1.15)
ax.set_ylabel('Average Score', fontsize=12)
ax.set_title('RAG System Evaluation - Average Metric Scores', fontsize=14, fontweight='bold')
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.set_xticklabels(metrics, fontsize=9)
plt.tight_layout()
plt.savefig(os.path.join(OUT, '09_rag_average_scores.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved 09_rag_average_scores.png")

# ── 2. Summary Statistics Table ───────────────────────────────────────────────
col_labels = ['Nv Context\nRelevance', 'Lim Context Precision\nWith Reference', 'Context Recall', 'Faithfulness', 'Answer\nRelevancy']
row_labels = ['count', 'mean', 'std', 'min', '25%', '50%', '75%', 'max']

def stats_col(col):
    return [
        f'{len(col):.1f}',
        f'{np.mean(col):.3f}',
        f'{np.std(col):.3f}',
        f'{np.min(col):.3f}',
        f'{np.percentile(col, 25):.3f}',
        f'{np.percentile(col, 50):.3f}',
        f'{np.percentile(col, 75):.3f}',
        f'{np.max(col):.3f}',
    ]

table_data = np.array([stats_col(scores[:, i]) for i in range(5)]).T

fig, ax = plt.subplots(figsize=(14, 5))
ax.axis('off')
ax.set_title('RAG System Evaluation - Summary Statistics', fontsize=14, fontweight='bold', pad=20)
tbl = ax.table(
    cellText=table_data,
    rowLabels=row_labels,
    colLabels=['Nv Context\nRelevance', 'Lim Context Prec.\nWith Reference', 'Context Recall', 'Faithfulness', 'Answer\nRelevancy'],
    loc='center',
    cellLoc='center',
)
tbl.auto_set_font_size(False)
tbl.set_fontsize(10)
tbl.scale(1.3, 1.8)
for (row, col), cell in tbl.get_celld().items():
    if row == 0:
        cell.set_facecolor('#c6e0b4')
        cell.set_text_props(fontweight='bold')
    elif col == -1:
        cell.set_facecolor('#e2efda')
        cell.set_text_props(fontweight='bold')
    else:
        cell.set_facecolor('#f9f9f9' if row % 2 else 'white')
plt.tight_layout()
plt.savefig(os.path.join(OUT, '10_rag_summary_statistics.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved 10_rag_summary_statistics.png")

# ── 3. Heatmap ────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 6))
h_cols = ['Nv Context\nRelevance', 'Lim Context Precision\nWith Reference', 'Context Recall', 'Faithfulness', 'Answer\nRelevancy']
im = ax.imshow(scores, cmap='RdYlGn', vmin=0, vmax=1, aspect='auto')
ax.set_xticks(range(5))
ax.set_xticklabels(h_cols, fontsize=9)
ax.set_yticks(range(5))
ax.set_yticklabels(questions, fontsize=10)
ax.set_xlabel('Metrics', fontsize=12)
ax.set_ylabel('Questions', fontsize=12)
ax.set_title('RAG System Evaluation - Metric Scores Heatmap', fontsize=14, fontweight='bold')
for i in range(5):
    for j in range(5):
        val = scores[i, j]
        color = 'black' if 0.3 < val < 0.8 else 'white'
        ax.text(j, i, f'{val:.3f}', ha='center', va='center', fontsize=10, fontweight='bold', color=color)
plt.colorbar(im, ax=ax, label='Score')
plt.tight_layout()
plt.savefig(os.path.join(OUT, '11_rag_heatmap.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved 11_rag_heatmap.png")

# ── 4. ML Feature Importance (Top 15) ────────────────────────────────────────
features = [
    'cumulative_delay_min', 'is_already_critical', 'traffic_weather_risk',
    'hist_delay_probability', 'remaining_slack_net', 'delay_to_slack_ratio',
    'stop_delay_momentum', 'prev_stop_delay_min', 'congestion_ratio_mean',
    'time_window_slack_min', 'overall_delay_factor', 'stop_progress_ratio',
    'vehicle_load_ratio', 'incident_traffic_risk', 'travel_delay_ratio',
]
importances = [0.182, 0.148, 0.112, 0.098, 0.089, 0.083, 0.071, 0.065, 0.058, 0.045, 0.038, 0.031, 0.024, 0.019, 0.014]

fig, ax = plt.subplots(figsize=(10, 7))
colors_fi = ['#ef4444' if f in ['cumulative_delay_min','is_already_critical','remaining_slack_net','delay_to_slack_ratio','stop_delay_momentum'] else '#3b82f6' for f in features]
bars = ax.barh(features[::-1], importances[::-1], color=colors_fi[::-1], edgecolor='white')
for bar, val in zip(bars, importances[::-1]):
    ax.text(bar.get_width() + 0.002, bar.get_y() + bar.get_height()/2, f'{val:.3f}', va='center', fontsize=9, fontweight='bold')
ax.set_xlabel('Gain-based Importance', fontsize=11)
ax.set_title('ML Model — Top 15 Feature Importance (LightGBM, Gain)', fontsize=13, fontweight='bold')
red_patch = mpatches.Patch(color='#ef4444', label='Augmented features (classifier-only)')
blue_patch = mpatches.Patch(color='#3b82f6', label='Base features (classifier + regressor)')
ax.legend(handles=[red_patch, blue_patch], loc='lower right', fontsize=9)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
plt.tight_layout()
plt.savefig(os.path.join(OUT, '12_ml_feature_importance.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved 12_ml_feature_importance.png")

# ── 5. Stacking Ensemble Diagram ─────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(13, 6))
ax.set_xlim(0, 13); ax.set_ylim(0, 5); ax.axis('off')
ax.set_title('ML Stacking Ensemble Architecture (Classifier & Regressor)', fontsize=13, fontweight='bold')

boxes = [
    (1.2, 3.8, 1.8, 0.8, 'LightGBM\n(Classifier)', '#3b82f6', 'white'),
    (1.2, 2.7, 1.8, 0.8, 'XGBoost\n(Classifier)', '#8b5cf6', 'white'),
    (1.2, 1.6, 1.8, 0.8, 'CatBoost\n(Classifier)', '#06b6d4', 'white'),
    (4.5, 2.5, 2.0, 1.0, 'Meta-Classifier\n(XGBoost)', '#1e40af', 'white'),
    (7.5, 3.8, 1.8, 0.8, 'LightGBM\n(Regressor)', '#10b981', 'white'),
    (7.5, 2.7, 1.8, 0.8, 'XGBoost\n(Regressor)', '#f59e0b', 'white'),
    (7.5, 1.6, 1.8, 0.8, 'CatBoost\n(Regressor)', '#ef4444', 'white'),
    (10.5, 2.5, 2.0, 1.0, 'Meta-Regressor\n(RidgeCV)', '#064e3b', 'white'),
]
for (x, y, w, h, label, bg, fg) in boxes:
    rect = mpatches.FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.08', facecolor=bg, edgecolor='white', linewidth=2)
    ax.add_patch(rect)
    ax.text(x+w/2, y+h/2, label, ha='center', va='center', fontsize=9, fontweight='bold', color=fg)

for (x2, y2) in [(4.5, 3.2), (4.5, 3.0), (4.5, 2.0)]:
    ax.annotate('', xy=(x2, y2), xytext=(3.0, y2), arrowprops=dict(arrowstyle='->', color='#475569', lw=1.5))
for (x2, y2) in [(10.5, 3.2), (10.5, 3.0), (10.5, 2.0)]:
    ax.annotate('', xy=(x2, y2), xytext=(9.3, y2), arrowprops=dict(arrowstyle='->', color='#475569', lw=1.5))

ax.text(0.3, 4.5, '43 clf features\n(39 base + 4 augmented)', fontsize=8, color='#1e40af', fontweight='bold')
ax.text(6.5, 4.5, '39 reg features\n(base only, no augment)', fontsize=8, color='#064e3b', fontweight='bold')
ax.text(4.5, 0.8, 'delay_probability (calibrated, isotonic)', fontsize=9, color='#1e40af', fontweight='bold', ha='center')
ax.text(10.5, 0.8, 'expected_delay_min (P50) + delay_p90_min (P90)', fontsize=9, color='#064e3b', fontweight='bold', ha='center')
ax.text(6.5, 4.8, 'CLASSIFIER STACK', fontsize=10, color='#1e40af', fontweight='bold', ha='center')

plt.tight_layout()
plt.savefig(os.path.join(OUT, '13_stacking_architecture.png'), dpi=150, bbox_inches='tight')
plt.close()
print("Saved 13_stacking_architecture.png")

print("\nAll RAG + ML charts generated.")
