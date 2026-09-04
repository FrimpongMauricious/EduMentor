import { useNavigate } from "react-router-dom";

export default function Landing() {
  const navigate = useNavigate();

  return (
    <div className="page-center">
      <div className="card card--landing">
        <p className="eyebrow">Aethelis Solutions</p>
        <h1 className="page-title">WASSCE AI Mentor</h1>
        <p className="page-subtitle">Performance Dashboard</p>
        <p className="landing-copy">
          Track question accuracy, subject strengths, and study activity across the
          WASSCE AI Mentor programme.
        </p>
        <div className="landing-actions">
          <button className="btn btn--primary" onClick={() => navigate("/student")}>
            I am a Student
          </button>
          <button className="btn btn--secondary" onClick={() => navigate("/teacher")}>
            I am a Teacher
          </button>
        </div>
      </div>
    </div>
  );
}
