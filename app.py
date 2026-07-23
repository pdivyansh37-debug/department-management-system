"""
app.py
------
Streamlit frontend for the Corporate Department Management System.

Departments support a 5-level tree
    Facility -> Main Department -> Sub-Department -> Section/Line -> Workstation/Cell
but Facility is dormant for now -- Main Department is the current root.
Employees attach at the bottom (Workstation/Cell). Department heads still log
in at the Main Department level, same as Phase 1. New "Manage Departments"
page lets each head build out their own Sub-Department / Section / Workstation
structure by typing a name at each level -- names only need to be unique
among siblings under the same parent, so different branches of the tree can
freely reuse names without colliding.

Run with:
    streamlit run app.py

Mock login accounts (seeded automatically on first run):
    x_head           / welding123        -> Operations (OP)
    y_head           / yhead123          -> Packaging (PK)
    assembly_head    / assembly123       -> Assembly (AS)
    qc_head          / qualitycontrol123 -> Quality Control (QC)
    logistics_head   / logistics123      -> Logistics (LG)
    rnd_head         / randd123          -> R&D (RD)
    maintenance_head / maintenance123    -> Maintenance (MT)

Each of these starts with ZERO Sub-Departments -- log in and use
"Manage Departments" to type in your own Sub-Department / Section /
Workstation names.
"""

import io
from datetime import date

import pandas as pd
import qrcode
import streamlit as st

from database import (
    add_child_department,
    add_employee,
    approve_pending_employee,
    authenticate_user,
    change_password,
    get_all_skills,
    get_children,
    get_department,
    get_department_breakdown,
    get_department_employees,
    get_dept_name,
    get_main_departments,
    get_pending_employees,
    get_summary_stats,
    handle_webhook_employee,
    init_db,
    reject_pending_employee,
    search_employees,
    update_employee_status,
)
from theme import apply_theme, eyebrow, render_kpi_cards

st.set_page_config(page_title="Department Management System", page_icon="🏭", layout="wide")
apply_theme()
init_db()  # safe to call every run -- uses CREATE TABLE IF NOT EXISTS / INSERT OR IGNORE

# ---------------------------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.dept_id = None    # <- Main Department dept_id, set at login
    st.session_state.dept_name = None  # <- full breadcrumb path, e.g. "Plant 1 > Assembly (AS)"


def logout():
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.dept_id = None
    st.session_state.dept_name = None


