// Shared card for AI-generated content (per-student recommendation or
// cohort-level insight). The backend returns `items` as a list of bullet
// points, plus `hasData` telling us whether that's a real, grounded list
// or just the honest "not enough data yet" placeholder — the placeholder
// always renders as a single plain message, never as a one-item bullet list.
export default function InsightCard({ label, items, hasData }) {
  if (!items || items.length === 0) return null;

  return (
    <div className="insight-card">
      <p className="insight-card__label">{label}</p>
      {hasData ? (
        <ul className="insight-card__list">
          {items.map((item, idx) => (
            <li key={idx}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="insight-card__text">{items[0]}</p>
      )}
    </div>
  );
}
