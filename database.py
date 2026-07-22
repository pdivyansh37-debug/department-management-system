"""
database.py
-----------
Postgres backend for 4-Tier Plant Hierarchy Management System.
Hierarchy: Plant (1-5) -> Operating Unit (Dept) -> Area (Dept Keywords) -> Workstation (Dept Keywords)
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

    # Drop legacy tables for a completely fresh start
    tables_to_drop = [
        "employee_skills", "skills", "pending_employees", "employees", 
        "users", "workstations", "areas", "operating_units", "plants", 
        "sub_sub_departments", "sub_departments", "main_departments", "facilities", "departments"
    ]
    for table in tables_to_drop:
        cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    # 1. 4-Tier Hierarchy Tables
    cur.execute("""
        CREATE TABLE plants (
            plant_id SERIAL PRIMARY KEY,
            plant_name TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE operating_units (
            ou_id SERIAL PRIMARY KEY,
            plant_id INTEGER NOT NULL REFERENCES plants(plant_id) ON DELETE CASCADE,
            ou_name TEXT NOT NULL,
            UNIQUE (plant_id, ou_name)
        )
    """)

    cur.execute("""
        CREATE TABLE areas (
            area_id SERIAL PRIMARY KEY,
            ou_id INTEGER NOT NULL REFERENCES operating_units(ou_id) ON DELETE CASCADE,
            area_name TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE workstations (
            workstation_id SERIAL PRIMARY KEY,
            area_id INTEGER NOT NULL REFERENCES areas(area_id) ON DELETE CASCADE,
            workstation_name TEXT NOT NULL
        )
    """)

    # 2. Users, Employees & Skills
    cur.execute("""
        CREATE TABLE users (
            user_id SERIAL PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            ou_id INTEGER NOT NULL REFERENCES operating_units(ou_id) ON DELETE CASCADE
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

    _seed_hierarchy_data(cur)
    conn.commit()
    cur.close()
    conn.close()

def _seed_hierarchy_data(cur):
    """Seeds 5 Plants, 5 Operating Units (Departments), with 4-5 options per layer using dept keywords."""
    # 5 Plants
    plant_ids = []
    for i in range(1, 6):
        cur.execute("INSERT INTO plants (plant_name) VALUES (%s) ON CONFLICT DO NOTHING RETURNING plant_id", (f"Plant {i},",))
        # Clean comma if needed, or keep standard naming:
    
    # Let's cleanly insert 5 plants
    plant_ids = []
    for i in range(1, 6):
        p_name = f"Plant {i}"
        cur.execute("INSERT INTO plants (plant_name) VALUES (%s) ON CONFLICT (plant_name) DO NOTHING RETURNING plant_id", (p_name,))
        row = cur.fetchone()
        if not row:
            cur.execute("SELECT plant_id FROM plants WHERE plant_name = %s", (p_name,))
            row = cur.fetchone()
        plant_ids.append(row["plant_id"])

    # 5 Operating Units (Departments) mapped across Plant 1
    departments = [
        {"name": "Operations", "prefix": "OPS"},
        {"name": "Packaging", "prefix": "PKG"},
        {"name": "Assembly", "prefix": "ASM"},
        {"name": "Quality Control", "prefix": "QC"},
        {"name": "Logistics", "prefix": "LOG"}
    ]

    primary_plant_id = plant_ids[0] # Default to Plant 1 for main dept mapping

    for dept in departments:
        ou_name = dept["name"]
        prefix = dept["prefix"]

        cur.execute(
            "INSERT INTO operating_units (plant_id, ou_name) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING ou_id",
            (primary_plant_id, ou_name)
        )
        ou_row = cur.fetchone()
        if not ou_row:
            cur.execute("SELECT ou_id FROM operating_units WHERE plant_id = %s AND ou_name = %s", (primary_plant_id, ou_name))
            ou_row = cur.fetchone()
        ou_id = ou_row["ou_id"]

        # Create 4-5 Areas using Department Keyword
        for a_idx in range(1, 5):
            area_name = f"{prefix}-Area-{a_idx}"
            cur.execute(
                "INSERT INTO areas (ou_id, area_name) VALUES (%s, %s) RETURNING area_id",
                (ou_id, area_name)
            )
            area_id = cur.fetchone()["area_id"]

            # Create 4-5 Workstations per Area using Department Keywords
            for w_idx in range(1, 5):
                ws_name = f"Cell-{prefix}-{a_idx}-{w_idx}"
                cur.execute(
                    "INSERT INTO workstations (area_id, workstation_name) VALUES (%s, %s)",
                    (area_id, ws_name)
                )

        # Create Login Account for each Department Head
        username = f"{prefix.lower()}_head"
        password = hash_password(f"{prefix.lower()}123")
        cur.execute(
            "INSERT INTO users (username, password, ou_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (username, password, ou_id)
        )

    # Seed Skills
    for skill in ["Welding", "Machining", "Electrical Wiring", "Forklift Operation", "Quality Inspection"]:
        cur.execute("INSERT INTO skills (skill_name) VALUES (%s) ON CONFLICT DO NOTHING", (skill,))

# ---------------------------------------------------------------------------
# AUTH & GETTERS
# ---------------------------------------------------------------------------
def authenticate_user(username: str, password: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, username, ou_id FROM users WHERE username = %s AND password = %s", (username, hash_password(password)))
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

def get_plants():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM plants ORDER BY plant_name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_operating_units(plant_id=None):
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM operating_units"
    params = []
    if plant_id:
        query += " WHERE plant_id = %s"
        params.append(plant_id)
    query += " ORDER BY ou_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_areas(ou_id=None):
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM areas"
    params = []
    if ou_id:
        query += " WHERE ou_id = %s"
        params.append(ou_id)
    query += " ORDER BY area_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_workstations(area_id=None):
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT * FROM workstations"
    params = []
    if area_id:
        query += " WHERE area_id = %s"
        params.append(area_id)
    query += " ORDER BY workstation_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

def get_ou_name(ou_id: int) -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT ou_name FROM operating_units WHERE ou_id = %s", (ou_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["ou_name"] if row else "Unknown"

def get_all_skills():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT skill_id, skill_name FROM skills ORDER BY skill_name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]

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

def get_department_employees(ou_id):
    """Returns employees scoped strictly to the given Operating Unit (Department)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT e.emp_no, e.emp_name, e.phone_number, e.working_area, e.status, e.joining_date, e.leaving_date,
               p.plant_name, ou.ou_name, a.area_name, w.workstation_name
        FROM employees e
        JOIN workstations w ON e.workstation_id = w.workstation_id
        JOIN areas a ON w.area_id = a.area_id
        JOIN operating_units ou ON a.ou_id = ou.ou_id
        JOIN plants p ON ou.plant_id = p.plant_id
        WHERE ou.ou_id = %s ORDER BY e.emp_name
    """, (ou_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return _attach_skills([dict(r) for r in rows])

def update_employee_status(emp_no, new_status, leaving_date, ou_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE employees SET status = %s, leaving_date = %s
        WHERE emp_no = %s AND workstation_id IN (
            SELECT w.workstation_id FROM workstations w
            JOIN areas a ON w.area_id = a.area_id
            JOIN operating_units ou ON a.ou_id = ou.ou_id
            WHERE ou.ou_id = %s
        )
    """, (new_status, leaving_date, emp_no, ou_id))
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
    cur.execute("SELECT COUNT(*) AS c FROM operating_units")
    departments = cur.fetchone()["c"]
    cur.close()
    conn.close()
    return {"total": total, "working": working, "not_working": not_working, "departments": departments}

def search_employees(search_term=None, sort_by="Name", sort_order="Ascending", status=None, ou_id=None):
    allowed_sort_columns = {
        "Name": "e.emp_name", "Employee No": "e.emp_no", "Department": "ou.ou_name",
        "Status": "e.status", "Joining Date": "e.joining_date",
    }
    sort_column = allowed_sort_columns.get(sort_by, "e.emp_name")
    order = "ASC" if sort_order == "Ascending" else "DESC"

    query = """
        SELECT e.emp_no, e.emp_name, e.phone_number, e.working_area, e.status, e.joining_date, e.leaving_date, 
               p.plant_name, ou.ou_name, a.area_name, w.workstation_name
        FROM employees e
        JOIN workstations w ON e.workstation_id = w.workstation_id
        JOIN areas a ON w.area_id = a.area_id
        JOIN operating_units ou ON a.ou_id = ou.ou_id
        JOIN plants p ON ou.plant_id = p.plant_id
        WHERE 1=1
    """
    params = []
    if ou_id:
        query += " AND ou.ou_id = %s"
        params.append(ou_id)
    if search_term:
        query += """ AND (
            e.emp_name ILIKE %s OR e.emp_no ILIKE %s OR e.phone_number ILIKE %s OR
            ou.ou_name ILIKE %s OR a.area_name ILIKE %s OR w.workstation_name ILIKE %s
        )"""
        params.extend([f"%{search_term}%"] * 6)
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