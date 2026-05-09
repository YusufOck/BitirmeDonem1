import React, { useEffect, useState } from 'react';
import { useRouteStore } from '../store/useRouteStore';
import { fetchAgentEvalSummary } from '../services/routeService';
import { PageHeader, MetricCard, LoadingState, ErrorState } from '../components/shared';

function ModelInfoSection({ modelInfo }) {
  return (
    <section className="page-card">
      <span className="panel-kicker">ML Model</span>
      <h2>Current model metadata</h2>
      <p style={{ marginBottom: '0.8rem' }}>
        The prediction model runs on the backend. These metrics update when the model is retrained.
      </p>
      <div className="settings-grid">
        <MetricCard label="Model version" value={modelInfo?.model_version || 'unknown'} />
        <MetricCard label="Classifier features" value={modelInfo?.n_clf_features ?? '--'} />
        <MetricCard label="Regressor features" value={modelInfo?.n_reg_features ?? '--'} />
        <MetricCard label="P90 quantile" value={modelInfo?.has_p90 ? 'Active' : 'Inactive'} tone={modelInfo?.has_p90 ? 'success' : 'warning'} />
        <MetricCard label="Threshold" value={modelInfo?.threshold ?? '--'} />
        <MetricCard label="Severe threshold" value={`${modelInfo?.severe_threshold_min ?? '--'} min`} />
      </div>
    </section>
  );
}

function ModelMetricsSection({ modelInfo }) {
  const metrics = modelInfo?.evaluation?.metrics;
  const metricsUnavailable = modelInfo?.evaluation?.metrics_unavailable;
  const version = modelInfo?.model_version || '?';

  if (metricsUnavailable) {
    return (
      <section className="page-card">
        <span className="panel-kicker">Evaluation Metrics</span>
        <h2>Model performance ({version})</h2>
        <div className="inline-error" style={{ marginTop: '0.5rem' }}>
          Metrics unavailable: {modelInfo?.evaluation?.metrics_error || 'Unknown reason'}
        </div>
      </section>
    );
  }

  if (!metrics) {
    return (
      <section className="page-card">
        <span className="panel-kicker">Evaluation Metrics</span>
        <h2>Model performance</h2>
        <div className="inline-error" style={{ marginTop: '0.5rem' }}>
          Model metrics not available. Ensure v9 model metrics are generated on the backend.
        </div>
      </section>
    );
  }

  const fmt = (v) => (v != null ? Number(v).toFixed(4) : '--');
  const fmtPct = (v) => (v != null ? `${(Number(v) * 100).toFixed(2)}%` : '--');

  return (
    <section className="page-card">
      <span className="panel-kicker">Evaluation Metrics</span>
      <h2>Model performance ({version})</h2>
      <p style={{ fontSize: '0.85rem', color: '#6b7280', marginBottom: '0.8rem' }}>
        Source: {modelInfo?.evaluation?.source || 'unknown'}
      </p>
      <div className="settings-grid">
        <MetricCard label="MAE" value={`${fmt(metrics['MAE delay minutes'])} min`} />
        <MetricCard label="RMSE" value={`${fmt(metrics['RMSE delay minutes'])} min`} />
        <MetricCard label="R²" value={fmt(metrics['R2 delay'])} tone={metrics['R2 delay'] > 0.8 ? 'success' : 'warning'} />
        <MetricCard label="Within 5 min" value={fmtPct(metrics['Within 5 minutes'])} />
        <MetricCard label="Late precision" value={fmt(metrics['Late precision'])} />
        <MetricCard label="Late recall" value={fmt(metrics['Late recall'])} />
        <MetricCard label="Avg precision" value={fmt(metrics['Average precision'])} />
        <MetricCard label="High-delay MAE (≥60)" value={`${fmt(metrics['High-delay MAE >=60'])} min`} />
        <MetricCard label="Extreme-delay MAE (≥120)" value={`${fmt(metrics['Extreme-delay MAE >=120'])} min`} />
        <MetricCard label="P90 coverage" value={fmtPct(metrics['P90 coverage'])} tone={metrics['P90 coverage'] > 0.85 ? 'success' : 'warning'} />
        <MetricCard label="P90 pinball loss" value={fmt(metrics['P90 pinball loss'])} />
        <MetricCard label="Test rows" value={metrics['Test rows'] ?? '--'} />
        <MetricCard label="Test routes" value={metrics['Test routes'] ?? '--'} />
      </div>
    </section>
  );
}

