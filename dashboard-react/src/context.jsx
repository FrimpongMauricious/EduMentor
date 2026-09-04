import { createContext, useContext, useState } from "react";

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const [teacherPassword, setTeacherPassword] = useState("");
  const [studentPhone, setStudentPhone] = useState("");

  const value = { teacherPassword, setTeacherPassword, studentPhone, setStudentPhone };
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext() {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error("useAppContext must be used within AppProvider");
  }
  return ctx;
}
