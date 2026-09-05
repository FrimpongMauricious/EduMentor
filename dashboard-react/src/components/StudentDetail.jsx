import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import StatCard from "./StatCard";
import SubjectTable from "./SubjectTable";
import BarChart from "./BarChart";
import InsightCard from "./InsightCard";
import { fetchTeacherStudentDetail, ApiError } from "../api";
import { useAppContext } from "../context";
import { subjectLabel } from "../theme";

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

export default function StudentDetail() {
  const { phone } = useParams();
  const navigate = useNavigate();
  const { teacherPassword } = useAppContext();

  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!teacherPassword) {
      navigate("/teacher", { replace: true });
      return;
    }

    let cancelled = false;
    setLoading(true);
    fetchTeacherStudentDetail(teacherPassword, phone)
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 403) {
          navigate("/teacher", { replace: true });
          return;
        }
        setError(err.message || "Could not load this student.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [phone, teacherPassword, navigate]);

  return (
    <div className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">WASSCE AI Mentor</p>
          <h1 className="page-title page-title--sm">{data ? data.name : "Student"}</h1>
          {data && <p className="page-subtitle">{data.phone_masked}</p>}
        </div>
        <button className="btn btn--secondary" onClick={() => navigate("/teacher/dashboard")}>
          Back to Cohort
        </button>
      </header>

      {loading && <p className="empty-note">Loading student record...</p>}
      {error && <p className="form-error">{error}</p>}

      {data && (
        <>
          <div className="stat-row">
            <StatCard label="Total Questions Answered" value={data.total_questions} />
            <StatCard label="Overall Accuracy" value={`${data.overall_accuracy}%`} accent />
            <StatCard label="Last Active" value={formatDate(data.last_active)} />
          </div>

          <InsightCard label="Suggested Action" text={data.recommendation} />

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
        </>
      )}
    </div>
  );
}
