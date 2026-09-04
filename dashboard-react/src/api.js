export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "https://wassce-ai-mentor-api.onrender.com";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, options);
  } catch (err) {
    throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
  }

  let body = null;
  try {
    body = await response.json();
  } catch (err) {
    body = null;
  }

  if (!response.ok) {
    const message = body?.error || body?.detail || "Something went wrong. Please try again.";
    throw new ApiError(response.status, message);
  }

  return body;
}

export function fetchStudentDashboard(phone) {
  return request(`/api/dashboard/student/${encodeURIComponent(phone)}`);
}

export function fetchTeacherOverview(password) {
  return request("/api/dashboard/teacher/overview", {
    headers: { "X-Dashboard-Password": password },
  });
}

export function fetchTeacherStudentDetail(password, phone) {
  return request(`/api/dashboard/teacher/student/${encodeURIComponent(phone)}`, {
    headers: { "X-Dashboard-Password": password },
  });
}
