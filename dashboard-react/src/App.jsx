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
      </Routes>
    </AppProvider>
  );
}
