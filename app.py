"""
app.py
------
Streamlit frontend for 5-Tier Corporate Department Management System.
"""

import io
from datetime import date
import pandas as pd
import qrcode
import streamlit as st

from database import (
    add_employee, approve_pending_employee, authenticate_user, change_password,
    get_all_skills, get_department_employees, get_dept_name, get_facilities,
    get_main_departments, get_sub_departments, get_sub_sub_departments, get_workstations,
    get_pending_employees, get_summary_stats, handle_webhook_employee, init_db,
    reject_pending_employee, search_employees, update_employee_status
)
from theme import apply_theme, eyebrow, render_kpi_cards

st.set_page_config(page_title="Nested Dept Management", page_icon="🏭", layout="wide")
apply_theme()
init_db()  # Safely builds the 5-tier architecture on start

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.dept_id = None
    st.session_state.dept_name = None

def logout():
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.dept_id = None
    st.session_state.dept_name = None

# ---------------------------------------------------------------------------
# CASCADING HIERARCHY WIDGET (Shared component)
# ---------------------------------------------------------------------------
def render_hierarchy_selector(prefix=""):
    """Renders the 5-tier dropdowns and returns the selected workstation_id."""
    st.subheader("📍 Location / Hierarchy Selection")
    
    facs = get_facilities()
    fac_opts = {f["facility_name"]: f["facility_id"] for f in facs}
    sel_fac = st.selectbox("Facility", list(fac_opts.keys()), key=f"{prefix}_fac")

    mains = get_main_departments(fac_opts[sel_fac]) if sel_fac else []
    main_opts = {m["main_dept_name"]: m["main_dept_id"] for m in mains}
    sel_main = st.selectbox("Main Department", list(main_opts.keys()) if main_opts else ["None"], key=f"{prefix}_main")

    subs = get_sub_departments(main_opts[sel_main]) if sel_main != "None" else []
    sub_opts = {s["sub_dept_name"]: s["sub_dept_id"] for s in subs}
    sel_sub = st.selectbox("Sub-Department", list(sub_opts.keys()) if sub_opts else ["None"], key=f"{prefix}_sub")

    sub_subs = get_sub_sub_departments(sub_opts[sel_sub]) if sel_sub != "None" else []
    sub_sub_opts = {ss["sub_sub_dept_name"]: ss["sub_sub_dept_id"] for ss in sub_subs}
    sel_sub_sub = st.selectbox("Sub-Sub-Department", list(sub_sub_opts.keys()) if sub_sub_opts else ["None"], key=f"{prefix}_subsub")

    ws = get_workstations(sub_sub_opts[sel_sub_sub]) if sel_sub_sub != "None" else []
    ws_opts = {w["workstation_name"]: w["workstation_id"] for w in ws}
    sel_ws = st.selectbox("Workstation / Cell", list(ws_opts.keys()) if ws_opts else ["None"], key=f"{prefix}_ws")

    return ws_opts.get(sel_ws) if sel_ws != "None" else None

# ---------------------------------------------------------------------------
# PUBLIC SUBMISSION PAGE
# ---------------------------------------------------------------------------
def public_submission_page():
    st.title("🏭 Employee Data Submission")
    st.caption("Fill in your details below. Your submission routes up the 5-tier hierarchy for approval.")

    # Dropdowns placed outside form so they cascade dynamically
    selected_ws_id = render_hierarchy_selector(prefix="pub")
    skill_names = [s["skill_name"] for s in get_all_skills()]

    with st.form("public_submit_form", clear_on_submit=True):
        st.subheader("👤 Employee Details")
        emp_name = st.text_input("Full Name*")
        emp_no = st.text_input("Employee No.*", placeholder="e.g. E1001")
        phone_number = st.text_input("Phone Number*")
        skills = st.multiselect("Skills", skill_names)
        working_area = st.text_input("Working Area*")
        joining_date = st.date_input("Joining Date*", value=date.today())
        submitted = st.form_submit_button("Submit for Approval")

    if submitted:
        if not all([emp_name, emp_no, phone_number, working_area]):
            st.error("Please fill in all required fields.")
        elif not selected_ws_id:
            st.error("Please complete the Location / Hierarchy Selection down to the Workstation.")
        else:
            ws_name = [name for name, i in {w["workstation_name"]: w["workstation_id"] for w in get_workstations()}.items() if i == selected_ws_id][0]
            success, message = handle_webhook_employee({
                "emp_name": emp_name.strip(), "emp_no": emp_no.strip(), "phone_number": phone_number.strip(),
                "workstation_name": ws_name, "working_area": working_area.strip(), "joining_date": str(joining_date), "skills": skills,
            })
            (st.success if success else st.error)(message)