# ---------------------------------------------------------------------------
# PUBLIC SUBMISSION PAGE  (no login -- reached via ?page=submit)
# ---------------------------------------------------------------------------
def public_submission_page():
    """
    A no-login page any employee can open via a shared link or QR code.
    Submissions go through the exact same handle_webhook_employee() logic
    used by the Flask webhook endpoint (api_server.py) -- they land in the
    pending-approval queue, never straight into the live employees table.
    This function is only reached because main() checks st.query_params
    BEFORE the login gate.

    The department picker is 4 SEPARATE cascading dropdowns -- Department
    (Main Department) -> Sub-Department -> Section/Line -> Workstation/Cell
    -- each one only offers children of whatever was picked above it. These
    live OUTSIDE st.form on purpose: forms in Streamlit only re-run the
    script on submit, but each dropdown here needs to immediately repopulate
    the next one the moment a selection changes.
    """
    st.title("🏭 Employee Data Submission")
    st.caption(
        "Fill in your details below. Your department head will review and "
        "approve this before it becomes part of the official records — you "
        "don't need an account to submit."
    )

    main_depts = get_main_departments()
    if not main_depts:
        st.warning("No departments have been set up yet — check back later.")
        return

    st.markdown("**Department**")
    main_options = {f"{d['label']} ({d['name']})": d["dept_id"] for d in main_depts}
    sel_main = st.selectbox("Department", list(main_options.keys()), key="pub_main_dept")
    main_dept_id = main_options[sel_main]

    sub_depts = get_children(main_dept_id)
    if not sub_depts:
        st.warning(f"'{sel_main}' has no Sub-Departments set up yet — check back later.")
        return
    sub_options = {d["name"]: d["dept_id"] for d in sub_depts}
    sel_sub = st.selectbox("Sub-Department", list(sub_options.keys()), key="pub_sub_dept")
    sub_dept_id = sub_options[sel_sub]

    sections = get_children(sub_dept_id)
    if not sections:
        st.warning(f"'{sel_sub}' has no Sections/Lines set up yet — check back later.")
        return
    sec_options = {d["name"]: d["dept_id"] for d in sections}
    sel_sec = st.selectbox("Section/Line", list(sec_options.keys()), key="pub_section")
    section_id = sec_options[sel_sec]

    workstations = get_children(section_id)
    if not workstations:
        st.warning(f"'{sel_sec}' has no Workstations/Cells set up yet — check back later.")
        return
    ws_options = {d["name"]: d["dept_id"] for d in workstations}
    sel_ws = st.selectbox("Workstation/Cell", list(ws_options.keys()), key="pub_workstation")
    leaf_dept_id = ws_options[sel_ws]

    skill_names = [s["skill_name"] for s in get_all_skills()]

    with st.form("public_submit_form", clear_on_submit=True):
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
        else:
            # handle_webhook_employee() resolves 'department' by exact leaf
            # CODE, not a display label -- so we look the code back up.
            leaf_dept = get_department(leaf_dept_id)
            success, message = handle_webhook_employee({
                "emp_name": emp_name.strip(),
                "emp_no": emp_no.strip(),
                "phone_number": phone_number.strip(),
                "department": leaf_dept["name"],
                "working_area": working_area.strip(),
                "joining_date": str(joining_date),
                "skills": skills,
            })
            if success:
                st.success("Submitted! Your department head has been notified for approval.")
            else:
                st.error(message)


def generate_qr_code(data: str) -> io.BytesIO:
    """Renders `data` (a URL) as a PNG QR code in memory for st.image()."""
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
    st.caption(
        "Share this link or QR code with employees so they can submit their "
        "own details for you to review — nothing they submit goes live "
        "until you approve it in Pending Approvals."
    )

    base_url = st.text_input(
        "Your app's URL",
        value="http://localhost:8501",
        help=(
            "For a quick test on your own WiFi, use the 'Network URL' shown in your "
            "terminal when Streamlit starts (e.g. http://192.168.x.x:8501) — phones on "
            "the same WiFi can reach that, but NOT 'localhost'. For anyone off your "
            "network (e.g. scanning from mobile data), deploy the app (Streamlit "
            "Community Cloud is free) and paste that public URL here instead."
        ),
    )
    submission_url = f"{base_url.rstrip('/')}/?page=submit"

    st.code(submission_url, language="text")

    qr_buf = generate_qr_code(submission_url)
    st.image(qr_buf, caption="Scan to submit employee data", width=220)


# ---------------------------------------------------------------------------
# MY INFO PAGE  (account details + change password)
# ---------------------------------------------------------------------------
def my_info_page():
    st.header("👤 My Info")
    st.caption("Your account details, and where you can change your password.")

    st.write(f"**Username:** {st.session_state.username}")
    st.write(f"**Department:** {st.session_state.dept_name}")

    st.divider()
    st.subheader("Change Password")

    with st.form("change_password_form", clear_on_submit=True):
        current_pw = st.text_input("Current Password", type="password")
        new_pw = st.text_input("New Password", type="password")
        confirm_pw = st.text_input("Confirm New Password", type="password")
        submitted = st.form_submit_button("Update Password")

    if submitted:
        if not all([current_pw, new_pw, confirm_pw]):
            st.error("Please fill in all fields.")
        elif new_pw != confirm_pw:
            st.error("New password and confirmation don't match.")
        elif len(new_pw) < 6:
            st.error("New password must be at least 6 characters.")
        else:
            # change_password() re-verifies current_pw against the database
            # itself -- this check isn't just a UI nicety, it's enforced
            # server-side too. See database.py for that.
            success, message = change_password(st.session_state.username, current_pw, new_pw)
            (st.success if success else st.error)(message)


