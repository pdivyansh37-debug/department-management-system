"""
app.py
------
Streamlit frontend for 4-Tier Plant Hierarchy Management System.
"""

import io
from datetime import date
import pandas as pd
import streamlit as st

from database import (
    add_employee, authenticate_user, change_password,
    get_all_skills, get_department_employees, get_ou_name, get_plants,
    get_operating_units, get_areas, get_workstations,
    get_summary_stats, init_db, search_employees, update_employee_status
)
from theme import apply_theme, eyebrow, render_kpi_cards

st.set_page_config(page_title="Plant Hierarchy Management", page_icon="🏭", layout="wide")
apply_theme()
init_db()

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.ou_id = None
    st.session_state.ou_name = None

def logout():
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.ou_id = None
    st.session_state.ou_name = None

def render_hierarchy_selector(prefix="", locked_ou_id=None):
    """Renders the 4-tier selection: Plant -> Operating Unit -> Area -> Workstation."""
    st.subheader("📍 Location / Hierarchy Selection")
    
    # Layer 1: Plant (4-5 options)
    plants = get_plants()
    plant_opts = {p["plant_name"]: p["plant_id"] for p in plants}
    sel_plant = st.selectbox("Plant", list(plant_opts.keys()), key=f"{prefix}_plant")

    # Layer 2: Operating Unit / Department (Restricted to logged-in head's department if locked)
    if locked_ou_id:
        # Department head can only use their assigned department
        ou_list = [ou for ou in get_operating_units() if ou["ou_id"] == locked_ou_id]
    else:
        ou_list = get_operating_units(plant_opts[sel_plant]) if sel_plant else []
        
    ou_opts = {ou["ou_name"]: ou["ou_id"] for ou in ou_list}
    sel_ou = st.selectbox("Operating Unit (Department)", list(ou_opts.keys()) if ou_opts else ["None"], key=f"{prefix}_ou")

    # Layer 3: Area (Uses keyword according to department)
    areas = get_areas(ou_opts[sel_ou]) if sel_ou != "None" else []
    area_opts = {a["area_name"]: a["area_id"] for a in areas}
    sel_area = st.selectbox("Area", list(area_opts.keys()) if area_opts else ["None"], key=f"{prefix}_area")

    # Layer 4: Workstation (Uses keyword according to department)
    ws = get_workstations(area_opts[sel_area]) if sel_area != "None" else []
    ws_opts = {w["workstation_name"]: w["workstation_id"] for w in ws}
    sel_ws = st.selectbox("Workstation / Cell", list(ws_opts.keys()) if ws_opts else ["None"], key=f"{prefix}_ws")

    return ws_opts.get(sel_ws) if sel_ws != "None" else None

def login_page():
    st.title("🏭 Plant Hierarchy Management System")
    eyebrow("Restricted Access · Department Head Login")
    
    # Show quick helper info for testing demo accounts
    st.info("Demo Head Accounts available: `ops_head` / `ops123`, `pkg_head` / `pkg123`, `asm_head` / `asm123`, `qc_head` / `qc123`, `log_head` / `log123`")

    with st.form("login_form"):
        username = st.text_input("Username", value="ops_head")
        password = st.text_input("Password", type="password", value="ops123")
        submitted = st.form_submit_button("Log In")

    if submitted:
        user = authenticate_user(username, password)
        if user:
            st.session_state.logged_in = True
            st.session_state.username = user["username"]
            st.session_state.ou_id = user["ou_id"]
            st.session_state.ou_name = get_ou_name(user["ou_id"])
            st.rerun()
        else:
            st.error("Invalid credentials.")

