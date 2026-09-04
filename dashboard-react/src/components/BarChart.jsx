import {
  BarChart as ReBarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { COLORS, subjectLabel } from "../theme";

export default function BarChart({ rows, title }) {
  if (!rows || rows.length === 0) {
    return <p className="empty-note">No data to chart yet.</p>;
  }

  const data = rows.map((row) => ({
    subject: subjectLabel(row.subject),
    accuracy: row.accuracy,
  }));

  return (
    <div className="chart-block">
      {title && <h3 className="chart-title">{title}</h3>}
      <ResponsiveContainer width="100%" height={280}>
        <ReBarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={COLORS.ice} />
          <XAxis dataKey="subject" tick={{ fill: COLORS.gray, fontSize: 13 }} />
          <YAxis
            domain={[0, 100]}
            tick={{ fill: COLORS.gray, fontSize: 13 }}
            tickFormatter={(v) => `${v}%`}
          />
          <Tooltip
            formatter={(value) => [`${value}%`, "Accuracy"]}
            contentStyle={{
              borderRadius: 8,
              border: `1px solid ${COLORS.ice}`,
              fontSize: 13,
            }}
          />
          <Bar dataKey="accuracy" fill={COLORS.blue} radius={[6, 6, 0, 0]} maxBarSize={64} />
        </ReBarChart>
      </ResponsiveContainer>
    </div>
  );
}
