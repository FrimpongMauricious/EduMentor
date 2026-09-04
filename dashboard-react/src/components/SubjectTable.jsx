import { subjectLabel } from "../theme";

export default function SubjectTable({ rows }) {
  if (!rows || rows.length === 0) {
    return <p className="empty-note">No subject data available yet.</p>;
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Subject</th>
            <th>Attempted</th>
            <th>Correct</th>
            <th>Accuracy</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.subject}>
              <td>{subjectLabel(row.subject)}</td>
              <td>{row.attempted}</td>
              <td>{row.correct}</td>
              <td>
                <span className={`accuracy-pill ${accuracyClass(row.accuracy)}`}>
                  {row.accuracy}%
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function accuracyClass(accuracy) {
  if (accuracy >= 70) return "accuracy-pill--good";
  if (accuracy >= 40) return "accuracy-pill--fair";
  return "accuracy-pill--weak";
}
