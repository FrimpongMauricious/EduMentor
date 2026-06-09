"""
dashboard/app.py — Teacher analytics dashboard (Step 6).

6 tabs:
  1. 📈 Cohort Overview
  2. 🎯 Student Performance (accuracy heatmap)
  3. 💬 Channel Comparison (WhatsApp vs USSD)
  4. 📝 Weak Topics
  5. ✏️ Pre/Post Tests
  6. 📤 Export (CSV downloads)

Run with:  streamlit run dashboard/app.py
"""
import os

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine

try:
    from config import get_settings
    _s = get_settings()
    _DB_URL = _s.database_url
    _DASHBOARD_PASSWORD = _s.dashboard_password
except Exception:
    _DB_URL = os.getenv("DATABASE_URL", "sqlite:///./wassce_mentor.db")
    _DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "changeme")

from dashboard.queries import (
    cohort_overview,
    sessions_per_day,
    subject_topic_accuracy,
    channel_stats,
    weak_topics,
    prepost_results,
    student_list,
    interaction_export,
    performance_export,
    test_attempts_export,
)

TOTAL_TEST_QUESTIONS = 20

st.set_page_config(
    page_title="WASSCE AI Mentor — Dashboard",
    page_icon="📊",
    layout="wide",
)


# ── Engine (cached so the connection pool is shared across reruns) ─────────────

@st.cache_resource
def _get_engine():
    return create_engine(_DB_URL, connect_args={"check_same_thread": False})


# ── Password gate ──────────────────────────────────────────────────────────────

def _check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True

    st.title("WASSCE AI Mentor — Teacher Dashboard")
    st.markdown("---")
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.subheader("🔐 Login required")
        pwd = st.text_input("Password", type="password", key="login_pwd")
        if st.button("Login", use_container_width=True):
            if pwd == _DASHBOARD_PASSWORD:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")
    return False


# ── Dashboard ──────────────────────────────────────────────────────────────────