# ---------------------------------------------------------------------------
# LOGIN PAGE
# ---------------------------------------------------------------------------
def login_page():
    st.title("🏭 Department Management System")
    eyebrow("Restricted Access · Department Heads Only")
    st.subheader("Department Head Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log In")

    if submitted:
        # authenticate_user() checks username + hashed password together in
        # one SQL WHERE clause -- see database.py for the query.
        user = authenticate_user(username, password)
        if user:
            st.session_state.logged_in = True
            st.session_state.username = user["username"]
            st.session_state.dept_id = user["dept_id"]
            st.session_state.dept_name = get_dept_name(user["dept_id"])
            st.rerun()
        else:
            st.error("Invalid username or password.")


# ---------------------------------------------------------------------------
# MANAGE DEPARTMENTS PAGE  (write access -- build out the head's own subtree)
# ---------------------------------------------------------------------------
def manage_departments_page():
    st.header("🗂️ Manage Departments")
    st.caption(
        f"Build out the structure under **{st.session_state.dept_name}**: "
        "Sub-Department → Section/Line → Workstation/Cell. Type a name at "
        "each level — names just need to be unique among siblings under "
        "the same parent, so two different Sub-Departments elsewhere in "
        "the tree can reuse a name without conflict."
    )

    main_dept_id = st.session_state.dept_id

    # ---- Level: Sub-Departments (direct children of this head's Main Dept)
    st.subheader("1️⃣ Sub-Departments")
    sub_depts = get_children(main_dept_id)
    if not sub_depts:
        st.caption("No Sub-Departments yet.")
    with st.form("add_subdept_form", clear_on_submit=True):
        new_subdept_name = st.text_input("New Sub-Department name", key="new_subdept_name")
        add_subdept_submitted = st.form_submit_button("➕ Add Sub-Department")
    if add_subdept_submitted:
        success, result = add_child_department(main_dept_id, new_subdept_name)
        if success:
            st.success(f"Added Sub-Department '{get_department(result)['name']}'.")
            st.rerun()
        else:
            st.error(result)

    st.divider()

    # ---- Level: Sections/Lines (children of a chosen Sub-Department)
    st.subheader("2️⃣ Sections / Lines")
    if not sub_depts:
        st.info("Add a Sub-Department first.")
    else:
        sub_options = {d["name"]: d["dept_id"] for d in sub_depts}
        chosen_sub_name = st.selectbox("Under which Sub-Department?", list(sub_options.keys()), key="sec_parent_pick")
        chosen_sub_id = sub_options[chosen_sub_name]
        sections = get_children(chosen_sub_id)
        if not sections:
            st.caption(f"No Sections/Lines under '{chosen_sub_name}' yet.")
        with st.form("add_section_form", clear_on_submit=True):
            new_section_name = st.text_input(f"New Section/Line name (under '{chosen_sub_name}')", key="new_section_name")
            add_section_submitted = st.form_submit_button("➕ Add Section/Line")
        if add_section_submitted:
            success, result = add_child_department(chosen_sub_id, new_section_name)
            if success:
                st.success(f"Added Section/Line '{get_department(result)['name']}'.")
                st.rerun()
            else:
                st.error(result)

    st.divider()

    # ---- Level: Workstations/Cells (children of a chosen Section/Line)
    st.subheader("3️⃣ Workstations / Cells")
    if not sub_depts:
        st.info("Add a Sub-Department and a Section/Line first.")
    else:
        all_sections = []
        for d in sub_depts:
            all_sections.extend(get_children(d["dept_id"]))
        if not all_sections:
            st.info("Add a Section/Line first.")
        else:
            sec_options = {s["name"]: s["dept_id"] for s in all_sections}
            chosen_sec_name = st.selectbox("Under which Section/Line?", list(sec_options.keys()), key="ws_parent_pick")
            chosen_sec_id = sec_options[chosen_sec_name]
            workstations = get_children(chosen_sec_id)
            if not workstations:
                st.caption(f"No Workstations/Cells under '{chosen_sec_name}' yet.")
            with st.form("add_workstation_form", clear_on_submit=True):
                new_ws_name = st.text_input(f"New Workstation/Cell name (under '{chosen_sec_name}')", key="new_ws_name")
                add_ws_submitted = st.form_submit_button("➕ Add Workstation/Cell")
            if add_ws_submitted:
                success, result = add_child_department(chosen_sec_id, new_ws_name)
                if success:
                    st.success(f"Added Workstation/Cell '{get_department(result)['name']}'.")
                    st.rerun()
                else:
                    st.error(result)


