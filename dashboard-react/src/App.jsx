import { Routes, Route } from "react-router-dom";
import { AppProvider } from "./context";
import Landing from "./components/Landing";
import StudentLogin from "./components/StudentLogin";
import StudentDashboard from "./components/StudentDashboard";
import GuardianLogin from "./components/GuardianLogin";
import GuardianDashboard from "./components/GuardianDashboard";
import TeacherLogin from "./components/TeacherLogin";
import TeacherDashboard from "./components/TeacherDashboard";
import StudentDetail from "./components/StudentDetail";
import Leaderboard from "./components/Leaderboard";

export default function App() {
  return (
    <AppProvider>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/student" element={<StudentLogin />} />
        <Route path="/student/dashboard" element={<StudentDashboard />} />
        <Route path="/guardian" element={<GuardianLogin />} />
        <Route path="/guardian/dashboard" element={<GuardianDashboard />} />
        <Route path="/teacher" element={<TeacherLogin />} />
        <Route path="/teacher/dashboard" element={<TeacherDashboard />} />
        <Route path="/teacher/student/:phone" element={<StudentDetail />} />
        <Route path="/student/leaderboard" element={<Leaderboard audience="student" />} />
        <Route path="/guardian/leaderboard" element={<Leaderboard audience="guardian" />} />
        <Route path="/teacher/leaderboard" element={<Leaderboard audience="teacher" />} />
      </Routes>
    </AppProvider>
  );
}