def _render_dashboard() -> None:
    engine = _get_engine()

    # Header row
    hdr, _, btn_col = st.columns([6, 2, 2])
    with hdr:
        st.title("📊 WASSCE AI Mentor — Teacher Dashboard")
    with btn_col:
        st.markdown(" ")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🔄 Refresh"):
                st.cache_data.clear()
                st.rerun()
        with c2:
            if st.button("🚪 Logout"):
                st.session_state["authenticated"] = False
                st.rerun()

    tabs = st.tabs([
        "📈 Cohort Overview",
        "🎯 Student Performance",
        "💬 Channel Comparison",
        "📝 Weak Topics",
        "✏️ Pre/Post Tests",
        "📤 Export",
    ])

    # ── Tab 1: Cohort Overview ─────────────────────────────────────────────────
    with tabs[0]:
        st.header("Cohort Overview")
        try:
            m = cohort_overview(engine)
        except Exception as exc:
            st.error(f"Could not load metrics: {exc}")
            m = {"students": 0, "sessions": 0, "questions_answered": 0,
                 "accuracy_pct": 0.0, "active_7d": 0}

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("👤 Students", m["students"])
        c2.metric("📅 Sessions", m["sessions"])
        c3.metric("❓ Questions Answered", m["questions_answered"])
        c4.metric("✅ Overall Accuracy", f"{m['accuracy_pct']}%")
        c5.metric("🟢 Active (7 days)", m["active_7d"])

        st.markdown("---")
        st.subheader("Session Activity Over Time")
        try:
            spd = sessions_per_day(engine)
            if spd.empty:
                st.info("No session data yet.")
            else:
                fig = px.line(
                    spd, x="day", y="sessions",
                    title="Sessions per Day",
                    labels={"day": "Date", "sessions": "Sessions"},
                    markers=True,
                )
                st.plotly_chart(fig, use_container_width=True)
        except Exception as exc:
            st.warning(f"Could not load activity chart: {exc}")

    # ── Tab 2: Student Performance Heatmap ────────────────────────────────────
    with tabs[1]:
        st.header("Student Performance by Subject & Topic")
        try:
            perf_df = subject_topic_accuracy(engine)
        except Exception as exc:
            st.error(f"Could not load performance data: {exc}")
            perf_df = pd.DataFrame()

        if perf_df.empty:
            st.info("No performance data yet. Students need to answer questions first.")
        else:
            pivot = perf_df.pivot_table(
                index="topic",
                columns="subject_display",
                values="accuracy_pct",
                aggfunc="mean",
            ).fillna(0)

            fig = px.imshow(
                pivot,
                title="Accuracy Heatmap (%) — Topic × Subject",
                color_continuous_scale="RdYlGn",
                zmin=0,
                zmax=100,
                text_auto=".0f",
                aspect="auto",
                labels={"color": "Accuracy %"},
            )
            fig.update_layout(xaxis_title="Subject", yaxis_title="Topic")
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Aggregated Data")
            cols = ["subject_display", "topic", "attempts", "correct", "accuracy_pct"]
            st.dataframe(
                perf_df[[c for c in cols if c in perf_df.columns]]
                    .rename(columns={"subject_display": "Subject", "accuracy_pct": "Accuracy (%)"}),
                use_container_width=True,
            )

    # ── Tab 3: Channel Comparison ──────────────────────────────────────────────
    with tabs[2]:
        st.header("Channel Comparison: WhatsApp vs USSD")
        try:
            ch_df = channel_stats(engine)
        except Exception as exc:
            st.error(f"Could not load channel data: {exc}")
            ch_df = pd.DataFrame()

        if ch_df.empty:
            st.info("No channel data yet.")
        else:
            left, right = st.columns(2)
            with left:
                fig_pie = px.pie(
                    ch_df, names="channel", values="students",
                    title="Students by Channel", hole=0.4,
                )
                st.plotly_chart(fig_pie, use_container_width=True)
            with right:
                fig_bar = px.bar(
                    ch_df, x="channel", y=["sessions", "interactions"],
                    title="Sessions & Interactions by Channel",
                    barmode="group",
                    labels={"value": "Count", "variable": "Metric"},
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            st.subheader("Raw Counts")
            st.dataframe(ch_df, use_container_width=True)

    # ── Tab 4: Weak Topics ─────────────────────────────────────────────────────
    with tabs[3]:
        st.header("Weakest Topics (Pilot-wide)")
        min_att = st.slider("Minimum attempts to include a topic", 1, 20, 1, key="wt_slider")
        try:
            wt_df = weak_topics(engine, min_attempts=min_att)
        except Exception as exc:
            st.error(f"Could not load weak topics: {exc}")
            wt_df = pd.DataFrame()

        if wt_df.empty:
            st.info("No topic data yet.")
        else:
            top15 = wt_df.head(15)
            fig_wt = px.bar(
                top15,
                x="accuracy_pct",
                y="topic",
                color="subject_display",
                orientation="h",
                title="15 Weakest Topics by Accuracy (lowest first)",
                labels={
                    "accuracy_pct": "Accuracy (%)",
                    "topic": "Topic",
                    "subject_display": "Subject",
                },
                range_x=[0, 100],
            )
            fig_wt.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_wt, use_container_width=True)

            st.subheader("All Topics")
            st.dataframe(
                wt_df[["subject_display", "topic", "attempts", "correct", "accuracy_pct"]]
                    .rename(columns={"subject_display": "Subject", "accuracy_pct": "Accuracy (%)"}),
                use_container_width=True,
            )

    # ── Tab 5: Pre/Post Tests ──────────────────────────────────────────────────
    with tabs[4]:
        st.header("Pre/Post Test Results")
        try:
            pp_df = prepost_results(engine)
        except Exception as exc:
            st.error(f"Could not load test results: {exc}")
            pp_df = pd.DataFrame()

        valid = pd.DataFrame()
        if not pp_df.empty:
            valid = pp_df.dropna(subset=["pre_score", "post_score"])

        if valid.empty:
            st.info("No completed pre/post test pairs yet.")
        else:
            avg_pre = valid["pre_score"].mean()
            avg_post = valid["post_score"].mean()
            avg_gain = valid["improvement"].mean()
            pct_improved = (valid["improvement"] > 0).mean() * 100

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Students (both tests)", len(valid))
            c2.metric("Avg Pre-Test", f"{avg_pre:.1f}/{TOTAL_TEST_QUESTIONS}")
            c3.metric("Avg Post-Test", f"{avg_post:.1f}/{TOTAL_TEST_QUESTIONS}")
            c4.metric("Avg Improvement", f"{avg_gain:+.1f} pts")

            st.markdown(f"**{pct_improved:.0f}% of students improved**")

            fig_scatter = px.scatter(
                valid,
                x="pre_score",
                y="post_score",
                title="Pre-Test vs Post-Test Scores (above diagonal = improved)",
                labels={"pre_score": "Pre-Test Score", "post_score": "Post-Test Score"},
                range_x=[0, TOTAL_TEST_QUESTIONS + 1],
                range_y=[0, TOTAL_TEST_QUESTIONS + 1],
            )
            fig_scatter.add_shape(
                type="line",
                x0=0, y0=0, x1=TOTAL_TEST_QUESTIONS, y1=TOTAL_TEST_QUESTIONS,
                line=dict(color="grey", dash="dash"),
            )
            st.plotly_chart(fig_scatter, use_container_width=True)

        pre_only = pp_df[pp_df["post_score"].isna()] if not pp_df.empty else pd.DataFrame()
        if not pre_only.empty:
            st.subheader(f"Students with pre-test only: {len(pre_only)}")
            st.dataframe(pre_only[["student_id", "pre_score"]], use_container_width=True)

        st.markdown("---")
        st.subheader("All Test Attempts")
        try:
            raw_ta = test_attempts_export(engine)
            if raw_ta.empty:
                st.info("No test attempts recorded yet.")
            else:
                st.dataframe(raw_ta, use_container_width=True)
        except Exception as exc:
            st.warning(f"Could not load test attempts: {exc}")

    # ── Tab 6: Export ──────────────────────────────────────────────────────────
    with tabs[5]:
        st.header("Export Data as CSV")

        exports = [
            ("👥 Students", student_list, "wassce_students.csv"),
            ("💬 Interactions", interaction_export, "wassce_interactions.csv"),
            ("📊 Performance Vectors", performance_export, "wassce_performance.csv"),
            ("✏️ Test Attempts", test_attempts_export, "wassce_test_attempts.csv"),
        ]

        for label, query_fn, filename in exports:
            with st.expander(label, expanded=True):
                try:
                    df_exp = query_fn(engine)
                    st.caption(f"{len(df_exp):,} rows")
                    st.download_button(
                        label=f"⬇️ Download {filename}",
                        data=df_exp.to_csv(index=False).encode("utf-8"),
                        file_name=filename,
                        mime="text/csv",
                        key=f"dl_{filename}",
                    )
                except Exception as exc:
                    st.error(f"Could not export {label}: {exc}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    if _check_password():
        _render_dashboard()


main()
