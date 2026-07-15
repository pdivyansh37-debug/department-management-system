"""
app.py
------
Streamlit frontend for the Corporate Department Management System.

Run with:
    streamlit run app.py

Mock login accounts (seeded automatically on first run):
    welding_head / welding123   -> Welding Dept
    y_head       / yhead123     -> Department Y
"""

import io
from datetime import date

import pandas as pd
import qrcode
import streamlit as st

from database import (
    add_employee,
    approve_pending_employee,
    authenticate_user,
    get_all_departments,
    get_department_employees,
    get_dept_name,
    get_pending_employees,
    get_summary_stats,
    handle_webhook_employee,
    init_db,
    reject_pending_employee,
    search_employees,
    update_employee_status,
)
from theme import apply_theme, eyebrow, render_kpi_cards

st.set_page_config(page_title="Department Management System", page_icon=None, layout="wide")
apply_theme()
init_db()  # safe to call every run -- uses CREATE TABLE IF NOT EXISTS / INSERT OR IGNORE

# ---------------------------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = None
    st.session_state.dept_id = None    # <- this is what "remembers" the head's department
    st.session_state.dept_name = None


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
    Submissions go through they land in the
    pending-approval queue, never straight into the live employees table.
    This function is only reached because main() checks st.query_params
    BEFORE the login gate.
    """
    st.title("Employee Data Submission")
    st.caption(
        "Fill in your details below. Your information will be reviewed and "
        "approve this before it becomes part of the official records — you "
        "don't need an account to submit."
    )

    dept_names = [d["dept_name"] for d in get_all_departments()]

    with st.form("public_submit_form", clear_on_submit=True):
        emp_name = st.text_input("Full Name*")
        emp_no = st.text_input("Employee No.*", placeholder="e.g. E1001")
        phone_number = st.text_input("Phone Number*")
        department = st.selectbox("Department*", dept_names)
        working_area = st.text_input("Working Area*")
        joining_date = st.date_input("Joining Date*", value=date.today())
        submitted = st.form_submit_button("Submit for Approval")

    if submitted:
        if not all([emp_name, emp_no, phone_number, working_area]):
            st.error("Please fill in all required fields.")
        else:
            # Reuses the exact same intake function the webhook/import
            # simulator uses -- one path resolves 'department' text to a
            # dept_id and stages the row, whether it came from JSON or here.
            success, message = handle_webhook_employee({
                "emp_name": emp_name.strip(),
                "emp_no": emp_no.strip(),
                "phone_number": phone_number.strip(),
                "department": department,
                "working_area": working_area.strip(),
                "joining_date": str(joining_date),
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
# LOGIN PAGE
# ---------------------------------------------------------------------------
def login_page():
    st.title("Department Management System")
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
# ADD / UPDATE EMPLOYEE PAGE  (write access -- restricted to the head's own dept)
# ---------------------------------------------------------------------------
def add_employee_page():
    st.header("👥 Manage Employees")
    st.info(
        f"Logged in as **{st.session_state.username}** — both tabs below only "
        f" touch your **{st.session_state.dept_name}** department's records. You cannot add or update employees in any other department."
    )

    tab_add, tab_update = st.tabs(["➕ Add New Employee", "🔄 Update Employee Status"])

    # -----------------------------------------------------------------
    # TAB 1: Add a brand-new employee (unchanged from before)
    # -----------------------------------------------------------------
    with tab_add:
        # STATUS lives OUTSIDE st.form: Streamlit forms only re-run the script
        # on submit, but we need the leaving_date field to appear/disappear
        # live as soon as the radio changes -- so it has to be a normal
        # (non-form) widget.
        status = st.radio("Status", ["Working", "Not Working"], horizontal=True, key="add_status")

        leaving_date = None
        with st.form("add_employee_form", clear_on_submit=True):
            emp_no = st.text_input("Employee No.*", placeholder="e.g. E1001")
            emp_name = st.text_input("Employee Name*")
            phone_number = st.text_input("Phone Number*")
            working_area = st.text_input("Working Area*")
            joining_date = st.date_input("Joining Date*", value=date.today())

            # CONDITIONAL LOGIC: leaving_date is locked/hidden while
            # status == 'Working', and becomes an active, required input
            # the moment status is switched to 'Not Working'.
            if status == "Not Working":
                leaving_date = st.date_input("Leaving Date*", value=date.today(), key="add_leaving_date")
            else:
                st.text_input("Leaving Date", value="🔒 locked while status = Working", disabled=True)

            submitted = st.form_submit_button("Add Employee")

        if submitted:
            if not all([emp_no, emp_name, phone_number, working_area]):
                st.error("Please fill in all required fields.")
            elif status == "Not Working" and leaving_date is None:
                st.error("Leaving date is required when status is 'Not Working'.")
            else:
                # dept_id comes ONLY from st.session_state -- the value set
                # at login from the users table. It is never taken from a
                # form widget, which is what makes it impossible for this
                # head to write an employee into another department. See
                # add_employee() in database.py for the enforcing comment.
                success, message = add_employee(
                    emp_no=emp_no.strip(),
                    emp_name=emp_name.strip(),
                    phone_number=phone_number.strip(),
                    working_area=working_area.strip(),
                    status=status,
                    joining_date=str(joining_date),
                    leaving_date=str(leaving_date) if status == "Not Working" else None,
                    dept_id=st.session_state.dept_id,  # <-- enforced here
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

        # Scoped to the head's OWN department only -- same principle as the
        # Add tab, just enforced via get_department_employees(dept_id)
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
    st.header("📊Employee Dashboard")
    st.caption(
        "A quick summary, across every department. "
        "Use Find Employee to look up or filter individual records."
    )

    # No dept_id restriction here -- read access is intentionally global.
    render_kpi_cards(get_summary_stats())


# ---------------------------------------------------------------------------
# FIND EMPLOYEE PAGE  (global search + sort -- any head, any department)
# ---------------------------------------------------------------------------
def find_employee_page():
    st.header("🔍 Find Employee")
    st.caption(
        "Search across every department to quickly pull up one specific "
        "employee — by name, employee no., phone, working area, or department."
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        search_term = st.text_input(
            "Search",
            placeholder="Type a name, employee no., phone number, working area, or department...",
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
        c1.metric("Department", emp["dept_name"])
        c2.metric("Status", emp["status"])
        c3.metric("Working Area", emp["working_area"])
        st.write(f"**Employee No:** {emp['emp_no']}")
        st.write(f"**Phone:** {emp['phone_number']}")
        st.write(f"**Joined:** {emp['joining_date']}")
        if emp["leaving_date"]:
            st.write(f"**Left:** {emp['leaving_date']}")
        st.divider()

    df = pd.DataFrame(results).rename(columns={
        "emp_no": "Emp No", "emp_name": "Name", "phone_number": "Phone",
        "working_area": "Working Area", "status": "Status",
        "joining_date": "Joined", "leaving_date": "Left", "dept_name": "Department",
    })
    st.dataframe(df, width='stretch', hide_index=True)
    st.caption(f"{len(df)} result(s).")


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
                st.caption(
                    f" {row['phone_number']}  ·   {row['working_area']}  ·  "
                    f"Joining {row['joining_date']}"
                )
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

    st.sidebar.title(f" {st.session_state.username}")
    st.sidebar.caption(f"Department: {st.session_state.dept_name}")

    pending_count = len(get_pending_employees(st.session_state.dept_id))
    approvals_label = f"Pending Approvals ({pending_count})" if pending_count else "Pending Approvals"

    page = st.sidebar.radio(
        "Navigate",
        ["Dashboard", "Manage Employees", approvals_label, "Find Employee", "Share Submission Link"],
    )
    st.sidebar.divider()
    if st.sidebar.button("Log Out"):
        logout()
        st.rerun()

    if page == "Dashboard":
        dashboard_page()
    elif page == "Manage Employees":
        add_employee_page()
    elif page == approvals_label:
        pending_approvals_page()
    elif page == "Find Employee":
        find_employee_page()
    elif page == "Share Submission Link":
        share_link_page()


if __name__ == "__main__":
    main()