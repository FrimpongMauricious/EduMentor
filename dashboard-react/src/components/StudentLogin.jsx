import { useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import { fetchStudentDashboard, ApiError } from "../api";
import { useAppContext } from "../context";

export default function StudentLogin() {
  const [searchParams] = useSearchParams();
  // Pre-fill from a shared link (e.g. WhatsApp "Get My Report") so the
  // student doesn't have to re-type a number they already gave the bot.
  const [phone, setPhone] = useState(searchParams.get("phone") || "");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { setStudentPhone } = useAppContext();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (!phone.trim()) {
      setError("Please enter your phone number.");
      return;
    }

    setLoading(true);
    try {
      const data = await fetchStudentDashboard(phone.trim());
      setStudentPhone(phone.trim());
      navigate("/student/dashboard", { state: { data } });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError(
          "No record found for that number. Make sure you have used the WASSCE AI Mentor on WhatsApp or USSD."
        );
      } else if (err instanceof ApiError && err.status === 429) {
        setError("Too many attempts. Please wait a moment and try again.");
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
        <h1 className="page-title page-title--sm">View Your Performance</h1>
        <p className="page-subtitle">Enter the phone number you use with WASSCE AI Mentor.</p>

        <form onSubmit={handleSubmit} className="form">
          <label className="field-label" htmlFor="phone">
            Enter your phone number
          </label>
          <input
            id="phone"
            type="tel"
            className="text-input"
            placeholder="0531850867"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            disabled={loading}
          />
          {error && <p className="form-error">{error}</p>}
          <button type="submit" className="btn btn--primary btn--block" disabled={loading}>
            {loading ? "Checking..." : "View My Dashboard"}
          </button>
        </form>
      </div>
    </div>
  );
}
