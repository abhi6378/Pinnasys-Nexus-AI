"""
ui/pages/automations_page.py — Minimal scheduled automation management.
"""
import streamlit as st

from automation import service as automation_service
from storage.db import SessionLocal
from ui.auth_state import get_state_membership_id, get_state_user_id
from workflows.engine import WORKFLOWS


WORKFLOW_LABELS = {
    "marketing_campaign": "Marketing Campaign",
    "content_creation": "Content Creation",
    "sales_outreach": "Sales Outreach",
    "support_setup": "Support Setup",
    "business_strategy": "Business Strategy",
    "research_draft_send": "Research & Outreach",
    "lead_capture": "Lead Capture Sync",
    "email_triage": "Email Triage",
    "competitor_research": "Competitor Insight",
}


def _schedule_payload(schedule_type: str, timezone: str, start_at: str, interval_minutes: int, cron_expression: str) -> dict:
    payload = {
        "schedule_type": schedule_type,
        "timezone": timezone or "UTC",
        "start_at": start_at.strip(),
        "interval_seconds": 0,
        "cron_expression": "",
    }
    if schedule_type == "interval":
        payload["interval_seconds"] = max(1, int(interval_minutes or 1)) * 60
    if schedule_type == "cron":
        payload["cron_expression"] = cron_expression.strip()
    return payload


def _render_run_status(run: dict) -> None:
    status = run.get("status", "")
    planned = str(run.get("planned_for", ""))[:19]
    detail = run.get("error_message") or run.get("resume_token") or ""
    st.caption(f"`{status}` planned {planned} {detail}")


def render_automations(auth_user=None):
    workspace_id = st.session_state.workspace_id
    st.markdown(f"## Automations — {st.session_state.workspace_name}")
    st.markdown("Durable scheduled runs backed by the database scheduler and worker.")
    st.markdown("---")

    db = SessionLocal()
    try:
        tab_create, tab_list = st.tabs(["Create", "Schedules"])

        with tab_create:
            st.markdown("### Schedule a workflow")
            workflow_keys = sorted(WORKFLOWS.keys())
            selected_workflow = st.selectbox(
                "Workflow",
                options=workflow_keys,
                format_func=lambda key: WORKFLOW_LABELS.get(key, key.replace("_", " ").title()),
            )
            user_input = st.text_area(
                "Instructions for the workflow",
                placeholder="Example: Check recent emails and draft replies for urgent items.",
                height=120,
            )
            col_a, col_b = st.columns(2)
            with col_a:
                schedule_type = st.selectbox("Schedule type", ["once", "interval", "cron"])
                timezone = st.text_input("Timezone", value="UTC")
            with col_b:
                start_at = st.text_input("Start at", placeholder="2026-04-23T09:00:00+05:30")
                interval_minutes = st.number_input("Interval minutes", min_value=1, value=60, step=5)
                cron_expression = st.text_input("Cron expression", placeholder="0 9 * * *")

            if st.button("Create automation", type="primary", use_container_width=True):
                if not user_input.strip():
                    st.warning("Add workflow instructions first.")
                    st.stop()
                try:
                    task = automation_service.create_schedule(
                        db,
                        workspace_id=workspace_id,
                        actor_user_id=get_state_user_id(st.session_state),
                        membership_id=get_state_membership_id(db, st.session_state, workspace_id),
                        schedule=_schedule_payload(schedule_type, timezone, start_at, interval_minutes, cron_expression),
                        payload={
                            "target_kind": "workflow",
                            "target_name": selected_workflow,
                            "force_workflow": selected_workflow,
                            "user_input": user_input,
                        },
                        connector_context=dict(st.session_state.get("connector_context", {}) or {}),
                        metadata_json={"created_from": "streamlit"},
                    )
                    st.success(f"Automation created. Next run: {automation_service.task_to_dict(task).get('next_run_at')}")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        with tab_list:
            schedules = automation_service.list_schedules(db, workspace_id, limit=100)
            if not schedules:
                st.info("No automations yet.")
            for task in schedules:
                title = WORKFLOW_LABELS.get(task.get("target_name", ""), task.get("target_name", "automation"))
                with st.container(border=True):
                    st.markdown(f"### {title}")
                    st.caption(
                        f"`{task.get('status')}` · `{task.get('schedule_type')}` · "
                        f"next `{task.get('next_run_at') or 'none'}`"
                    )
                    st.write(task.get("payload", {}).get("user_input", ""))
                    cols = st.columns(4)
                    if cols[0].button("Run now", key=f"auto_run_{task['id']}", use_container_width=True):
                        automation_service.run_now(db, task["id"])
                        st.success("Queued a run.")
                        st.rerun()
                    if task.get("status") == "active":
                        if cols[1].button("Pause", key=f"auto_pause_{task['id']}", use_container_width=True):
                            automation_service.pause_schedule(db, task["id"])
                            st.rerun()
                    else:
                        if cols[1].button("Resume", key=f"auto_resume_{task['id']}", use_container_width=True):
                            automation_service.resume_schedule(db, task["id"])
                            st.rerun()
                    if cols[2].button("Cancel", key=f"auto_cancel_{task['id']}", use_container_width=True):
                        automation_service.cancel_schedule(db, task["id"])
                        st.rerun()
                    runs = automation_service.list_runs(db, workspace_id=workspace_id, scheduled_task_id=task["id"], limit=5)
                    with st.expander("Recent runs"):
                        if not runs:
                            st.caption("No runs yet.")
                        for run in runs:
                            _render_run_status(run)
    finally:
        db.close()