def add_employee_page():
    st.header("👥 Manage Department Employees")
    st.info(f"🔒 Security Notice: You are logged in as **{st.session_state.username}**. You can *only* manage records for your department: **{st.session_state.ou_name}**.")

    tab_add, tab_update, tab_view = st.tabs(["➕ Add New Employee", "🔄 Update Status", "📋 View Department Staff"])

    with tab_add:
        status = st.radio("Status", ["Working", "Not Working"], horizontal=True, key="add_status")
        
        # Enforce Rule 4: Head can add data ONLY for their own department by locking locked_ou_id
        selected_ws_id = render_hierarchy_selector(prefix="mgmt", locked_ou_id=st.session_state.ou_id)
        skill_options = {s["skill_name"]: s["skill_id"] for s in get_all_skills()}

        with st.form("add_employee_form", clear_on_submit=True):
            emp_no = st.text_input("Employee No.*", placeholder="e.g. E1001")
            emp_name = st.text_input("Employee Name*")
            phone_number = st.text_input("Phone Number*")
            working_area = st.text_input("Working Area Description")
            selected_skills = st.multiselect("Skills", list(skill_options.keys()))
            joining_date = st.date_input("Joining Date*", value=date.today())
            leaving_date = st.date_input("Leaving Date*", value=date.today()) if status == "Not Working" else None
            submitted = st.form_submit_button("Add Employee to Department")

        if submitted:
            if not all([emp_no, emp_name, phone_number]) or not selected_ws_id:
                st.error("Please fill all required fields and complete the workstation selection.")
            else:
                success, msg = add_employee(
                    emp_no=emp_no.strip(), emp_name=emp_name.strip(), phone_number=phone_number.strip(),
                    working_area=working_area.strip(), status=status, joining_date=str(joining_date),
                    leaving_date=str(leaving_date) if leaving_date else None, workstation_id=selected_ws_id,
                    skill_ids=[skill_options[name] for name in selected_skills],
                )
                (st.success if success else st.error)(msg)

    with tab_update:
        dept_emps = get_department_employees(st.session_state.ou_id)
        if not dept_emps:
            st.warning("No employees registered in your department yet.")
        else:
            opts = {f"{e['emp_name']} ({e['emp_no']}) - {e['status']}": e["emp_no"] for e in dept_emps}
            selected_emp = st.selectbox("Select Employee", list(opts.keys()))
            new_status = st.radio("New Status", ["Working", "Not Working"], horizontal=True)
            leaving_d = st.date_input("Leaving Date", value=date.today()) if new_status == "Not Working" else None
            if st.button("Update Status"):
                success, msg = update_employee_status(opts[selected_emp], new_status, str(leaving_d) if leaving_d else None, st.session_state.ou_id)
                (st.success if success else st.error)(msg)
                if success: st.rerun()

    with tab_view:
        st.subheader(f"Staff List for {st.session_state.ou_name}")
        dept_emps = get_department_employees(st.session_state.ou_id)
        if dept_emps:
            df_dept = pd.DataFrame(dept_emps).rename(columns={
                "emp_no": "Emp No", "emp_name": "Name", "phone_number": "Phone", "working_area": "Working Area", 
                "status": "Status", "skills": "Skills", "joining_date": "Joined", "leaving_date": "Left",
                "plant_name": "Plant", "ou_name": "Operating Unit", "area_name": "Area", "workstation_name": "Workstation"
            })
            st.dataframe(df_dept, width='stretch', hide_index=True)
        else:
            st.info("No records found for your department.")

def dashboard_page():
    st.header("📊 Global Employee Dashboard")
    # Matches the KPI view shown in your template image with total departments count
    render_kpi_cards(get_summary_stats())

def find_employee_page():
    st.header("🔍 Search Global Records")
    st.caption("Search across all company records or filter by your department clearance.")
    
    col1, col2 = st.columns([2, 1])
    with col1: search_term = st.text_input("Search", placeholder="Type name, emp no, area, workstation...")
    with col2: sort_by = st.selectbox("Sort by", ["Name", "Employee No", "Department", "Working", "Not Working", "Joining Date"])

    status_filter = sort_by if sort_by in ("Working", "Not Working") else None
    effective_sort = "Name" if status_filter else sort_by
    
    # Global search view across company
    results = search_employees(search_term=search_term.strip() if search_term else None, sort_by=effective_sort, status=status_filter)

    if not results:
        st.warning("No matching employees found.")
        return

    df = pd.DataFrame(results).rename(columns={
        "emp_no": "Emp No", "emp_name": "Name", "phone_number": "Phone", "working_area": "Working Area", 
        "status": "Status", "skills": "Skills", "joining_date": "Joined", "leaving_date": "Left",
        "plant_name": "Plant", "ou_name": "Operating Unit", "area_name": "Area", "workstation_name": "Workstation"
    })
    st.dataframe(df, width='stretch', hide_index=True)

def my_info_page():
    st.header("👤 My Account Info")
    st.write(f"**Username:** {st.session_state.username}")
    st.write(f"**Managed Department (Operating Unit):** {st.session_state.ou_name}")
    st.divider()
    with st.form("change_password_form", clear_on_submit=True):
        current_pw = st.text_input("Current Password", type="password")
        new_pw = st.text_input("New Password", type="password")
        confirm_pw = st.text_input("Confirm New Password", type="password")
        submitted = st.form_submit_button("Update Password")
    if submitted:
        if not all([current_pw, new_pw, confirm_pw]): st.error("Fill all fields.")
        elif new_pw != confirm_pw: st.error("Passwords don't match.")
        elif len(new_pw) < 6: st.error("Password must be at least 6 characters.")
        else:
            success, msg = change_password(st.session_state.username, current_pw, new_pw)
            (st.success if success else st.error)(msg)

def main():
    if not st.session_state.logged_in:
        login_page()
        return

    st.sidebar.title(f"👤 {st.session_state.username}")
    st.sidebar.caption(f"Department: {st.session_state.ou_name}")

    page = st.sidebar.radio("Navigate", ["Dashboard", "Manage Department Employees", "Search Records", "My Info"])
    st.sidebar.divider()
    if st.sidebar.button("Log Out"):
        logout()
        st.rerun()

    if page == "Dashboard": dashboard_page()
    elif page == "Manage Department Employees": add_employee_page()
    elif page == "Search Records": find_employee_page()
    elif page == "My Info": my_info_page()

if __name__ == "__main__":
    main()