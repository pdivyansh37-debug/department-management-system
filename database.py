"""
database.py
-----------
Postgres backend for the 5-Tier Corporate Department Management System.
Hierarchy: Facility -> Main Dept -> Sub-Dept (OP1) -> Sub-Sub-Dept (op11) -> Workstation (cell-op11-1)
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
        raise RuntimeError("DATABASE_URL is not set.")
    return url

def get_connection():
    return psycopg2.connect(_get_database_url(), cursor_factory=psycopg2.extras.RealDictCursor)

def hash_password(raw_password: str) -> str:
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # 1. PURGE OLD DATA FOR A FRESH START
    tables_to_drop = [
        "employee_skills", "skills", "pending_employees", "employees", 
        "users", "workstations", "sub_sub_departments", "sub_departments", 
        "main_departments", "facilities", "departments"
    ]
    for table in tables_to_drop:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    # 2. CREATE NEW 5-TIER HIERARCHY TABLES
    cur.execute("""
        CREATE TABLE facilities (
            facility_id SERIAL PRIMARY KEY,
            facility_name TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE main_departments (
            main_dept_id SERIAL PRIMARY KEY,
            facility_id INTEGER NOT NULL REFERENCES facilities(facility_id) ON DELETE CASCADE,
            main_dept_name TEXT NOT NULL,
            UNIQUE (facility_id, main_dept_name)
        )
    """)

    # Constraint: Sub-departments MUST be uppercase (e.g., OP1, OP2)
    cur.execute("""
        CREATE TABLE sub_departments (
            sub_dept_id SERIAL PRIMARY KEY,
            main_dept_id INTEGER NOT NULL REFERENCES main_departments(main_dept_id) ON DELETE CASCADE,
            sub_dept_name TEXT NOT NULL CHECK (sub_dept_name = UPPER(sub_dept_name)),
            UNIQUE (main_dept_id, sub_dept_name)
        )
    """)

    # Constraint: Sub-sub-departments MUST be lowercase (e.g., op11, op12)
    cur.execute("""
        CREATE TABLE sub_sub_departments (
            sub_sub_dept_id SERIAL PRIMARY KEY,
            sub_dept_id INTEGER NOT NULL REFERENCES sub_departments(sub_dept_id) ON DELETE CASCADE,
            sub_sub_dept_name TEXT NOT NULL CHECK (sub_sub_dept_name = LOWER(sub_sub_dept_name)),
            UNIQUE (sub_dept_id, sub_sub_dept_name)
        )
    """)

    cur.execute("""
        CREATE TABLE workstations (
            workstation_id SERIAL PRIMARY KEY,
            sub_sub_dept_id INTEGER NOT NULL REFERENCES sub_sub_departments(sub_sub_dept_id) ON DELETE CASCADE,
            workstation_name TEXT NOT NULL,
            UNIQUE (sub_sub_dept_id, workstation_name)
        )
    """)

    # 3. CREATE USERS, EMPLOYEES, AND SKILLS
    cur.execute("""
        CREATE TABLE users (
            user_id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            main_dept_id INTEGER NOT NULL REFERENCES main_departments(main_dept_id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE employees (
            emp_no TEXT PRIMARY KEY,
            emp_name TEXT NOT NULL,
            phone_number TEXT,
            working_area TEXT,
            status TEXT NOT NULL CHECK (status IN ('Working', 'Not Working')),
            joining_date TEXT,
            leaving_date TEXT,
            workstation_id INTEGER NOT NULL REFERENCES workstations(workstation_id)
        )
    """)

    cur.execute("""
        CREATE TABLE pending_employees (
            pending_id SERIAL PRIMARY KEY,
            emp_no TEXT NOT NULL,
            emp_name TEXT NOT NULL,
            phone_number TEXT,
            working_area TEXT,
            joining_date TEXT,
            workstation_id INTEGER NOT NULL REFERENCES workstations(workstation_id),
            skills TEXT,
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Approved', 'Rejected'))
        )
    """)

    cur.execute("""
        CREATE TABLE skills (
            skill_id SERIAL PRIMARY KEY,
            skill_name TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE employee_skills (
            emp_no TEXT NOT NULL REFERENCES employees(emp_no) ON DELETE CASCADE,
            skill_id INTEGER NOT NULL REFERENCES skills(skill_id),
            PRIMARY KEY (emp_no, skill_id)
        )
    """)
    conn.commit()

    # 4. SEED INITIAL EXACT DATA
    _seed_hierarchy_data(cur)
    conn.commit()
    cur.close()
    conn.close()

def generate_prefix(name: str) -> str:
    """Extracts the first two letters of a department name and makes them uppercase."""
    return name[:2].upper()

def _seed_hierarchy_data(cur):
    """Dynamically seeds the database using the new naming convention (OP1 -> op11 -> cell-op11-1)."""
    cur.execute("INSERT INTO facilities (facility_name) VALUES ('Main Plant Alpha') ON CONFLICT DO NOTHING RETURNING facility_id")
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT facility_id FROM facilities WHERE facility_name = 'Main Plant Alpha'")
        row = cur.fetchone()
    fac_id = row["facility_id"]

    main_departments = ["Operations", "Packaging", "Assembly"]

    for main_name in main_departments:
        cur.execute("INSERT INTO main_departments (facility_id, main_dept_name) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING main_dept_id", (fac_id, main_name))
        main_row = cur.fetchone()
        if not main_row:
            cur.execute("SELECT main_dept_id FROM main_departments WHERE main_dept_name = %s", (main_name,))
            main_row = cur.fetchone()
        main_id = main_row["main_dept_id"]

        prefix = generate_prefix(main_name)

        # Create 2 Sub-Departments (e.g., OP1, OP2)
        for sub_idx in range(1, 3):
            sub_name = f"{prefix}{sub_idx}"
            cur.execute("INSERT INTO sub_departments (main_dept_id, sub_dept_name) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING sub_dept_id", (main_id, sub_name))
            sub_row = cur.fetchone()
            if not sub_row:
                cur.execute("SELECT sub_dept_id FROM sub_departments WHERE sub_dept_name = %s AND main_dept_id = %s", (sub_name, main_id))
                sub_row = cur.fetchone()
            sub_id = sub_row["sub_dept_id"]

            # Create 2 Sub-Sub-Departments (e.g., op11, op12)
            for sub_sub_idx in range(1, 3):
                sub_sub_name = f"{sub_name.lower()}{sub_sub_idx}"
                cur.execute("INSERT INTO sub_sub_departments (sub_dept_id, sub_sub_dept_name) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING sub_sub_dept_id", (sub_id, sub_sub_name))
                sub_sub_row = cur.fetchone()
                if not sub_sub_row:
                    cur.execute("SELECT sub_sub_dept_id FROM sub_sub_departments WHERE sub_sub_dept_name = %s AND sub_dept_id = %s", (sub_sub_name, sub_id))
                    sub_sub_row = cur.fetchone()
                sub_sub_id = sub_sub_row["sub_sub_dept_id"]

                # Create 2 Workstations (e.g., cell-op11-1)
                for ws_idx in range(1, 3):
                    ws_name = f"cell-{sub_sub_name}-{ws_idx}"
                    cur.execute("INSERT INTO workstations (sub_sub_dept_id, workstation_name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (sub_sub_id, ws_name))

    # Seed Skills
    for skill in ["Welding", "Machining", "Electrical Wiring", "Quality Inspection"]:
        cur.execute("INSERT INTO skills (skill_name) VALUES (%s) ON CONFLICT DO NOTHING", (skill,))

    # Seed Department Heads
    for main_name in main_departments:
        username = f"{main_name.lower()[:3]}_head" 
        password = hash_password(f"{main_name.lower()[:3]}123") 
        cur.execute("SELECT main_dept_id FROM main_departments WHERE main_dept_name = %s", (main_name,))
        main_id = cur.fetchone()["main_dept_id"]
        cur.execute("INSERT INTO users (username, password, main_dept_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (username, password, main_id))

# ---------------------------------------------------------------------------
# AUTH & USER
# ---------------------------------------------------------------------------
def authenticate_user(username: str, password: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, main_dept_id FROM users WHERE username = %s AND password = %s", (username, hash_password(password)))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None

def change_password(username: str, current_password: str, new_password: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE username = %s AND password = %s", (username, hash_password(current_password)))
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        return False, "Current password is incorrect."
    cur.execute("UPDATE users SET password = %s WHERE username = %s", (hash_password(new_password), username))
    conn.commit()
    cur.close()
    conn.close()
    return True, "Password updated successfully."

# ---------------------------------------------------------------------------
# HIERARCHY GETTERS
# ---------------------------------------------------------------------------
def get_facilities():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM facilities ORDER BY facility_name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_main_departments(facility_id=None):
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM main_departments"
    params = []
    if facility_id:
        query += " WHERE facility_id = %s"
        params.append(facility_id)
    query += " ORDER BY main_dept_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_sub_departments(main_dept_id=None):
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM sub_departments"
    params = []
    if main_dept_id:
        query += " WHERE main_dept_id = %s"
        params.append(main_dept_id)
    query += " ORDER BY sub_dept_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_sub_sub_departments(sub_dept_id=None):
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM sub_sub_departments"
    params = []
    if sub_dept_id:
        query += " WHERE sub_dept_id = %s"
        params.append(sub_dept_id)
    query += " ORDER BY sub_sub_dept_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_workstations(sub_sub_dept_id=None):
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM workstations"
    params = []
    if sub_sub_dept_id:
        query += " WHERE sub_sub_dept_id = %s"
        params.append(sub_sub_dept_id)
    query += " ORDER BY workstation_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_dept_name(main_dept_id: int) -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT main_dept_name FROM main_departments WHERE main_dept_id = %s", (main_dept_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["main_dept_name"] if row else "Unknown"

def get_workstation_id_by_name(ws_name: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT workstation_id FROM workstations WHERE LOWER(TRIM(workstation_name)) = LOWER(TRIM(%s))", (ws_name,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["workstation_id"] if row else None

# ---------------------------------------------------------------------------
# SKILLS & EMPLOYEES
# ---------------------------------------------------------------------------
def get_all_skills():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT skill_id, skill_name FROM skills ORDER BY skill_name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_skill_id_by_name(skill_name: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT skill_id FROM skills WHERE LOWER(TRIM(skill_name)) = LOWER(TRIM(%s))", (skill_name,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["skill_id"] if row else None

def _attach_skills(rows):
    if not rows: return rows
    emp_nos = [r["emp_no"] for r in rows]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT es.emp_no, s.skill_name FROM employee_skills es
        JOIN skills s ON es.skill_id = s.skill_id
        WHERE es.emp_no = ANY(%s) ORDER BY s.skill_name
    """, (emp_nos,))
    skill_rows = cur.fetchall()
    cur.close()
    conn.close()
    skills_by_emp = {}
    for sr in skill_rows:
        skills_by_emp.setdefault(sr["emp_no"], []).append(sr["skill_name"])
    for r in rows:
        r["skills"] = ", ".join(skills_by_emp.get(r["emp_no"], []))
    return rows

def add_employee(emp_no, emp_name, phone_number, working_area, status, joining_date, leaving_date, workstation_id, skill_ids=None):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO employees (emp_no, emp_name, phone_number, working_area, status, joining_date, leaving_date, workstation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (emp_no, emp_name, phone_number, working_area, status, joining_date, leaving_date, workstation_id))
        for skill_id in (skill_ids or []):
            cur.execute("INSERT INTO employee_skills (emp_no, skill_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (emp_no, skill_id))
        conn.commit()
        return True, f"Employee '{emp_name}' added successfully."
    except psycopg2.IntegrityError as e:
        conn.rollback()
        return False, f"Could not add employee: {e}"
    finally:
        cur.close()
        conn.close()

def get_department_employees(main_dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.emp_no, e.emp_name, e.status 
        FROM employees e
        JOIN workstations w ON e.workstation_id = w.workstation_id
        JOIN sub_sub_departments ssd ON w.sub_sub_dept_id = ssd.sub_sub_dept_id
        JOIN sub_departments sd ON ssd.sub_dept_id = sd.sub_dept_id
        WHERE sd.main_dept_id = %s ORDER BY e.emp_name
    """, (main_dept_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def update_employee_status(emp_no, new_status, leaving_date, main_dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE employees SET status = %s, leaving_date = %s
        WHERE emp_no = %s AND workstation_id IN (
            SELECT w.workstation_id FROM workstations w
            JOIN sub_sub_departments ssd ON w.sub_sub_dept_id = ssd.sub_sub_dept_id
            JOIN sub_departments sd ON ssd.sub_dept_id = sd.sub_dept_id
            WHERE sd.main_dept_id = %s
        )
    """, (new_status, leaving_date, emp_no, main_dept_id))
    conn.commit()
    updated = cur.rowcount > 0
    cur.close()
    conn.close()
    return (True, f"'{emp_no}' status updated.") if updated else (False, "Employee not found in your department scope.")

def get_summary_stats():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM employees")
    total = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM employees WHERE status = 'Working'")
    working = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM employees WHERE status = 'Not Working'")
    not_working = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM main_departments")
    departments = cur.fetchone()["c"]
    cur.close()
    conn.close()
    return {"total": total, "working": working, "not_working": not_working, "departments": departments}

def search_employees(search_term=None, sort_by="Name", sort_order="Ascending", status=None):
    allowed_sort_columns = {
        "Name": "e.emp_name", "Employee No": "e.emp_no", "Main Dept": "md.main_dept_name",
        "Status": "e.status", "Joining Date": "e.joining_date",
    }
    sort_column = allowed_sort_columns.get(sort_by, "e.emp_name")
    order = "ASC" if sort_order == "Ascending" else "DESC"

    query = """
        SELECT e.emp_no, e.emp_name, e.phone_number, e.working_area, e.status, e.joining_date, e.leaving_date, 
               f.facility_name, md.main_dept_name, sd.sub_dept_name, ssd.sub_sub_dept_name, w.workstation_name
        FROM employees e
        JOIN workstations w ON e.workstation_id = w.workstation_id
        JOIN sub_sub_departments ssd ON w.sub_sub_dept_id = ssd.sub_sub_dept_id
        JOIN sub_departments sd ON ssd.sub_dept_id = sd.sub_dept_id
        JOIN main_departments md ON sd.main_dept_id = md.main_dept_id
        JOIN facilities f ON md.facility_id = f.facility_id
        WHERE 1=1
    """
    params = []
    if search_term:
        query += """ AND (
            e.emp_name ILIKE %s OR e.emp_no ILIKE %s OR e.phone_number ILIKE %s OR
            md.main_dept_name ILIKE %s OR sd.sub_dept_name ILIKE %s OR
            ssd.sub_sub_dept_name ILIKE %s OR w.workstation_name ILIKE %s
        )"""
        params.extend([f"%{search_term}%"] * 7)
    if status:
        query += " AND e.status = %s"
        params.append(status)

    query += f" ORDER BY {sort_column} {order}"
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return _attach_skills([dict(r) for r in rows])

# ---------------------------------------------------------------------------
# PENDING APPROVALS & WEBHOOKS
# ---------------------------------------------------------------------------
def add_pending_employee(emp_no, emp_name, phone_number, working_area, joining_date, workstation_id, skills=""):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO pending_employees (emp_no, emp_name, phone_number, working_area, joining_date, workstation_id, submitted_at, status, skills)
            VALUES (%s, %s, %s, %s, %s, %s, NOW()::text, 'Pending', %s)
        """, (emp_no, emp_name, phone_number, working_area, joining_date, workstation_id, skills))
        conn.commit()
        return True, "Submission received — awaiting approval."
    except psycopg2.IntegrityError as e:
        conn.rollback()
        return False, f"Could not submit: {e}"
    finally:
        cur.close()
        conn.close()

def get_pending_employees(main_dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.pending_id, p.emp_no, p.emp_name, p.phone_number, p.working_area, p.joining_date, p.submitted_at, p.skills,
               w.workstation_name, ssd.sub_sub_dept_name, sd.sub_dept_name
        FROM pending_employees p
        JOIN workstations w ON p.workstation_id = w.workstation_id
        JOIN sub_sub_departments ssd ON w.sub_sub_dept_id = ssd.sub_sub_dept_id
        JOIN sub_departments sd ON ssd.sub_dept_id = sd.sub_dept_id
        WHERE sd.main_dept_id = %s AND p.status = 'Pending' ORDER BY p.submitted_at
    """, (main_dept_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def approve_pending_employee(pending_id, main_dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pending_employees WHERE pending_id = %s AND status = 'Pending'", (pending_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None: return False, "Submission not found or already handled."

    skill_names = [s.strip() for s in (row.get("skills") or "").split(",") if s.strip()]
    skill_ids = [sid for sid in (get_skill_id_by_name(name) for name in skill_names) if sid is not None]

    success, message = add_employee(
        emp_no=row["emp_no"], emp_name=row["emp_name"], phone_number=row["phone_number"], working_area=row["working_area"],
        status="Working", joining_date=row["joining_date"], leaving_date=None, workstation_id=row["workstation_id"], skill_ids=skill_ids
    )
    if not success: return False, message

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE pending_employees SET status = 'Approved' WHERE pending_id = %s", (pending_id,))
    conn.commit()
    cur.close()
    conn.close()
    return True, f"Approved — '{row['emp_name']}' added to Employees."

def reject_pending_employee(pending_id, main_dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE pending_employees SET status = 'Rejected' WHERE pending_id = %s AND status = 'Pending'", (pending_id,))
    conn.commit()
    updated = cur.rowcount > 0
    cur.close()
    conn.close()
    return (True, "Submission rejected.") if updated else (False, "Failed to reject.")

def handle_webhook_employee(payload: dict):
    required = ["emp_name", "emp_no", "phone_number", "workstation_name", "working_area", "joining_date"]
    missing = [f for f in required if f not in payload or not str(payload[f]).strip()]
    if missing: return False, f"Missing required fields: {', '.join(missing)}"

    ws_id = get_workstation_id_by_name(payload["workstation_name"])
    if ws_id is None: return False, f"Unknown workstation '{payload['workstation_name']}'."

    skills_val = payload.get("skills") or []
    skills_text = skills_val if isinstance(skills_val, str) else ", ".join(skills_val)

    return add_pending_employee(
        emp_no=str(payload["emp_no"]), emp_name=payload["emp_name"], phone_number=payload["phone_number"],
        working_area=payload["working_area"], joining_date=payload["joining_date"], workstation_id=ws_id, skills=skills_text
    )