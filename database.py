"""
database.py (Postgres edition -- hierarchical departments)
------------------------------------------------------------
Departments are a self-referencing tree. The full model supports 5 levels:

    Facility -> Main Department -> Sub-Department -> Section/Line -> Workstation/Cell

but Facility is NOT IN USE YET -- it stays in the schema's CHECK constraint
so it can be turned on later without a migration, but nothing creates a
Facility row right now. Main Department is the current root of the tree
(parent_id = NULL). To bring Facility back later: call add_facility(), then
pass its dept_id into add_main_department() as the new parent -- see the
comment on add_main_department() for exactly what to change.

Every node lives in ONE `departments` table (adjacency list: parent_id points
at its parent). Two columns make queries fast without recursive CTEs:

  - main_dept_id : every node's ancestor at the "Main Department" level
                    (a Main Department's main_dept_id points at itself).
                    Lets us answer "does this workstation belong to this
                    head's department?" with a single equality check.
  - path_name    : the full human-readable breadcrumb, precomputed once at
                    insert time, e.g. "Assembly (AB) > AB1 > AB1A > AB1A1".
                    Used for display AND as the free-text search target on
                    the Find Employee page -- searching "AB1" finds every
                    employee anywhere under that sub-department.

Naming rule:
  Main Department    : you supply a short code, e.g. "AB"    (uppercased)
  Sub-Department      : you type a free-text name
  Section/Line        : you type a free-text name
  Workstation/Cell    : you type a free-text name
Below Main Department, names are fully free text -- the only rule is
"not blank" and "unique among siblings under the same parent" (enforced by
a UNIQUE(parent_id, name) constraint). Two different Sub-Departments in
different branches of the tree can happily share a name; only siblings
under the exact same parent can't collide.

Employees attach ONLY at Workstation/Cell (the leaf level). Department heads
(users) attach ONLY at Main Department level -- same as Phase 1.

IMPORTANT: every function keeps a stable name/signature so app.py stays
readable; only the internals (and a few new hierarchy functions) changed.
"""

import hashlib
import os

import psycopg2
import psycopg2.extras

# Active levels, root to leaf. 'Facility' deliberately excluded for now --
# see the module docstring above for how to re-enable it later.
LEVELS = ["Main Department", "Sub-Department", "Section/Line", "Workstation/Cell"]


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
    return psycopg2.connect(_get_database_url(), cursor_factory=psycopg2.extras.RealDictCursor)


def hash_password(raw_password: str) -> str:
    return hashlib.sha256(raw_password.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# SCHEMA
# ---------------------------------------------------------------------------
def _old_schema_present(cur) -> bool:
    """True if either `departments` or `users` predates the hierarchy model.
    Checked independently per table -- a database can have a stale `users`
    table (e.g. missing/renamed `dept_id`) even if `departments` looks fine,
    or vice versa, depending on exactly when a previous deploy failed."""
    cur.execute("SELECT to_regclass('public.departments') AS t")
    departments_exists = cur.fetchone()["t"] is not None
    if departments_exists:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'departments' AND column_name = 'level'"
        )
        if cur.fetchone() is None:
            return True

        # Facility used to be the root; it's dormant now (Main Department is
        # the root). If any row is still parented under a Facility from an
        # earlier run, the tree is built on the old shape -- rebuild.
        cur.execute("SELECT 1 FROM departments WHERE level = 'Facility' LIMIT 1")
        if cur.fetchone() is not None:
            return True

    cur.execute("SELECT to_regclass('public.users') AS t")
    users_exists = cur.fetchone()["t"] is not None
    if users_exists:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'dept_id'"
        )
        if cur.fetchone() is None:
            return True

    return False


