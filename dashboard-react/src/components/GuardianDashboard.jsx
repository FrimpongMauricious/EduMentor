import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import StatCard from "./StatCard";
import SubjectTable from "./SubjectTable";
import BarChart from "./BarChart";
import InsightCard from "./InsightCard";
import { subjectLabel } from "../theme";

// Guardian-labeled view of the exact same dashboard data returned by
// GET /api/dashboard/student/{phone} — same fields, same shape as
// StudentDashboard, only the copy differs.
function formatDate(iso) {
  if (!iso) return "Not yet active";
  try {
    return new Date(iso).toLocaleString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function encouragement(accuracy, totalQuestions) {
  if (totalQuestions === 0) {
    return "Your ward hasn't answered any questions yet — encourage them to start practising.";
  }
  if (accuracy >= 70) {
    return "Strong performance overall. Keep encouraging consistent practice.";
  }
  if (accuracy >= 40) {
    return "Steady progress. Focused revision on weaker subjects will lift this further.";
  }
  return "There is clear room for improvement. Regular practice will help most here.";
}

export default function GuardianDashboard() {
  const location = useLocation();
  const navigate = useNavigate();
  const data = location.state?.data;

  useEffect(() => {
    if (!data) {
      navigate("/guardian", { replace: true });
    }
  }, [data, navigate]);

  if (!data) return null;

  return (
    <div className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">WASSCE AI Mentor</p>
          <h1 className="page-title page-title--sm">Performance Summary for {data.name}</h1>
          <p className="page-subtitle">{data.phone_masked}</p>
        </div>
        <button className="btn btn--secondary" onClick={() => navigate("/guardian")}>
          Enter a Different Number
        </button>
      </header>

      <p className="encouragement">{encouragement(data.overall_accuracy, data.total_questions)}</p>

      <div className="stat-row">
        <StatCard label="Total Questions Answered" value={data.total_questions} />
        <StatCard label="Overall Accuracy" value={`${data.overall_accuracy}%`} accent />
        <StatCard label="Last Active" value={formatDate(data.last_active)} />
      </div>

      <InsightCard label="Recommended Focus" text={data.recommendation} />

      <section className="section">
        <h2 className="section-title">Performance by Subject</h2>
        <BarChart rows={data.by_subject} />
        <SubjectTable rows={data.by_subject} />
      </section>

      <section className="section">
        <h2 className="section-title">Recent Activity</h2>
        {data.recent_activity && data.recent_activity.length > 0 ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Subject</th>
                  <th>Result</th>
                  <th>Date</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_activity.map((item, idx) => (
                  <tr key={idx}>
                    <td>{subjectLabel(item.subject)}</td>
                    <td>
                      <span
                        className={`accuracy-pill ${
                          item.score ? "accuracy-pill--good" : "accuracy-pill--weak"
                        }`}
                      >
                        {item.score ? "Correct" : "Incorrect"}
                      </span>
                    </td>
                    <td>{formatDate(item.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty-note">No recent activity recorded yet.</p>
        )}
      </section>
    </div>
  );
}