function RAGEvalSection({ evalData, evalLoading, evalError }) {
  if (evalLoading) {
    return (
      <section className="page-card">
        <span className="panel-kicker">RAG Pipeline</span>
        <h2>Agent evaluation summary</h2>
        <p>Loading evaluation data...</p>
      </section>
    );
  }

  if (evalError) {
    return (
      <section className="page-card">
        <span className="panel-kicker">RAG Pipeline</span>
        <h2>Agent evaluation summary</h2>
        <div className="inline-error">{evalError}</div>
      </section>
    );
  }

  if (!evalData || evalData.status === 'unavailable') {
    return (
      <section className="page-card">
        <span className="panel-kicker">RAG Pipeline</span>
        <h2>Agent evaluation summary</h2>
        <div className="inline-error">
          Evaluation has not been run yet. Run: <code>python reports/evaluate_agent_rag.py</code>
        </div>
      </section>
    );
  }

  const metrics = evalData.metrics || {};
  const fmtPct = (v) => (v != null ? `${(Number(v) * 100).toFixed(1)}%` : '--');

  return (
    <section className="page-card">
      <span className="panel-kicker">RAG Pipeline</span>
      <h2>Agent evaluation summary</h2>
      <p style={{ fontSize: '0.85rem', color: '#6b7280', marginBottom: '0.8rem' }}>
        Based on {evalData.total_questions || 0} evaluation questions. Pipeline: Retriever → Grader → Ollama → Hallucination Grader → Answer Grader.
      </p>
      <div className="settings-grid">
        <MetricCard label="Retriever Score" value={fmtPct(metrics.Retriever_Score)} tone={metrics.Retriever_Score >= 0.8 ? 'success' : 'warning'} />
        <MetricCard label="Context Precision" value={fmtPct(metrics.Context_Precision)} tone={metrics.Context_Precision >= 0.7 ? 'success' : 'warning'} />
        <MetricCard label="Hallucination Pass" value={fmtPct(metrics.Hallucination_Pass)} tone={metrics.Hallucination_Pass >= 0.8 ? 'success' : 'danger'} />
        <MetricCard label="Answer Score" value={fmtPct(metrics.Answer_Score)} tone={metrics.Answer_Score >= 0.8 ? 'success' : 'warning'} />
        <MetricCard label="Fallback Used" value={fmtPct(metrics.Fallback_Used)} tone={metrics.Fallback_Used < 0.3 ? 'success' : 'warning'} />
        <MetricCard label="Hallucination Blocked" value={evalData.hallucination_blocked_count ?? '--'} />
        <MetricCard label="Fallback Count" value={evalData.fallback_used_count ?? '--'} />
        <MetricCard label="Total Questions" value={evalData.total_questions ?? '--'} />
      </div>
      {evalData.file_paths && (
        <div style={{ marginTop: '0.8rem', fontSize: '0.8rem', color: '#6b7280' }}>
          <strong>Generated reports:</strong>
          <ul style={{ margin: '0.3rem 0', paddingLeft: '1.2rem' }}>
            {Object.entries(evalData.file_paths).map(([key, path]) => (
              <li key={key}><code>{path}</code></li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function DataSourcesSection() {
  const dataSources = [
    ['PostgreSQL', 'Couriers, routes, stops, lifecycle state'],
    ['Mapbox Matrix/Directions', 'Road duration, distance, geometry'],
    ['CSV feature pipeline', 'Training/historical delay features (not visual geometry)'],
    ['ML model (v9)', 'Trained delay prediction model'],
    ['OR-Tools solver', 'Route optimization with time windows'],
    ['RAG Agent Pipeline', 'Retriever → Grader → Ollama → Hallucination/Answer Graders'],
  ];
  return (
    <section className="page-card page-card--wide">
      <span className="panel-kicker">Architecture</span>
      <h2>Data sources and services</h2>
      <div className="source-grid">
        {dataSources.map(([name, desc]) => (
          <div className="source-card" key={name}>
            <strong>{name}</strong>
            <small>{desc}</small>
          </div>
        ))}
      </div>
    </section>
  );
}

function KnownLimitationsSection() {
  return (
    <section className="page-card">
      <span className="panel-kicker">Notes</span>
      <h2>Known limitations</h2>
      <ul className="plain-list">
        <li>CSV training data is synthetic stress-test; not used for visual road geometry</li>
        <li>Live traffic is simulated via scenario condition controls</li>
        <li>AI explanation is grounded via RAG pipeline with hallucination/answer graders</li>
        <li>If Ollama times out, deterministic fallback explanation is shown</li>
        <li>P90 coverage drops at extreme delays (120+ min)</li>
        <li>Courier simulation is demo-only (WebSocket)</li>
      </ul>
    </section>
  );
}

export default function AdminPage() {
  const {
    loading, error, fetchData, forceFetchData, modelInfo,
  } = useRouteStore();

  const [evalData, setEvalData] = useState(null);
  const [evalLoading, setEvalLoading] = useState(true);
  const [evalError, setEvalError] = useState(null);

  useEffect(() => { fetchData(); }, [fetchData]);

  useEffect(() => {
    fetchAgentEvalSummary()
      .then((data) => { setEvalData(data); setEvalLoading(false); })
      .catch((err) => { setEvalError(err.message); setEvalLoading(false); });
  }, []);

  if (loading) return <LoadingState text="Loading system info" />;
  if (error) return <ErrorState message={error} onRetry={forceFetchData} />;

  return (
    <div className="dashboard-container">
      <div className="dashboard-shell">
        <PageHeader
          eyebrow="Administration"
          title="System & Reports"
          subtitle="Model metadata, evaluation metrics, RAG pipeline scores, and system configuration. Separate from dispatcher workflow."
        />

        <div className="page-grid page-grid--reports">
          <ModelInfoSection modelInfo={modelInfo} />
          <ModelMetricsSection modelInfo={modelInfo} />
          <RAGEvalSection evalData={evalData} evalLoading={evalLoading} evalError={evalError} />
          <DataSourcesSection />
          <KnownLimitationsSection />
        </div>
      </div>
    </div>
  );
}
