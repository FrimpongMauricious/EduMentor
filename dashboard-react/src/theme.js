export const COLORS = {
  navy: "#12284B",
  blue: "#1F5FA8",
  steel: "#3E7CC0",
  ice: "#E8F0FA",
  lightBg: "#F4F8FC",
  white: "#FFFFFF",
  gray: "#5A6B7B",
  darkText: "#1B2A3A",
  success: "#2E7D5B",
  warning: "#B7791F",
};

export const SUBJECT_LABELS = {
  maths: "Mathematics",
  english: "English",
  science: "Science",
  social_studies: "Social Studies",
};

export function subjectLabel(subject) {
  return SUBJECT_LABELS[subject] || subject;
}
