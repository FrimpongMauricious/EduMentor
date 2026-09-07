import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import StatCard from "./StatCard";
import SubjectTable from "./SubjectTable";
import BarChart from "./BarChart";
import InsightCard from "./InsightCard";
import { fetchTeacherOverview, ApiError } from "../api";
import { useAppContext } from "../context";

function formatDate(iso) {
  if (!iso) return "Never";
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

const SORT_FIELDS = {
  name: (s) => (s.name || "").toLowerCase(),
  total_questions: (s) => s.total_questions,
  accuracy: (s) => s.accuracy,
  last_active: (s) => s.last_active || "",
};

export default function TeacherDashboard() {
  const location = useLocation();
  const navigate = useNavigate();
  const { teacherPassword } = useAppContext();

  const [data, setData] = useState(location.state?.data || null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sortKey, setSortKey] = useState("last_active");
  const [sortDir, setSortDir] = useState("desc");

  useEffect(() => {
    if (!teacherPassword) {
      navigate("/teacher", { replace: true });
    }
  }, [teacherPassword, navigate]);

  async function refresh() {
    setLoading(true);
    setError("");
    try {
      const fresh = await fetchTeacherOverview(teacherPassword);
      setData(fresh);
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        navigate("/teacher", { replace: true });
        return;
      }
      setError(err.message || "Could not refresh data.");
    } finally {
      setLoading(false);
    }
  }

  if (!data) return null;

  function toggleSort(key) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const sortedStudents = [...data.students].sort((a, b) => {
    const getter = SORT_FIELDS[sortKey];
    const va = getter(a);
    const vb = getter(b);
    if (va < vb) return sortDir === "asc" ? -1 : 1;
    if (va > vb) return sortDir === "asc" ? 1 : -1;
    return 0;
  });

  return (
    <div className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">WASSCE AI Mentor</p>
          <h1 className="page-title page-title--sm">Cohort Overview</h1>
        </div>
        <button className="btn btn--secondary" onClick={refresh} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </header>

      {error && <p className="form-error">{error}</p>}

      <div className="stat-row">
        <StatCard label="Total Students" value={data.total_students} />
        <StatCard label="Questions Answered" value={data.total_questions_answered} />
        <StatCard label="Overall Accuracy" value={`${data.overall_accuracy}%`} accent />
      </div>

      <InsightCard label="Cohort Insight" items={data.insights} hasData={data.insights_has_data} />

      <section className="section">
        <h2 className="section-title">Accuracy by Subject</h2>
        <BarChart rows={data.by_subject} />
        <SubjectTable rows={data.by_subject} />
      </section>

      <section className="section">
        <h2 className="section-title">Students</h2>
        {sortedStudents.length === 0 ? (
          <p className="empty-note">No students registered yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <SortableHeader label="Name" sortKey="name" active={sortKey} dir={sortDir} onSort={toggleSort} />
                  <SortableHeader
                    label="Questions"
                    sortKey="total_questions"
                    active={sortKey}
                    dir={sortDir}
                    onSort={toggleSort}
                  />
                  <SortableHeader
                    label="Accuracy"
                    sortKey="accuracy"
                    active={sortKey}
                    dir={sortDir}
                    onSort={toggleSort}
                  />
                  <SortableHeader
                    label="Last Active"
                    sortKey="last_active"
                    active={sortKey}
                    dir={sortDir}
                    onSort={toggleSort}
                  />
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sortedStudents.map((s, idx) => (
                  <tr key={`${s.phone || "no-phone"}-${idx}`}>
                    <td>{s.name}</td>
                    <td>{s.total_questions}</td>
                    <td>
                      <span
                        className={`accuracy-pill ${
                          s.accuracy >= 70
                            ? "accuracy-pill--good"
                            : s.accuracy >= 40
                            ? "accuracy-pill--fair"
                            : "accuracy-pill--weak"
                        }`}
                      >
                        {s.accuracy}%
                      </span>
                    </td>
                    <td>{formatDate(s.last_active)}</td>
                    <td>
                      {s.phone ? (
                        <button
                          className="btn btn--link"
                          onClick={() =>
                            navigate(`/teacher/student/${encodeURIComponent(s.phone)}`)
                          }
                        >
                          View
                        </button>
                      ) : (
                        <span className="empty-note">No phone on file</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function SortableHeader({ label, sortKey, active, dir, onSort }) {
  const isActive = active === sortKey;
  return (
    <th className="sortable-header" onClick={() => onSort(sortKey)}>
      {label}
      {isActive && <span className="sort-arrow">{dir === "asc" ? " ↑" : " ↓"}</span>}
    </th>
  );
}