def init_db():
    """Create tables if missing and seed a starter hierarchy + mock heads."""
    conn = get_connection()
    cur = conn.cursor()

    # ---- One-time destructive migration off the old flat department model.
    # Confirmed with the project owner that existing data can be discarded.
    if _old_schema_present(cur):
        cur.execute(
            "DROP TABLE IF EXISTS pending_employees, employee_skills, employees, "
            "users, departments CASCADE"
        )
        conn.commit()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            dept_id         SERIAL PRIMARY KEY,
            name            TEXT NOT NULL,          -- code/identifier at this node
            label           TEXT,                   -- friendly name (Facility / Main Dept only)
            level           TEXT NOT NULL CHECK (level IN (
                                'Facility', 'Main Department', 'Sub-Department',
                                'Section/Line', 'Workstation/Cell')),
            parent_id       INTEGER REFERENCES departments(dept_id) ON DELETE CASCADE,
            main_dept_id    INTEGER REFERENCES departments(dept_id),
            path_name       TEXT NOT NULL,
            child_counter   INTEGER NOT NULL DEFAULT 0,
            UNIQUE (parent_id, name)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     SERIAL PRIMARY KEY,
            username    TEXT NOT NULL UNIQUE,
            password    TEXT NOT NULL,
            dept_id     INTEGER NOT NULL REFERENCES departments(dept_id)  -- Main Department only
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
            dept_id         INTEGER NOT NULL REFERENCES departments(dept_id)  -- Workstation/Cell only
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
            dept_id         INTEGER NOT NULL REFERENCES departments(dept_id),  -- Workstation/Cell only
            submitted_at    TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'Pending'
                            CHECK (status IN ('Pending', 'Approved', 'Rejected')),
            skills          TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            skill_id    SERIAL PRIMARY KEY,
            skill_name  TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS employee_skills (
            emp_no      TEXT NOT NULL REFERENCES employees(emp_no) ON DELETE CASCADE,
            skill_id    INTEGER NOT NULL REFERENCES skills(skill_id),
            PRIMARY KEY (emp_no, skill_id)
        )
    """)
    conn.commit()

    # ---- Seed skills ----
    for name in ["Welding", "Machining", "Electrical Wiring", "Forklift Operation",
                 "Quality Inspection", "CNC Operation", "Painting", "Assembly Line Operation"]:
        cur.execute(
            "INSERT INTO skills (skill_name) VALUES (%s) ON CONFLICT (skill_name) DO NOTHING",
            (name,),
        )
    conn.commit()

    # ---- Seed a starter hierarchy, only if departments is empty ----
    cur.execute("SELECT COUNT(*) AS c FROM departments")
    if cur.fetchone()["c"] == 0:
        cur.close()
        conn.close()
        _seed_starter_hierarchy()
        conn = get_connection()
        cur = conn.cursor()

    # ---- Seed one Department Head login per Main Department (idempotent) ----
    cur.execute("SELECT dept_id, name FROM departments WHERE level = 'Main Department'")
    main_by_code = {row["name"]: row["dept_id"] for row in cur.fetchall()}

    mock_users = [
        ("x_head", "welding123", main_by_code.get("OP")),
        ("y_head", "yhead123", main_by_code.get("PK")),
        ("assembly_head", "assembly123", main_by_code.get("AS")),
        ("qc_head", "qualitycontrol123", main_by_code.get("QC")),
        ("logistics_head", "logistics123", main_by_code.get("LG")),
        ("rnd_head", "randd123", main_by_code.get("RD")),
        ("maintenance_head", "maintenance123", main_by_code.get("MT")),
    ]
    for username, plain_pw, dept_id in mock_users:
        if dept_id is None:
            continue
        cur.execute(
            "INSERT INTO users (username, password, dept_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (username) DO NOTHING",
            (username, hash_password(plain_pw), dept_id),
        )
    conn.commit()
    cur.close()
    conn.close()


def _seed_starter_hierarchy():
    """7 Main Departments as the current root of the tree, matching the old
    department list. Sub-Departments/Sections/Workstations are left for
    heads to build via the Manage Departments page -- that's the whole
    point of self-service. (No Facility row is created -- see module
    docstring for how to add that layer back in later.)"""
    for label, code in [
        ("Operations", "OP"), ("Packaging", "PK"), ("Assembly", "AS"),
        ("Quality Control", "QC"), ("Logistics", "LG"), ("R&D", "RD"),
        ("Maintenance", "MT"),
    ]:
        add_main_department(label, code)


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------
def authenticate_user(username: str, password: str):
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


def change_password(username: str, current_password: str, new_password: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM users WHERE username = %s AND password = %s",
        (username, hash_password(current_password)),
    )
    row = cur.fetchone()
    if row is None:
        cur.close()
        conn.close()
        return False, "Current password is incorrect."

    cur.execute(
        "UPDATE users SET password = %s WHERE username = %s",
        (hash_password(new_password), username),
    )
    conn.commit()
    cur.close()
    conn.close()
    return True, "Password updated successfully."


# ---------------------------------------------------------------------------
# DEPARTMENT HIERARCHY -- WRITE
# ---------------------------------------------------------------------------
def add_facility(name: str) -> int:
    """DORMANT for now -- not called anywhere while Main Department is the
    tree's root. Kept so re-enabling the Facility layer later is just:
    (1) call this to create the Facility row, (2) change
    add_main_department() below to take a facility_id again and set it as
    parent_id/prefix the path_name with it, instead of leaving both NULL/bare
    like it does today. Returns the new dept_id."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO departments (name, label, level, parent_id, main_dept_id, path_name)
        VALUES (%s, %s, 'Facility', NULL, NULL, %s)
        RETURNING dept_id
        """,
        (name.strip(), name.strip(), name.strip()),
    )
    dept_id = cur.fetchone()["dept_id"]
    conn.commit()
    cur.close()
    conn.close()
    return dept_id


def add_main_department(label: str, code: str):
    """Currently the ROOT of the tree (parent_id = NULL) -- Facility is not
    in use yet. `code` is short, user-chosen, uppercased (e.g. 'AB') and
    becomes the seed for every code auto-generated further down its subtree."""
    code = code.strip().upper()
    label = label.strip()
    if not code or not code.isalpha():
        return False, "Department code must be letters only (e.g. 'AB')."

    conn = get_connection()
    cur = conn.cursor()
    path_name = f"{label} ({code})"
    try:
        cur.execute(
            """
            INSERT INTO departments (name, label, level, parent_id, main_dept_id, path_name)
            VALUES (%s, %s, 'Main Department', NULL, NULL, %s)
            RETURNING dept_id
            """,
            (code, label, path_name),
        )
        dept_id = cur.fetchone()["dept_id"]
        # self-reference: a Main Department is its own main_dept_id anchor
        cur.execute("UPDATE departments SET main_dept_id = %s WHERE dept_id = %s", (dept_id, dept_id))
        conn.commit()
        return True, dept_id
    except psycopg2.IntegrityError:
        conn.rollback()
        return False, f"Department code '{code}' already exists."
    finally:
        cur.close()
        conn.close()


def add_child_department(parent_id: int, name: str):
    """Inserts a user-named Sub-Department / Section-Line / Workstation-Cell
    under `parent_id`, at whatever level comes next after the parent's level.
    `name` is free text -- the only rules are "not blank" and "unique among
    siblings under this same parent" (enforced by the UNIQUE(parent_id, name)
    constraint, so two different Sub-Departments elsewhere in the tree can
    freely share a name -- only siblings can't collide).
    Returns (success, dept_id_or_message)."""
    name = (name or "").strip()
    if not name:
        return False, "Name cannot be blank."

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM departments WHERE dept_id = %s", (parent_id,))
    parent = cur.fetchone()
    if parent is None:
        cur.close()
        conn.close()
        return False, "Parent department not found."

    parent_level = parent["level"]
    if parent_level not in LEVELS[:-1]:
        cur.close()
        conn.close()
        return False, f"'{parent_level}' is the lowest level -- nothing can be added under it."
    child_level = LEVELS[LEVELS.index(parent_level) + 1]
    if child_level == "Main Department":
        cur.close()
        conn.close()
        return False, "Use add_main_department() for this level (it needs a user-chosen code)."

    path_name = f"{parent['path_name']} > {name}"

    try:
        cur.execute(
            """
            INSERT INTO departments (name, label, level, parent_id, main_dept_id, path_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING dept_id
            """,
            (name, name, child_level, parent_id, parent["main_dept_id"], path_name),
        )
        dept_id = cur.fetchone()["dept_id"]
        cur.execute(
            "UPDATE departments SET child_counter = child_counter + 1 WHERE dept_id = %s",
            (parent_id,),
        )
        conn.commit()
        return True, dept_id
    except psycopg2.IntegrityError:
        conn.rollback()
        return False, f"'{name}' already exists under '{parent['name']}' -- pick a different name."
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# DEPARTMENT HIERARCHY -- READ
# ---------------------------------------------------------------------------
def get_facilities():
    """DORMANT for now -- returns [] until add_facility() is actually used
    somewhere. Left in place for when the Facility layer comes back."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT dept_id, name, label, path_name FROM departments WHERE level = 'Facility' ORDER BY name")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_main_departments():
    """Main Department is the current root of the tree, so this is simply
    every row at that level -- no facility filter needed for now."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT dept_id, name, label, path_name, parent_id FROM departments "
        "WHERE level = 'Main Department' ORDER BY label"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_children(parent_id: int):
    """Immediate children of any department node, ordered by name."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT dept_id, name, label, level, path_name FROM departments "
        "WHERE parent_id = %s ORDER BY name",
        (parent_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_department(dept_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM departments WHERE dept_id = %s", (dept_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def get_leaf_departments(main_dept_id=None):
    """Workstation/Cell nodes only -- the level employees actually attach to.
    Pass main_dept_id to scope this to one head's subtree."""
    conn = get_connection()
    cur = conn.cursor()
    query = "SELECT dept_id, name, path_name FROM departments WHERE level = 'Workstation/Cell'"
    params = []
    if main_dept_id:
        query += " AND main_dept_id = %s"
        params.append(main_dept_id)
    query += " ORDER BY path_name"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_all_departments():
    """Back-compat name used by the public submission page's department
    picker -- now returns leaf (Workstation/Cell) departments, since that's
    where an employee actually gets assigned. Each item exposes 'dept_name'
    as the full path for a self-explanatory dropdown label."""
    leaves = get_leaf_departments()
    return [{"dept_id": d["dept_id"], "dept_name": d["path_name"]} for d in leaves]


def get_dept_name(dept_id: int) -> str:
    """Full breadcrumb path for a department, e.g. 'Plant 1 > Assembly (AS) > AS1'."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT path_name FROM departments WHERE dept_id = %s", (dept_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["path_name"] if row else "Unknown"


def get_dept_id_by_name(name: str):
    """Resolves a leaf department by its exact code (e.g. 'AS1A2'), case/space
    insensitive -- used by the webhook/public-submission intake path."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT dept_id FROM departments WHERE level = 'Workstation/Cell' "
        "AND LOWER(TRIM(name)) = LOWER(TRIM(%s))",
        (name,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["dept_id"] if row else None


# ---------------------------------------------------------------------------
# SKILLS
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
    cur.execute(
        "SELECT skill_id FROM skills WHERE LOWER(TRIM(skill_name)) = LOWER(TRIM(%s))",
        (skill_name,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["skill_id"] if row else None


def _attach_skills(rows):
    if not rows:
        return rows
    emp_nos = [r["emp_no"] for r in rows]
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT es.emp_no, s.skill_name
        FROM employee_skills es
        JOIN skills s ON es.skill_id = s.skill_id
        WHERE es.emp_no = ANY(%s)
        ORDER BY s.skill_name
        """,
        (emp_nos,),
    )
    skill_rows = cur.fetchall()
    cur.close()
    conn.close()

    skills_by_emp = {}
    for sr in skill_rows:
        skills_by_emp.setdefault(sr["emp_no"], []).append(sr["skill_name"])
    for r in rows:
        r["skills"] = ", ".join(skills_by_emp.get(r["emp_no"], []))
    return rows


# ---------------------------------------------------------------------------
# EMPLOYEES -- WRITE (restricted)
# ---------------------------------------------------------------------------
def add_employee(emp_no, emp_name, phone_number, working_area, status,
                  joining_date, leaving_date, dept_id, skill_ids=None):
    """dept_id MUST be a Workstation/Cell leaf -- enforced here, not just by
    convention, so a bad dept_id can never silently create a mis-scoped
    employee record."""
    dept = get_department(dept_id)
    if dept is None or dept["level"] != "Workstation/Cell":
        return False, "Employees must be assigned to a Workstation/Cell (the lowest level)."

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
        for skill_id in (skill_ids or []):
            cur.execute(
                "INSERT INTO employee_skills (emp_no, skill_id) VALUES (%s, %s) "
                "ON CONFLICT (emp_no, skill_id) DO NOTHING",
                (emp_no, skill_id),
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
def get_employees(dept_id=None, main_dept_id=None, status=None):
    """dept_id filters to one exact leaf; main_dept_id filters to an entire
    head's subtree (any workstation under it) -- pass whichever you have."""
    query = """
        SELECT e.emp_no, e.emp_name, e.phone_number, e.working_area,
               e.status, e.joining_date, e.leaving_date,
               d.path_name AS dept_name, d.dept_id AS dept_id
        FROM employees e
        JOIN departments d ON e.dept_id = d.dept_id
        WHERE 1=1
    """
    params = []
    if dept_id:
        query += " AND e.dept_id = %s"
        params.append(dept_id)
    if main_dept_id:
        query += " AND d.main_dept_id = %s"
        params.append(main_dept_id)
    if status:
        query += " AND e.status = %s"
        params.append(status)
    query += " ORDER BY d.path_name, e.emp_name"

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return _attach_skills([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# EMPLOYEES -- OWN-DEPARTMENT LIST (for the "update status" dropdown)
# ---------------------------------------------------------------------------
def get_department_employees(main_dept_id):
    """Everyone anywhere under this head's Main Department subtree."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.emp_no, e.emp_name, e.status
        FROM employees e
        JOIN departments d ON e.dept_id = d.dept_id
        WHERE d.main_dept_id = %s
        ORDER BY e.emp_name
        """,
        (main_dept_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# EMPLOYEES -- UPDATE STATUS (restricted)
# ---------------------------------------------------------------------------
def update_employee_status(emp_no, new_status, leaving_date, dept_id):
    """dept_id here is the head's main_dept_id -- the UPDATE only matches if
    the employee's workstation actually falls under that subtree."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE employees
        SET status = %s, leaving_date = %s
        WHERE emp_no = %s AND dept_id IN (
            SELECT dept_id FROM departments WHERE main_dept_id = %s
        )
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
    cur.execute("SELECT COUNT(*) AS c FROM departments WHERE level = 'Main Department'")
    departments = cur.fetchone()["c"]
    cur.close()
    conn.close()
    return {"total": total, "working": working, "not_working": not_working, "departments": departments}


# ---------------------------------------------------------------------------
# EMPLOYEES -- SEARCH & SORT (global, for the "Find Employee" tab)
# ---------------------------------------------------------------------------
def search_employees(search_term=None, sort_by="Name", sort_order="Ascending", status=None):
    """search_term matches against the FULL hierarchy path (path_name), so
    typing a facility, main department, sub-department, section, or
    workstation code -- at any level -- finds everyone under it."""
    allowed_sort_columns = {
        "Name": "e.emp_name",
        "Employee No": "e.emp_no",
        "Department": "d.path_name",
        "Status": "e.status",
        "Joining Date": "e.joining_date",
    }
    sort_column = allowed_sort_columns.get(sort_by, "e.emp_name")
    order = "ASC" if sort_order == "Ascending" else "DESC"

    query = """
        SELECT e.emp_no, e.emp_name, e.phone_number, e.working_area,
               e.status, e.joining_date, e.leaving_date, d.path_name AS dept_name
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
            d.path_name ILIKE %s OR
            EXISTS (
                SELECT 1 FROM employee_skills es
                JOIN skills s ON es.skill_id = s.skill_id
                WHERE es.emp_no = e.emp_no AND s.skill_name ILIKE %s
            )
        )"""
        like_term = f"%{search_term}%"
        params.extend([like_term] * 6)
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
# PENDING APPROVALS (externally-submitted data awaiting head sign-off)
# ---------------------------------------------------------------------------
def add_pending_employee(emp_no, emp_name, phone_number, working_area, joining_date, dept_id, skills=""):
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO pending_employees
                (emp_no, emp_name, phone_number, working_area, joining_date, dept_id, submitted_at, status, skills)
            VALUES (%s, %s, %s, %s, %s, %s, NOW()::text, 'Pending', %s)
            """,
            (emp_no, emp_name, phone_number, working_area, joining_date, dept_id, skills),
        )
        conn.commit()
        return True, "Submission received — awaiting department head approval."
    except psycopg2.IntegrityError as e:
        conn.rollback()
        return False, f"Could not submit: {e}"
    finally:
        cur.close()
        conn.close()


def get_pending_employees(main_dept_id):
    """Pending rows anywhere under this head's subtree."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.pending_id, p.emp_no, p.emp_name, p.phone_number, p.working_area,
               p.joining_date, p.submitted_at, p.skills, d.path_name AS dept_name
        FROM pending_employees p
        JOIN departments d ON p.dept_id = d.dept_id
        WHERE d.main_dept_id = %s AND p.status = 'Pending'
        ORDER BY p.submitted_at
        """,
        (main_dept_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def approve_pending_employee(pending_id, main_dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.* FROM pending_employees p
        JOIN departments d ON p.dept_id = d.dept_id
        WHERE p.pending_id = %s AND d.main_dept_id = %s AND p.status = 'Pending'
        """,
        (pending_id, main_dept_id),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None:
        return False, "Submission not found in your department (or already handled)."

    skill_names = [s.strip() for s in (row.get("skills") or "").split(",") if s.strip()]
    skill_ids = [sid for sid in (get_skill_id_by_name(name) for name in skill_names) if sid is not None]

    success, message = add_employee(
        emp_no=row["emp_no"],
        emp_name=row["emp_name"],
        phone_number=row["phone_number"],
        working_area=row["working_area"],
        status="Working",
        joining_date=row["joining_date"],
        leaving_date=None,
        dept_id=row["dept_id"],
        skill_ids=skill_ids,
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


def reject_pending_employee(pending_id, main_dept_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE pending_employees SET status = 'Rejected'
        WHERE pending_id = %s AND status = 'Pending' AND dept_id IN (
            SELECT dept_id FROM departments WHERE main_dept_id = %s
        )
        """,
        (pending_id, main_dept_id),
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
    """payload['department'] must be the exact leaf (Workstation/Cell) code,
    e.g. 'AS1A2' -- that's what get_all_departments() now offers as options."""
    required = ["emp_name", "emp_no", "phone_number", "department", "working_area", "joining_date"]
    missing = [f for f in required if f not in payload or not str(payload[f]).strip()]
    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"

    dept_id = get_dept_id_by_name(payload["department"])
    if dept_id is None:
        return False, f"Unknown department '{payload['department']}' -- no matching workstation found."

    skills_value = payload.get("skills") or []
    skills_text = skills_value if isinstance(skills_value, str) else ", ".join(skills_value)

    return add_pending_employee(
        emp_no=str(payload["emp_no"]),
        emp_name=payload["emp_name"],
        phone_number=payload["phone_number"],
        working_area=payload["working_area"],
        joining_date=payload["joining_date"],
        dept_id=dept_id,
        skills=skills_text,
    )