def generate_qr_code(data: str) -> io.BytesIO:
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1B4B66", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def share_link_page():
    st.header("🔗 Employee Submission Link")
    st.caption("Share this link or QR code with employees for self-submission.")
    base_url = st.text_input("Your app's URL", value="http://localhost:8501")
    submission_url = f"{base_url.rstrip('/')}/?page=submit"
    st.code(submission_url, language="text")
    st.image(generate_qr_code(submission_url), caption="Scan to submit data", width=220)

def my_info_page():
    st.header("👤 My Info")
    st.write(f"**Username:** {st.session_state.username}")
    st.write(f"**Main Department:** {st.session_state.dept_name}")
    st.divider()
    with st.form("change_password_form", clear_on_submit=True):
        current_pw = st.text_input("Current Password", type="password")
        new_pw = st.text_input("New Password", type="password")
        confirm_pw = st.text_input("Confirm New Password", type="password")
        submitted = st.form_submit_button("Update Password")
    if submitted:
        if not all([current_pw, new_pw, confirm_pw]): st.error("Fill all fields.")
        elif new_pw != confirm_pw: st.error("New passwords don't match.")
        elif len(new_pw) < 6: st.error("Password must be at least 6 characters.")
        else:
            success, msg = change_password(st.session_state.username, current_pw, new_pw)
            (st.success if success else st.error)(msg)

def login_page():
    st.title("🏭 Department Management System")
    eyebrow("Restricted Access · Department Heads Only")
    with st.form("login_form"):
        username = st.text_input("Username", value="ope_head")
        password = st.text_input("Password", type="password", value="ope123")
        submitted = st.form_submit_button("Log In")
    if submitted:
        user = authenticate_user(username, password)
        if user:
            st.session_state.logged_in = True
            st.session_state.username = user["username"]
            st.session_state.dept_id = user["main_dept_id"]
            st.session_state.dept_name = get_dept_name(user["main_dept_id"])
            st.rerun()
        else:
            st.error("Invalid credentials.")

def add_employee_page():
    st.header("👥 Manage Employees")
    st.info(f"Logged in for Main Department: **{st.session_state.dept_name}**")
    tab_add, tab_update = st.tabs(["➕ Add New Employee", "🔄 Update Employee Status"])

    with tab_add:
        status = st.radio("Status", ["Working", "Not Working"], horizontal=True, key="add_status")
        
        # Hierarchy outside form to allow live updates
        selected_ws_id = render_hierarchy_selector(prefix="mgmt")
        skill_options = {s["skill_name"]: s["skill_id"] for s in get_all_skills()}

        with st.form("add_employee_form", clear_on_submit=True):
            emp_no = st.text_input("Employee No.*", placeholder="e.g. E1001")
            emp_name = st.text_input("Employee Name*")
            phone_number = st.text_input("Phone Number*")
            working_area = st.text_input("Working Area Description")
            selected_skills = st.multiselect("Skills", list(skill_options.keys()))
            joining_date = st.date_input("Joining Date*", value=date.today())
            leaving_date = st.date_input("Leaving Date*", value=date.today()) if status == "Not Working" else None
            submitted = st.form_submit_button("Add Employee")

        if submitted:
            if not all([emp_no, emp_name, phone_number]) or not selected_ws_id:
                st.error("Please fill required fields and ensure a Workstation is selected.")
            else:
                success, msg = add_employee(
                    emp_no=emp_no.strip(), emp_name=emp_name.strip(), phone_number=phone_number.strip(),
                    working_area=working_area.strip(), status=status, joining_date=str(joining_date),
                    leaving_date=str(leaving_date) if leaving_date else None, workstation_id=selected_ws_id,
                    skill_ids=[skill_options[name] for name in selected_skills],
                )
                (st.success if success else st.error)(msg)

    with tab_update:
        dept_emps = get_department_employees(st.session_state.dept_id)
        if not dept_emps:
            st.warning("No employees in your department yet.")
        else:
            opts = {f"{e['emp_name']} ({e['emp_no']}) - {e['status']}": e["emp_no"] for e in dept_emps}
            selected_emp = st.selectbox("Select Employee", list(opts.keys()))
            new_status = st.radio("New Status", ["Working", "Not Working"], horizontal=True)
            leaving_d = st.date_input("Leaving Date", value=date.today()) if new_status == "Not Working" else None
            if st.button("Update Status"):
                success, msg = update_employee_status(opts[selected_emp], new_status, str(leaving_d) if leaving_d else None, st.session_state.dept_id)
                (st.success if success else st.error)(msg)
                if success: st.rerun()

