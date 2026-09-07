import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchLeaderboard, fetchTeacherLeaderboard, ApiError } from "../api";
import { useAppContext } from "../context";
import { subjectLabel } from "../theme";

const SUBJECTS = ["maths", "english", "science", "social_studies"];

const AUDIENCE_CONFIG = {
  student: { backTo: "/student/dashboard", backLabel: "Back to Dashboard" },
  guardian: { backTo: "/guardian/dashboard", backLabel: "Back to Dashboard" },
  teacher: { backTo: "/teacher/dashboard", backLabel: "Back to Cohort" },
};

// Shared by all three entry points (Student, Guardian, Teacher) — same
// ranking endpoint/logic, only the audience differs (masked vs full names,
// and where "own row" identification comes from).
export default function Leaderboard({ audience }) {
  const navigate = useNavigate();
  const { studentPhone, teacherPassword } = useAppContext();
  const config = AUDIENCE_CONFIG[audience];

  const [subject, setSubject] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (audience === "teacher" && !teacherPassword) {
      navigate("/teacher", { replace: true });
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError("");

    const promise =
      audience === "teacher"
        ? fetchTeacherLeaderboard(teacherPassword, subject)
        : fetchLeaderboard(subject, studentPhone);

    promise
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (cancelled) return;
        if (audience === "teacher" && err instanceof ApiError && err.status === 403) {
          navigate("/teacher", { replace: true });
          return;
        }
        setError(err.message || "Could not load the leaderboard.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [audience, subject, studentPhone, teacherPassword, navigate]);

  return (
    <div className="page-shell">
      <header className="page-header">
        <div>
          <p className="eyebrow">WASSCE AI Mentor</p>
          <h1 className="page-title page-title--sm">Leaderboard</h1>
          <p className="page-subtitle">
            {subject
              ? `Ranked by ${subjectLabel(subject)} performance`
              : "Ranked by overall performance"}
          </p>
        </div>
        <button className="btn btn--secondary" onClick={() => navigate(config.backTo)}>
          {config.backLabel}
        </button>
      </header>

      <div className="subject-tabs">
        <button
          className={`subject-tab${subject === null ? " subject-tab--active" : ""}`}
          onClick={() => setSubject(null)}
        >
          Overall
        </button>
        {SUBJECTS.map((subj) => (
          <button
            key={subj}
            className={`subject-tab${subject === subj ? " subject-tab--active" : ""}`}
            onClick={() => setSubject(subj)}
          >
            {subjectLabel(subj)}
          </button>
        ))}
      </div>

      {loading && <p className="empty-note">Loading leaderboard...</p>}
      {error && <p className="form-error">{error}</p>}

      {data && !loading && (
        <>
          {audience !== "teacher" && data.viewer && data.viewer.has_data === false && (
            <p className="empty-note">
              You haven't answered any questions{subject ? ` in ${subjectLabel(subject)}` : ""} yet
              — answer a few to see your rank here.
            </p>
          )}

          {data.rankings.length === 0 ? (
            <p className="empty-note">No ranked students in this scope yet.</p>
          ) : (
            <div className="table-wrap">
              <table className="data-table leaderboard-table">
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>Name</th>
                    <th>Score</th>
                    <th>Accuracy</th>
                    <th>Questions</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rankings.map((row) => (
                    <tr
                      key={`${row.rank}-${row.name}`}
                      className={row.is_you ? "leaderboard-row--you" : ""}
                    >
                      <td>
                        <RankBadge rank={row.rank} />
                      </td>
                      <td>
                        {row.name}
                        {row.is_you && <span className="you-tag">You</span>}
                      </td>
                      <td>{row.score}</td>
                      <td>{row.accuracy}%</td>
                      <td>{row.attempts}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function RankBadge({ rank }) {
  const variant =
    rank === 1 ? " rank-badge--gold" : rank === 2 ? " rank-badge--silver" : rank === 3 ? " rank-badge--bronze" : "";
  return <span className={`rank-badge${variant}`}>{rank}</span>;
}
