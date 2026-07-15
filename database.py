"""
database.py (Postgres edition)
-------------------------------
Same data layer as before, now backed by Postgres (e.g. a free Neon
project) instead of a local SQLite file -- so data survives independent of
whether the app itself is running, sleeping, or being redeployed.

IMPORTANT: every function keeps the EXACT same name and signature as the
SQLite version. app.py and theme.py do not need to change at all -- only
this file's internals changed.

Connection string resolution order:
  1. st.secrets["DATABASE_URL"]  -- used when running under Streamlit,
     locally (via .streamlit/secrets.toml) or on Streamlit Community Cloud
     (via the app's Secrets settings).
  2. DATABASE_URL environment variable -- used by api_server.py, or any
     plain Python/non-Streamlit context.
"""

import hashlib
import os

import psycopg2
import psycopg2.extras


def _get_database_url() -> str:
    try:
        import streamlit as st
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Locally, put it in .streamlit/secrets.toml "
            "as DATABASE_URL = \"postgresql://...\". On Streamlit Community Cloud, "
            "add it under your app's Settings -> Secrets. For api_server.py, "
            "export it as a plain environment variable."
        )
    return url


def get_connection():
    """
    Opens a fresh Postgres connection per call, with a dict-like row cursor
    factory set as the connection default -- so `row['col_name']` and
    `dict(row)` behave exactly the way they did with sqlite3.Row.
    """
    return psycopg2.connect(_get_database_url(), cursor_factory=psycopg2.extras.RealDictCursor)


def hash_password(raw_password: str) -> str:
    """SHA-256 hash so plaintext passwords are never stored or compared directly."""
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()