def dashboard_page():
    st.header("📊 Global Employee Dashboard")
    render_kpi_cards(get_summary_stats())

def find_employee_page():
    st.header("🔍 Find Employee")
    col1, col2 = st.columns([2, 1])
    with col1: search_term = st.text_input("Search", placeholder="Type name, emp no, facility, dept...")
    with col2: sort_by = st.selectbox("Sort by", ["Name", "Employee No", "Main Dept", "Working", "Not Working", "Joining Date"])

    status_filter = sort_by if sort_by in ("Working", "Not Working") else None
    effective_sort = "Name" if status_filter else sort_by
    results = search_employees(search_term=search_term.strip() if search_term else None, sort_by=effective_sort, status=status_filter)

    if not results:
        st.warning("No matching employees found.")
        return

    df = pd.DataFrame(results).rename(columns={
        "emp_no": "Emp No", "emp_name": "Name", "phone_number": "Phone", "working_area": "Working Area", 
        "status": "Status", "skills": "Skills", "joining_date": "Joined", "leaving_date": "Left",
        "facility_name": "Facility", "main_dept_name": "Main Dept", "sub_dept_name": "Sub-Dept", 
        "sub_sub_dept_name": "Section", "workstation_name": "Workstation"
    })
    st.dataframe(df, width='stretch', hide_index=True)
    st.download_button("⬇️ Download CSV", data=df.to_csv(index=False).encode("utf-8"), file_name="employees.csv", mime="text/csv")

def pending_approvals_page():
    st.header("🕒 Pending Approvals")
    st.caption("Review data submitted through the external form.")
    pending = get_pending_employees(st.session_state.dept_id)
    if not pending:
        st.success("No pending submissions.")
        return

    for row in pending:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.markdown(f"**{row['emp_name']}**  ·  {row['emp_no']}")
                st.caption(f"Path: {row['sub_dept_name']} -> {row['sub_sub_dept_name']} -> {row['workstation_name']}")
                st.caption(f"Submitted: {row['submitted_at']}")
            with c2:
                if st.button("✅ Approve", key=f"approve_{row['pending_id']}", width='stretch'):
                    success, msg = approve_pending_employee(row["pending_id"], st.session_state.dept_id)
                    (st.success if success else st.error)(msg)
                    if success: st.rerun()
            with c3:
                if st.button("❌ Reject", key=f"reject_{row['pending_id']}", width='stretch'):
                    success, msg = reject_pending_employee(row["pending_id"], st.session_state.dept_id)
                    (st.success if success else st.error)(msg)
                    if success: st.rerun()

def main():
    if st.query_params.get("page") == "submit":
        public_submission_page()
        return

    if not st.session_state.logged_in:
        login_page()
        return

    st.sidebar.title(f"👤 {st.session_state.username}")
    st.sidebar.caption(f"Dept Scope: {st.session_state.dept_name}")
    pending_count = len(get_pending_employees(st.session_state.dept_id))
    appr_lbl = f"Pending Approvals ({pending_count})" if pending_count else "Pending Approvals"

    page = st.sidebar.radio("Navigate", ["Dashboard", "Manage Employees", appr_lbl, "Find Employee", "Share Submission Link", "My Info"])
    st.sidebar.divider()
    if st.sidebar.button("Log Out"):
        logout()
        st.rerun()

    if page == "Dashboard": dashboard_page()
    elif page == "Manage Employees": add_employee_page()
    elif page == appr_lbl: pending_approvals_page()
    elif page == "Find Employee": find_employee_page()
    elif page == "Share Submission Link": share_link_page()
    elif page == "My Info": my_info_page()

if __name__ == "__main__":
    main()