# ---------------------------------------------------------------------------
# ADD / UPDATE EMPLOYEE PAGE  (write access -- restricted to the head's own dept)
# ---------------------------------------------------------------------------
def add_employee_page():
    st.header("👥 Manage Employees")
    st.info(
        f"Logged in as **{st.session_state.username}** — both tabs below only "
        f"ever touch **{st.session_state.dept_name}**. There is no field "
        f"anywhere on this page to target a different department."
    )

    tab_add, tab_update = st.tabs(["➕ Add New Employee", "🔄 Update Employee Status"])

    # -----------------------------------------------------------------
    # TAB 1: Add a brand-new employee -- now requires picking all the way
    # down to a Workstation/Cell, since that's the only level an employee
    # can attach to.
    # -----------------------------------------------------------------
    with tab_add:
        main_dept_id = st.session_state.dept_id

        st.markdown("**Assign to Workstation/Cell**")
        sub_depts = get_children(main_dept_id)
        leaf_dept_id = None

        if not sub_depts:
            st.warning(
                "Your department has no Sub-Departments yet. Go to "
                "**Manage Departments** in the sidebar to build out your "
                "structure before adding employees."
            )
        else:
            sub_options = {d["name"]: d["dept_id"] for d in sub_depts}
            sel_sub = st.selectbox("Sub-Department", list(sub_options.keys()), key="add_emp_sub")
            sections = get_children(sub_options[sel_sub])

            if not sections:
                st.warning(
                    f"'{sel_sub}' has no Sections/Lines yet. Add one in "
                    "Manage Departments."
                )
            else:
                sec_options = {d["name"]: d["dept_id"] for d in sections}
                sel_sec = st.selectbox("Section/Line", list(sec_options.keys()), key="add_emp_sec")
                workstations = get_children(sec_options[sel_sec])

                if not workstations:
                    st.warning(
                        f"'{sel_sec}' has no Workstations/Cells yet. Add one "
                        "in Manage Departments."
                    )
                else:
                    ws_options = {d["name"]: d["dept_id"] for d in workstations}
                    sel_ws = st.selectbox("Workstation/Cell", list(ws_options.keys()), key="add_emp_ws")
                    leaf_dept_id = ws_options[sel_ws]

        # STATUS lives OUTSIDE st.form: Streamlit forms only re-run the script
        # on submit, but we need the leaving_date field to appear/disappear
        # live as soon as the radio changes -- so it has to be a normal
        # (non-form) widget.
        status = st.radio("Status", ["Working", "Not Working"], horizontal=True, key="add_status")

        leaving_date = None
        skill_options = {s["skill_name"]: s["skill_id"] for s in get_all_skills()}
        with st.form("add_employee_form", clear_on_submit=True):
            emp_no = st.text_input("Employee No.*", placeholder="e.g. E1001")
            emp_name = st.text_input("Employee Name*")
            phone_number = st.text_input("Phone Number*")
            working_area = st.text_input("Working Area*")
            selected_skills = st.multiselect("Skills", list(skill_options.keys()))
            joining_date = st.date_input("Joining Date*", value=date.today())

            # CONDITIONAL LOGIC: leaving_date is locked/hidden while
            # status == 'Working', and becomes an active, required input
            # the moment status is switched to 'Not Working'.
            if status == "Not Working":
                leaving_date = st.date_input("Leaving Date*", value=date.today(), key="add_leaving_date")
            else:
                st.text_input("Leaving Date", value="🔒 locked while status = Working", disabled=True)

            submitted = st.form_submit_button("Add Employee", disabled=leaf_dept_id is None)

        if submitted:
            if leaf_dept_id is None:
                st.error("Finish building out a Workstation/Cell above before adding an employee.")
            elif not all([emp_no, emp_name, phone_number, working_area]):
                st.error("Please fill in all required fields.")
            elif status == "Not Working" and leaving_date is None:
                st.error("Leaving date is required when status is 'Not Working'.")
            else:
                # leaf_dept_id was resolved via cascading selects that are
                # themselves rooted at st.session_state.dept_id (this head's
                # Main Department) -- so it's structurally impossible for
                # this to point outside the head's own subtree. add_employee()
                # also independently rejects anything that isn't a
                # Workstation/Cell, as a second line of defense.
                success, message = add_employee(
                    emp_no=emp_no.strip(),
                    emp_name=emp_name.strip(),
                    phone_number=phone_number.strip(),
                    working_area=working_area.strip(),
                    status=status,
                    joining_date=str(joining_date),
                    leaving_date=str(leaving_date) if status == "Not Working" else None,
                    dept_id=leaf_dept_id,
                    skill_ids=[skill_options[name] for name in selected_skills],
                )
                (st.success if success else st.error)(message)

    # -----------------------------------------------------------------
    # TAB 2: Move an EXISTING employee between Working / Not Working --
    # this is the piece that was missing: someone was added as Working,
    # and now they've actually left, so their record needs to move into
    # the Not Working pool (or come back if they're rehired later).
    # -----------------------------------------------------------------
    with tab_update:
        st.caption(
            "Pick one of your department's employees and change their status — "
            "e.g. mark them 'Not Working' the day they actually leave."
        )

        # Scoped to the head's OWN department subtree only -- same principle
        # as the Add tab, just enforced via get_department_employees(main_dept_id)
        # instead of a hidden form field.
        dept_employees = get_department_employees(st.session_state.dept_id)

        if not dept_employees:
            st.warning("No employees in your department yet — add one in the first tab.")
        else:
            options = {
                f"{e['emp_name']}  ·  {e['emp_no']}  ·  currently {e['status']}": e["emp_no"]
                for e in dept_employees
            }
            selected_label = st.selectbox("Select Employee", list(options.keys()), key="update_emp_select")
            selected_emp_no = options[selected_label]

            new_status = st.radio(
                "New Status", ["Working", "Not Working"], horizontal=True, key="update_status_radio"
            )

            new_leaving_date = None
            if new_status == "Not Working":
                new_leaving_date = st.date_input(
                    "Leaving Date*", value=date.today(), key="update_leaving_date"
                )
            else:
                st.caption("Leaving date will be cleared since status is being set back to Working.")

            if st.button("Update Status", key="update_status_btn"):
                if new_status == "Not Working" and new_leaving_date is None:
                    st.error("Leaving date is required when status is 'Not Working'.")
                else:
                    success, message = update_employee_status(
                        emp_no=selected_emp_no,
                        new_status=new_status,
                        leaving_date=str(new_leaving_date) if new_status == "Not Working" else None,
                        dept_id=st.session_state.dept_id,  # <-- enforced here too
                    )
                    (st.success if success else st.error)(message)
                    if success:
                        st.rerun()  # refresh the dropdown's "currently X" labels