def init_db():
    """Create tables if missing and seed mock departments/heads for testing."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            dept_id     SERIAL PRIMARY KEY,
            dept_name   TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     SERIAL PRIMARY KEY,
            username    TEXT NOT NULL UNIQUE,
            password    TEXT NOT NULL,          -- SHA-256 hash, not plaintext
            dept_id     INTEGER NOT NULL REFERENCES departments(dept_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            emp_no          TEXT PRIMARY KEY,
            emp_name        TEXT NOT NULL,
            phone_number    TEXT,
            working_area    TEXT,
            status          TEXT NOT NULL CHECK (status IN ('Working', 'Not Working')),
            joining_date    TEXT,
            leaving_date    TEXT,
            dept_id         INTEGER NOT NULL REFERENCES departments(dept_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_employees (
            pending_id      SERIAL PRIMARY KEY,
            emp_no          TEXT NOT NULL,
            emp_name        TEXT NOT NULL,
            phone_number    TEXT,
            working_area    TEXT,
            joining_date    TEXT,
            dept_id         INTEGER NOT NULL REFERENCES departments(dept_id),
            submitted_at    TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'Pending'
                            CHECK (status IN ('Pending', 'Approved', 'Rejected'))
        )
    """)
    conn.commit()

    # ---- Seed departments (idempotent via ON CONFLICT ... DO NOTHING) ----
    for name in ["Welding", "Department Y", "Assembly", "Quality Control", "Logistics"]:
        cur.execute(
            "INSERT INTO departments (dept_name) VALUES (%s) ON CONFLICT (dept_name) DO NOTHING",
            (name,),
        )
    conn.commit()

    cur.execute("SELECT dept_id, dept_name FROM departments")
    dept_id_by_name = {row["dept_name"]: row["dept_id"] for row in cur.fetchall()}

    # ---- Seed one Department Head login per department ----
    mock_users = [
        ("x_head", "welding123", dept_id_by_name["Welding"]),
        ("y_head", "yhead123", dept_id_by_name["Department Y"]),
        ("assembly_head", "assembly123", dept_id_by_name["Assembly"]),
        ("qc_head", "qualitycontrol123", dept_id_by_name["Quality Control"]),
        ("logistics_head", "logistics123", dept_id_by_name["Logistics"]),
    ]
    for username, plain_pw, dept_id in mock_users:
        cur.execute(
            "INSERT INTO users (username, password, dept_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (username) DO NOTHING",
            (username, hash_password(plain_pw), dept_id),
        )
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------
def authenticate_user(username: str, password: str):
    """
    Returns a dict {user_id, username, dept_id} if credentials match, else None.
    Username + hashed password are checked together in one SQL WHERE clause;
    a mismatch on either returns zero rows and login fails.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, username, dept_id FROM users WHERE username = %s AND password = %s",
        (username, hash_password(password)),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# DEPARTMENTS
# ---------------------------------------------------------------------------
def get_all_departments():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT dept_id, dept_name FROM departments ORDER BY dept_name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_dept_name(dept_id: int) -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT dept_name FROM departments WHERE dept_id = %s", (dept_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["dept_name"] if row else "Unknown"


def get_dept_id_by_name(dept_name: str):
    """Case-insensitive, trimmed match -- resolves free-text department names to a dept_id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT dept_id FROM departments WHERE LOWER(TRIM(dept_name)) = LOWER(TRIM(%s))",
        (dept_name,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["dept_id"] if row else None


# ---------------------------------------------------------------------------
# EMPLOYEES -- WRITE (restricted)
# ---------------------------------------------------------------------------
def add_employee(emp_no, emp_name, phone_number, working_area, status,
                  joining_date, leaving_date, dept_id):
    """
    dept_id must be supplied by the caller from a trusted source (the
    logged-in head's session, or a resolved department name) -- never from
    a user-editable field. See app.py for how this is enforced end-to-end.
    """
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO employees
                (emp_no, emp_name, phone_number, working_area, status,
                 joining_date, leaving_date, dept_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (emp_no, emp_name, phone_number, working_area, status,
             joining_date, leaving_date, dept_id),
        )
        conn.commit()
        return True, f"Employee '{emp_name}' added successfully."
    except psycopg2.IntegrityError as e:
        conn.rollback()
        return False, f"Could not add employee (emp_no '{emp_no}' may already exist): {e}"
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# EMPLOYEES -- READ (global / cross-department, filterable)
# ---------------------------------------------------------------------------
def get_employees(dept_id=None, status=None):
    query = """
        SELECT e.emp_no, e.emp_name, e.phone_number, e.working_area,
               e.status, e.joining_date, e.leaving_date,
               d.dept_name
        FROM employees e
        JOIN departments d ON e.dept_id = d.dept_id
        WHERE 1=1
    """
    params = []
    if dept_id:
        query += " AND e.dept_id = %s"
        params.append(dept_id)
    if status:
        query += " AND e.status = %s"
        params.append(status)
    query += " ORDER BY d.dept_name, e.emp_name"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# EMPLOYEES -- OWN-DEPARTMENT LIST (for the "update status" dropdown)
# ---------------------------------------------------------------------------
def get_department_employees(dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT emp_no, emp_name, status FROM employees WHERE dept_id = %s ORDER BY emp_name",
        (dept_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# EMPLOYEES -- UPDATE STATUS (restricted)
# ---------------------------------------------------------------------------
def update_employee_status(emp_no, new_status, leaving_date, dept_id):
    """
    dept_id is included directly in the WHERE clause -- if a head somehow
    supplied another department's real emp_no, the UPDATE matches zero rows
    and nothing changes.
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE employees
        SET status = %s, leaving_date = %s
        WHERE emp_no = %s AND dept_id = %s
        """,
        (new_status, leaving_date, emp_no, dept_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    cur.close()
    conn.close()

    if updated:
        return True, f"'{emp_no}' status updated to '{new_status}'."
    return False, "Employee not found in your department — update blocked."


# ---------------------------------------------------------------------------
# SUMMARY STATS (for dashboard KPI readout cards)
# ---------------------------------------------------------------------------
def get_summary_stats():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM employees")
    total = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM employees WHERE status = 'Working'")
    working = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM employees WHERE status = 'Not Working'")
    not_working = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM departments")
    departments = cur.fetchone()["c"]
    cur.close()
    conn.close()
    return {"total": total, "working": working, "not_working": not_working, "departments": departments}


# ---------------------------------------------------------------------------
# EMPLOYEES -- SEARCH & SORT (global, for the "Find Employee" tab)
# ---------------------------------------------------------------------------
def search_employees(search_term=None, sort_by="Name", sort_order="Ascending", status=None):
    """
    status, when given ('Working' or 'Not Working'), filters the results to
    that status at the SQL level -- same pattern as get_employees(). Used by
    the Find Employee page when the person picks "Working"/"Not Working"
    from the dropdown instead of a real sortable column.
    """
    allowed_sort_columns = {
        "Name": "e.emp_name",
        "Employee No": "e.emp_no",
        "Department": "d.dept_name",
        "Status": "e.status",
        "Joining Date": "e.joining_date",
    }
    sort_column = allowed_sort_columns.get(sort_by, "e.emp_name")
    order = "ASC" if sort_order == "Ascending" else "DESC"

    query = """
        SELECT e.emp_no, e.emp_name, e.phone_number, e.working_area,
               e.status, e.joining_date, e.leaving_date, d.dept_name
        FROM employees e
        JOIN departments d ON e.dept_id = d.dept_id
        WHERE 1=1
    """
    params = []
    if search_term:
        query += """ AND (
            e.emp_name ILIKE %s OR
            e.emp_no ILIKE %s OR
            e.phone_number ILIKE %s OR
            e.working_area ILIKE %s OR
            d.dept_name ILIKE %s
        )"""
        like_term = f"%{search_term}%"
        params.extend([like_term] * 5)
    if status:
        query += " AND e.status = %s"
        params.append(status)

    query += f" ORDER BY {sort_column} {order}"  # sort_column whitelist-mapped above, safe to inline

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# PENDING APPROVALS (externally-submitted data awaiting head sign-off)
# ---------------------------------------------------------------------------
def add_pending_employee(emp_no, emp_name, phone_number, working_area, joining_date, dept_id):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO pending_employees
                (emp_no, emp_name, phone_number, working_area, joining_date, dept_id, submitted_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, NOW()::text, 'Pending')
            """,
            (emp_no, emp_name, phone_number, working_area, joining_date, dept_id),
        )
        conn.commit()
        return True, "Submission received — awaiting department head approval."
    except psycopg2.IntegrityError as e:
        conn.rollback()
        return False, f"Could not submit: {e}"
    finally:
        cur.close()
        conn.close()


def get_pending_employees(dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pending_id, emp_no, emp_name, phone_number, working_area,
               joining_date, submitted_at
        FROM pending_employees
        WHERE dept_id = %s AND status = 'Pending'
        ORDER BY submitted_at
        """,
        (dept_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def approve_pending_employee(pending_id, dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pending_employees WHERE pending_id = %s AND dept_id = %s AND status = 'Pending'",
        (pending_id, dept_id),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None:
        return False, "Submission not found in your department (or already handled)."

    success, message = add_employee(
        emp_no=row["emp_no"],
        emp_name=row["emp_name"],
        phone_number=row["phone_number"],
        working_area=row["working_area"],
        status="Working",
        joining_date=row["joining_date"],
        leaving_date=None,
        dept_id=row["dept_id"],
    )
    if not success:
        return False, message

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE pending_employees SET status = 'Approved' WHERE pending_id = %s", (pending_id,))
    conn.commit()
    cur.close()
    conn.close()
    return True, f"Approved — '{row['emp_name']}' added to Employees."


def reject_pending_employee(pending_id, dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE pending_employees SET status = 'Rejected' "
        "WHERE pending_id = %s AND dept_id = %s AND status = 'Pending'",
        (pending_id, dept_id),
    )
    conn.commit()
    updated = cur.rowcount > 0
    cur.close()
    conn.close()
    if updated:
        return True, "Submission rejected."
    return False, "Submission not found in your department (or already handled)."


# ---------------------------------------------------------------------------
# WEBHOOK INTAKE LOGIC (shared by api_server.py and the in-app simulator)
# ---------------------------------------------------------------------------
def handle_webhook_employee(payload: dict):
    required = ["emp_name", "emp_no", "phone_number", "department", "working_area", "joining_date"]
    missing = [f for f in required if f not in payload or not str(payload[f]).strip()]
    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"

    dept_id = get_dept_id_by_name(payload["department"])
    if dept_id is None:
        return False, f"Unknown department '{payload['department']}' -- no matching dept_id found."

    return add_pending_employee(
        emp_no=str(payload["emp_no"]),
        emp_name=payload["emp_name"],
        phone_number=payload["phone_number"],
        working_area=payload["working_area"],
        joining_date=payload["joining_date"],
        dept_id=dept_id,
    )