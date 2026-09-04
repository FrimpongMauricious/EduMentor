import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { fetchStudentDashboard, ApiError } from "../api";
import { useAppContext } from "../context";

// Guardian-labeled entry point onto the exact same student phone-lookup
// flow (GET /api/dashboard/student/{phone}) — no separate guardian
// backend logic or access model, just different copy for a pitch.
export default function GuardianLogin() {
  const [phone, setPhone] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { setStudentPhone } = useAppContext();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    if (!phone.trim()) {
      setError("Please enter your ward's phone number.");
      return;
    }

    setLoading(true);
    try {
      const data = await fetchStudentDashboard(phone.trim());
      setStudentPhone(phone.trim());
      navigate("/guardian/dashboard", { state: { data } });
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError(
          "No record found for that number. Make sure your ward has used the WASSCE AI Mentor on WhatsApp or USSD."
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
        <h1 className="page-title page-title--sm">View Your Ward's Performance</h1>
        <p className="page-subtitle">Enter the phone number your ward uses with WASSCE AI Mentor.</p>

        <form onSubmit={handleSubmit} className="form">
          <label className="field-label" htmlFor="phone">
            Enter your ward's phone number
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
            {loading ? "Checking..." : "View Dashboard"}
          </button>
        </form>
      </div>
    </div>
  );
}
