// Shared card for AI-generated text (per-student recommendation or
// cohort-level insight). The backend always returns a non-empty string —
// either a real recommendation or an honest "not enough data yet" message
// — so this never needs to render an empty/broken-looking state itself.
export default function InsightCard({ label, text }) {
  if (!text) return null;

  return (
    <div className="insight-card">
      <p className="insight-card__label">{label}</p>
      <p className="insight-card__text">{text}</p>
    </div>
  );
}
