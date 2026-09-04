import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { fetchTeacherOverview, ApiError } from "../api";
import { useAppContext } from "../context";

export default function TeacherLogin() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { setTeacherPassword } = useAppContext();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (!password) {
      setError("Please enter the dashboard password.");
      return;
    }

    setLoading(true);
    try {
      const data = await fetchTeacherOverview(password);
      setTeacherPassword(password);
      navigate("/teacher/dashboard", { state: { data } });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError("Incorrect password.");
      } else {
        setError(err.message || "Something went wrong. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page-center">
      <div className="card">
        <Link to="/" className="back-link">
          &larr; Back
        </Link>
        <h1 className="page-title page-title--sm">Teacher Access</h1>
        <p className="page-subtitle">Enter the dashboard password to view the cohort.</p>

        <form onSubmit={handleSubmit} className="form">
          <label className="field-label" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            type="password"
            className="text-input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={loading}
          />
          {error && <p className="form-error">{error}</p>}
          <button type="submit" className="btn btn--primary btn--block" disabled={loading}>
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>
      </div>
    </div>
  );
}
