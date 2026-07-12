
"""
api_server.py
--------------
A small, real HTTP API that simulates the webhook a Google Form (via Apps
Script) would call on submission. This runs as its OWN process, separate
from Streamlit, since Streamlit apps don't natively expose arbitrary HTTP
routes.
 
Run in its own terminal (separately from `streamlit run app.py`):
    python api_server.py
 
It listens on http://localhost:5001/webhook/employee
 
Example test, from another terminal:
    curl -X POST http://localhost:5001/webhook/employee \
      -H "Content-Type: application/json" \
      -d '{"emp_name":"John Doe","emp_no":"E1001","phone_number":"9876543210","department":"Welding","working_area":"Bay 3","joining_date":"2026-07-11"}'
 
Both this server and app.py point at the same SQLite file
(department_system.db), so anything inserted here shows up immediately
in the Streamlit dashboard.
"""
 
from flask import Flask, jsonify, request
 
from database import handle_webhook_employee, init_db
 
app = Flask(__name__)
init_db()  # ensure tables exist even if this is started before app.py
 
 
@app.route("/webhook/employee", methods=["POST"])
def webhook_employee():
    """
    Accepts the JSON packet a Google Form webhook would send, e.g.:
      {
        "emp_name": "John Doe",
        "emp_no": "E1001",
        "phone_number": "9876543210",
        "department": "Welding",
        "working_area": "Bay 3",
        "joining_date": "2026-07-11"
      }
    Delegates all validation / dept_id lookup / insertion to
    handle_webhook_employee() in database.py, so the logic is identical to
    the in-app Streamlit simulator.
    """
    data = request.get_json(force=True, silent=True)
    if data is None:
        return jsonify({"success": False, "message": "Invalid or missing JSON body"}), 400
 
    success, message = handle_webhook_employee(data)
    return jsonify({"success": success, "message": message}), (201 if success else 400)
 
 
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "API server running", "endpoint": "/webhook/employee (POST)"}), 200
 
 
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
 