# ---------------------------------------------------------------------------
# DASHBOARD PAGE  (read access -- global, any head can view any department)
# ---------------------------------------------------------------------------
def dashboard_page():
    st.header("📊 Global Employee Dashboard")
    st.caption(
        "A quick company-wide headcount summary, across every department. "
        "Use Find Employee to look up or filter individual records."
    )

    # No dept_id restriction here -- read access is intentionally global.
    render_kpi_cards(get_summary_stats())

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.subheader("Department Breakdown")

    breakdown = get_department_breakdown()
    if not breakdown:
        st.caption("No departments yet.")
        return

    # A small rotating accent palette -- purely visual, not semantic, so the
    # grid doesn't read as a flat wall of identical white boxes.
    accents = ["#0F6E56", "#993C1D", "#185FA5", "#712B13", "#534AB7", "#993556", "#3B6D11"]

    cards_per_row = 3
    for row_start in range(0, len(breakdown), cards_per_row):
        row = breakdown[row_start:row_start + cards_per_row]
        cols = st.columns(cards_per_row)
        for col, dept, accent in zip(cols, row, accents[row_start:row_start + cards_per_row]):
            total = dept["total"] or 0
            working = dept["working"] or 0
            not_working = dept["not_working"] or 0
            working_pct = round((working / total) * 100) if total else 0

            with col:
                st.markdown(
                    f"""
                    <div style="border:1px solid rgba(128,128,128,0.25); border-radius:10px;
                                overflow:hidden; margin-bottom:16px;">
                        <div style="height:4px; background:{accent};"></div>
                        <div style="padding:16px 18px;">
                            <div style="font-size:12px; font-weight:600; letter-spacing:0.04em;
                                        text-transform:uppercase; opacity:0.75;">
                                {dept['label']} <span style="opacity:0.6;">({dept['code']})</span>
                            </div>
                            <div style="font-size:30px; font-weight:700; margin-top:6px;">
                                {total}
                                <span style="font-size:13px; font-weight:400; opacity:0.6;">
                                    {"employee" if total == 1 else "employees"}
                                </span>
                            </div>
                            <div style="display:flex; gap:16px; margin-top:10px; font-size:13px; opacity:0.85;">
                                <span>✅ {working} Working</span>
                                <span>⛔ {not_working} Not Working</span>
                            </div>
                            <div style="margin-top:12px; height:6px; border-radius:3px;
                                        background:rgba(128,128,128,0.2); overflow:hidden;">
                                <div style="width:{working_pct}%; height:100%; background:{accent};"></div>
                            </div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# ---------------------------------------------------------------------------
# FIND EMPLOYEE PAGE  (global search + sort -- any head, any department)
# ---------------------------------------------------------------------------
def find_employee_page():
    st.header("🔍 Find Employee")
    st.caption(
        "Search across every department to quickly pull up one specific "
        "employee — by name, employee no., phone, working area, or any "
        "level of the department hierarchy (main department, "
        "sub-department, section, or workstation code)."
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        search_term = st.text_input(
            "Search",
            placeholder="Type a name, employee no., phone, or any department-hierarchy code...",
        )
    with col2:
        sort_by = st.selectbox(
            "Sort by", ["Name", "Employee No", "Department", "Working", "Not Working", "Joining Date"]
        )

    # "Working" / "Not Working" aren't sortable columns -- picking either one
    # filters the list to that status instead, and falls back to sorting by
    # name within it. Every other option sorts (ascending) across everyone.
    status_filter = sort_by if sort_by in ("Working", "Not Working") else None
    effective_sort_by = "Name" if status_filter else sort_by

    # No dept_id restriction here either -- same global-read principle as
    # the Dashboard, just with free-text search instead of dropdown filters.
    results = search_employees(
        search_term=search_term.strip() if search_term else None,
        sort_by=effective_sort_by,
        sort_order="Ascending",
        status=status_filter,
    )

    if not results:
        st.warning("No matching employees found.")
        return

    # If the search narrows it down to exactly one person, show a quick
    # detail card up top -- this is the "get a particular employee" case.
    if len(results) == 1:
        emp = results[0]
        st.divider()
        st.subheader(f"📇 {emp['emp_name']}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Status", emp["status"])
        c2.metric("Working Area", emp["working_area"])
        c3.metric("Employee No", emp["emp_no"])
        st.write(f"**Department:** {emp['dept_name']}")
        st.write(f"**Phone:** {emp['phone_number']}")
        st.write(f"**Skills:** {emp['skills'] or '—'}")
        st.write(f"**Joined:** {emp['joining_date']}")
        if emp["leaving_date"]:
            st.write(f"**Left:** {emp['leaving_date']}")
        st.divider()

    df = pd.DataFrame(results).rename(columns={
        "emp_no": "Emp No", "emp_name": "Name", "phone_number": "Phone",
        "working_area": "Working Area", "status": "Status", "skills": "Skills",
        "joining_date": "Joined", "leaving_date": "Left", "dept_name": "Department",
    })
    st.dataframe(df, width='stretch', hide_index=True)

    col_caption, col_download = st.columns([3, 1])
    with col_caption:
        st.caption(f"{len(df)} result(s).")
    with col_download:
        # Exports exactly what's currently shown -- respects the search
        # term, sort, and Working/Not Working filter above.
        st.download_button(
            "⬇️ Download as CSV",
            data=df.to_csv(index=False).encode("utf-8"),
            file_name="employees.csv",
            mime="text/csv",
        )


# ---------------------------------------------------------------------------
# PENDING APPROVALS PAGE  (review externally-submitted data before it's live)
# ---------------------------------------------------------------------------
def pending_approvals_page():
    st.header("🕒 Pending Approvals")
    st.caption(
        "Employee data submitted through the intake form/webhook lands here "
        "first. Nothing reaches the official employee list — or shows up on "
        "the Dashboard or Find Employee — until you approve it below. "
        "You only ever see submissions for your own department."
    )

    pending = get_pending_employees(st.session_state.dept_id)

    if not pending:
        st.success("No pending submissions for your department. All caught up.")
        return

    for row in pending:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.markdown(f"**{row['emp_name']}**  ·  {row['emp_no']}")
                st.caption(f"📍 {row['dept_name']}")
                st.caption(
                    f"📞 {row['phone_number']}  ·  🧭 {row['working_area']}  ·  "
                    f"Joining {row['joining_date']}"
                )
                if row.get("skills"):
                    st.caption(f"🛠️ Skills: {row['skills']}")
                st.caption(f"Submitted {row['submitted_at']}")
            with c2:
                if st.button("✅ Approve", key=f"approve_{row['pending_id']}", width='stretch'):
                    # dept_id is enforced inside approve_pending_employee()
                    # itself, not just assumed from this page being scoped --
                    # see the SQL comment there.
                    success, message = approve_pending_employee(row["pending_id"], st.session_state.dept_id)
                    (st.success if success else st.error)(message)
                    if success:
                        st.rerun()
            with c3:
                if st.button("❌ Reject", key=f"reject_{row['pending_id']}", width='stretch'):
                    success, message = reject_pending_employee(row["pending_id"], st.session_state.dept_id)
                    (st.success if success else st.error)(message)
                    if success:
                        st.rerun()


# ---------------------------------------------------------------------------
# MAIN ROUTER
# ---------------------------------------------------------------------------
def main():
    # Reached via a shared link/QR code, e.g. https://yourapp.../?page=submit
    # -- deliberately checked BEFORE the login gate below, since employees
    # submitting their own data don't have (and shouldn't need) an account.
    if st.query_params.get("page") == "submit":
        public_submission_page()
        return

    if not st.session_state.logged_in:
        login_page()
        return

    st.sidebar.title(f"👤 {st.session_state.username}")
    st.sidebar.caption(f"Department: {st.session_state.dept_name}")

    pending_count = len(get_pending_employees(st.session_state.dept_id))
    approvals_label = f"Pending Approvals ({pending_count})" if pending_count else "Pending Approvals"

    page = st.sidebar.radio(
        "Navigate",
        ["Dashboard", "Manage Departments", "Manage Employees", approvals_label,
         "Find Employee", "Share Submission Link", "My Info"],
    )
    st.sidebar.divider()
    if st.sidebar.button("Log Out"):
        logout()
        st.rerun()

    if page == "Dashboard":
        dashboard_page()
    elif page == "Manage Departments":
        manage_departments_page()
    elif page == "Manage Employees":
        add_employee_page()
    elif page == approvals_label:
        pending_approvals_page()
    elif page == "Find Employee":
        find_employee_page()
    elif page == "Share Submission Link":
        share_link_page()
    elif page == "My Info":
        my_info_page()


if __name__ == "__main__":
    main()