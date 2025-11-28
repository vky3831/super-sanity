
import streamlit as st
import requests, uuid, json, time
from pymongo import MongoClient
from bson import ObjectId
import rstr
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Config ---
import os
# Resolve MONGO_URI: prefer environment variable; if not set, try Streamlit secrets if available; else default.
MONGO_URI = os.environ.get("MONGO_URI")
if not MONGO_URI:
    try:
        # Accessing st.secrets may raise if no secrets file exists; handle gracefully.
        MONGO_URI = st.secrets.get("MONGO_URI")
    except Exception:
        MONGO_URI = None
if not MONGO_URI:
    MONGO_URI = "mongodb://mongo:27017"

DB_NAME = "supersanity"
MAX_PARALLEL_DEFAULT = 5

# --- DB ---
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
flows_col = db.flows
jobs_col = db.jobs
runs_col = db.runs

st.set_page_config(page_title="SuperSanity", layout="wide")

# --- Helpers ---
def generate_value(rule, context):
    typ = rule.get("type")
    if typ == "static":
        return rule.get("value")
    if typ == "auto_uuid":
        return str(uuid.uuid4())
    if typ == "regex":
        pattern = rule.get("pattern", ".+")
        try:
            return rstr.xeger(pattern)
        except Exception:
            return pattern
    if typ == "choice":
        choices = rule.get("choices", [])
        return choices[0] if choices else None
    if typ == "map":
        # simple dotted path from context, e.g., "flow0.api0.response.data.id"
        path = rule.get("path", "")
        parts = path.split(".")
        cur = context
        try:
            for p in parts:
                if isinstance(cur, list):
                    p = int(p)
                cur = cur[p]
            return cur
        except Exception:
            return None
    return None

def build_payload(field_rules, context):
    payload = {}
    for field, rule in field_rules.items():
        payload[field] = generate_value(rule, context)
    return payload

def run_flow(flow, run_id):
    """
    flow: {
      "name": str,
      "steps": [ { "name": str, "method": "GET"/"POST", "url":..., "headers": {...}, "body_rules": {...}, "expected_status": int } ]
    }
    """
    results = []
    context = {"flows": {}, "last_response": None}
    for i, step in enumerate(flow.get("steps", [])):
        # prepare payload
        payload = None
        if step.get("body_rules"):
            payload = build_payload(step["body_rules"], context)
        method = step.get("method","GET").upper()
        url = step.get("url")
        headers = step.get("headers") or {}
        try:
            if method == "GET":
                resp = requests.get(url, headers=headers, params=payload, timeout=30)
            else:
                resp = requests.request(method, url, headers=headers, json=payload, timeout=30)
            status_ok = (resp.status_code == step.get("expected_status", 200))
            body = resp.text
            # try parse json
            try:
                body_json = resp.json()
            except Exception:
                body_json = None
            # save in context for mapping
            # --------------------------
            # 1) NEW: Save last_request
            # --------------------------
            context["last_request"] = {
                "method": method,
                "url": url,
                "headers": headers,
                "payload": payload
            }
            # --------------------------
            # Save last_response (existing)
            # --------------------------
            context["last_response"] = {
                "status": resp.status_code,
                "text": body,
                "json": body_json
            }
            # --------------------------
            # 2 & 3) NEW: Save flows[flowName][stepName]
            # make flow a DICTIONARY instead of list
            # --------------------------
            flow_name = flow.get("name", "flow")
            step_name = step.get("name")

            if flow_name not in context["flows"]:
                context["flows"][flow_name] = {}

            # Create dictionary for step
            context["flows"][flow_name][step_name] = {
                "request": context["last_request"],
                "response": context["last_response"]
            }
            
            results.append({
                "step": step.get("name"),
                "method": method,
                "url": url,
                "request_headers": headers,
                "request_payload": payload,
                "status_code": resp.status_code,
                "status_ok": status_ok,
                "response_text": body,
                "response_json": body_json
            })
        except Exception as e:
            results.append({
                "step": step.get("name"),
                "method": method,
                "url": url,
                "request_headers": headers,
                "request_payload": payload,
                "status_code": None,
                "status_ok": False,
                "error": str(e)
            })
    # persist run-level result for this flow
    runs_col.insert_one({
        "run_id": run_id,
        "flow_name": flow.get("name"),
        "timestamp": time.time(),
        "results": results
    })
    return results

def run_job(job, max_parallel):
    """
    job: { name, flow_ids: [flow_doc_id...], _id }
    """
    job_id = str(job.get("_id"))
    flows = [flows_col.find_one({"_id": ObjectId(f_id)}) for f_id in job.get("flow_ids", [])]
    results_summary = {"job_id": job_id, "flows": []}
    # run flows in parallel (ThreadPool)
    with ThreadPoolExecutor(max_workers=max_parallel) as exc:
        futures = {exc.submit(run_flow, flow, job_id): flow for flow in flows if flow}
        for fut in as_completed(futures):
            flow = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = [{"error": str(e)}]
            results_summary["flows"].append({"flow_name": flow.get("name"), "results": res})
    # store job record
    jobs_col.update_one({"_id": job.get("_id")}, {"$set": {"last_run": results_summary, "last_run_ts": time.time()}}, upsert=False)
    return results_summary

# --- UI ---
st.title("SuperSanity — Integration Tester")

menu = st.sidebar.selectbox(
    "Menu",
    [
        "Create Flow",
        "View & Manage Flows",
        "Create Job",
        "Manage Jobs",
        "Run Job",
        "View Jobs & Reports",
        "Settings"
    ]
)


if "max_parallel" not in st.session_state:
    st.session_state["max_parallel"] = MAX_PARALLEL_DEFAULT

if menu == "Settings":
    st.header("Settings")
    st.session_state["max_parallel"] = st.number_input("Max parallel flows", min_value=1, max_value=20, value=st.session_state["max_parallel"], step=1)
    st.write("Mongo URI:", MONGO_URI)

# ----- Editing Flow Mode ------
editing_flow_id = st.session_state.get("edit_flow")

if editing_flow_id:
    flow_doc = flows_col.find_one({"_id": ObjectId(editing_flow_id)})

    st.header(f"Editing Flow: {flow_doc['name']}")

    flow_name = st.text_input("Flow name", value=flow_doc["name"])

    steps_docs = flow_doc["steps"]
    steps = []

    for i, step in enumerate(steps_docs):
        st.subheader(f"Step {i+1}")
        name = st.text_input(f"Step {i+1} name", value=step["name"], key=f"edit_name_{i}")
        method = st.selectbox(f"Method {i+1}", ["GET","POST","PUT","DELETE"], index=["GET","POST","PUT","DELETE"].index(step["method"]), key=f"edit_method_{i}")
        url = st.text_input(f"URL {i+1}", value=step["url"], key=f"edit_url_{i}")
        expected = st.number_input(
            f"Expected status {i+1}",
            value=step.get("expected_status", 200),
            key=f"edit_exp_{i}"
        )
        headers_text = st.text_area(
            f"Headers JSON {i+1}",
            value=json.dumps(step.get("headers", {}), indent=2),
            key=f"edit_hdr_{i}"
        )
        body_rules_text = st.text_area(
            f"Body rules JSON {i+1}",
            value=json.dumps(step.get("body_rules", {}), indent=2),
            key=f"edit_body_{i}"
        )

        steps.append({
            "name": name,
            "method": method,
            "url": url,
            "expected_status": expected,
            "headers_text": headers_text,
            "body_rules_text": body_rules_text
        })

    if st.button("Update Flow"):
        updated_steps = []
        for s in steps:
            updated_steps.append({
                "name": s["name"],
                "method": s["method"],
                "url": s["url"],
                "expected_status": s["expected_status"],
                "headers": json.loads(s["headers_text"] or "{}"),
                "body_rules": json.loads(s["body_rules_text"] or "{}")
            })

        flows_col.update_one(
            {"_id": ObjectId(editing_flow_id)},
            {"$set": {"name": flow_name, "steps": updated_steps}}
        )

        st.success("Flow updated successfully")
        del st.session_state["edit_flow"]
        st.rerun()

    st.stop()  # Prevent Create Flow screen from loading


if menu == "Create Flow":
    st.header("Create a Flow (sequence of API calls)")
    flow_name = st.text_input("Flow name")
    num_steps = st.number_input("Number of steps", min_value=1, max_value=10, value=1, step=1)
    steps = []
    for i in range(num_steps):
        st.subheader(f"Step {i+1}")
        name = st.text_input(f"Step {i+1} name", key=f"name_{i}")
        method = st.selectbox(f"Method {i+1}", ["GET","POST","PUT","DELETE"], key=f"method_{i}")
        url = st.text_input(f"URL {i+1}", key=f"url_{i}")
        expected = st.number_input(f"Expected status {i+1}", value=200, key=f"exp_{i}")
        headers_text = st.text_area(f"Headers JSON {i+1} (e.g. {{\"Authorization\":\"Bearer ...\"}})", key=f"hdr_{i}", height=100)
        # body rules: user provides a JSON structure where leafs are rule objects
        st.markdown("**Body field rules** — provide as JSON mapping field -> rule. Rule types: static, auto_uuid, regex (pattern), choice (list), map (path). Example:\n\n```\n{\"username\": {\"type\":\"regex\",\"pattern\":\"[a-z]{5}\"}, \"id\": {\"type\":\"auto_uuid\"}}\n```")
        body_rules_text = st.text_area(f"Body rules JSON {i+1}", key=f"body_{i}", height=150)
        steps.append({
            "name": name or f"step{i+1}",
            "method": method,
            "url": url,
            "expected_status": int(expected),
            "headers_text": headers_text,
            "body_rules_text": body_rules_text
        })
    if st.button("Save Flow"):
        if not flow_name:
            st.error("Provide flow name")
        else:
            # sanitize parse
            steps_docs = []
            for s in steps:
                headers = {}
                try:
                    if s["headers_text"]:
                        headers = json.loads(s["headers_text"])
                except Exception as e:
                    st.error(f"Invalid headers JSON: {e}")
                    headers = {}
                body_rules = {}
                try:
                    if s["body_rules_text"]:
                        body_rules = json.loads(s["body_rules_text"])
                except Exception as e:
                    st.error(f"Invalid body rules JSON: {e}")
                    body_rules = {}
                steps_docs.append({
                    "name": s["name"],
                    "method": s["method"],
                    "url": s["url"],
                    "expected_status": s["expected_status"],
                    "headers": headers,
                    "body_rules": body_rules
                })
            flows_col.insert_one({
                "name": flow_name,
                "steps": steps_docs,
                "created_at": time.time()
            })
            st.success("Flow saved")

if menu == "View & Manage Flows":
    st.header("All Flows")

    flow_docs = list(flows_col.find())

    for f in flow_docs:
        with st.expander(f"Flow: {f['name']}  |  ID: {f['_id']}"):
            
            st.json(f)  # Full details

            col1, col2 = st.columns(2)
            with col1:
                if st.button(f"Edit Flow {f['_id']}", key=f"edit_flow_{f['_id']}"):
                    st.session_state["edit_flow"] = str(f["_id"])
                    st.rerun()

            with col2:
                if st.button(f"Delete Flow {f['_id']}", key=f"delete_flow_{f['_id']}"):
                    flows_col.delete_one({"_id": f["_id"]})
                    st.success("Flow deleted")
                    st.rerun()

if menu == "Create Job":
    st.header("Create Job (collection of flows)")
    job_name = st.text_input("Job name")
    # list flows
    flow_docs = list(flows_col.find())
    flow_map = {str(f["_id"]): f["name"] for f in flow_docs}
    selected = st.multiselect("Select flows (job must have at least 1)", options=list(flow_map.keys()), format_func=lambda x: flow_map.get(x, x))
    if st.button("Save Job"):
        if not job_name:
            st.error("job name required")
        elif not selected:
            st.error("select at least one flow")
        else:
            jobs_col.insert_one({
                "name": job_name,
                "flow_ids": [f for f in selected],
                "created_at": time.time()
            })
            st.success("Job created")

if menu == "Manage Jobs":
    st.header("All Jobs")

    job_docs = list(jobs_col.find())
    flow_docs = list(flows_col.find())
    flow_map = {str(f["_id"]): f["name"] for f in flow_docs}

    for j in job_docs:
        with st.expander(f"Job: {j['name']} | ID: {j['_id']}"):
            st.write("Current Flows:", j["flow_ids"])

            new_name = st.text_input(f"Job Name ({j['_id']})", value=j["name"], key=f"job_name_{j['_id']}")

            new_flows = st.multiselect(
                f"Select Flows for Job {j['_id']}",
                options=list(flow_map.keys()),
                default=[str(fid) for fid in j["flow_ids"]],
                format_func=lambda x: flow_map.get(x, x),
                key=f"job_flows_{j['_id']}"
            )

            col1, col2 = st.columns(2)
            with col1:
                if st.button(f"Update Job {j['_id']}", key=f"update_job_{j['_id']}"):
                    jobs_col.update_one(
                        {"_id": j["_id"]},
                        {"$set": {"name": new_name, "flow_ids": new_flows}}
                    )
                    st.success("Job updated")
                    st.rerun()

            with col2:
                if st.button(f"Delete Job {j['_id']}", key=f"delete_job_{j['_id']}"):
                    jobs_col.delete_one({"_id": j["_id"]})
                    st.success("Job deleted")
                    st.rerun()


if menu == "Run Job":
    st.header("Run Job")
    jobs = list(jobs_col.find())
    job_map = {str(j["_id"]): j["name"] for j in jobs}
    sel = st.selectbox("Select job to run", options=list(job_map.keys()), format_func=lambda x: job_map.get(x, x))
    max_par = st.number_input("Max parallel flows (override)", min_value=1, max_value=20, value=st.session_state.get("max_parallel", 5))
    if st.button("Start Job"):
        job = jobs_col.find_one({"_id": ObjectId(sel)})
        if not job:
            st.error("Job not found")
        else:
            st.info("Starting job...")
            summary = run_job(job, max_par)
            st.success("Job completed")
            st.json(summary)
            # generate simple HTML report
            html = ["<html><head><meta charset='utf-8'><title>SuperSanity Report</title></head><body>"]
            html.append(f"<h1>Job Report: {job.get('name')}</h1>")
            for f in summary["flows"]:
                html.append(f"<h2>Flow: {f['flow_name']}</h2>")
                for step in f["results"]:
                    html.append(f"<h3>Step: {step.get('step')}</h3>")
                    # New Code Start
                    html.append(f"<p><b>Method:</b> {step.get('method')}</p>")
                    html.append(f"<p><b>URL:</b> {step.get('url')}</p>")
                    html.append(f"<p><b>Status:</b> {step.get('status_code')} - OK: {step.get('status_ok')}</p>")

                    # Request Headers
                    html.append("<h4>Request Headers</h4>")
                    html.append("<pre>" + json.dumps(step.get("request_headers", {}), indent=2) + "</pre>")

                    # Request Payload
                    html.append("<h4>Request Payload</h4>")
                    html.append("<pre>" + json.dumps(step.get("request_payload", {}), indent=2) + "</pre>")

                    # Response Body
                    if step.get("response_text"):
                        html.append("<h4>Response</h4>")
                        html.append("<pre>" + (step.get("response_text")[:5000]) + "</pre>")

                    # Error
                    if step.get("error"):
                        html.append("<h4 style='color:red'>Error</h4>")
                        html.append("<pre style='color:red'>" + str(step.get("error")) + "</pre>")

                    # New Code End
            html.append("</body></html>")
            report_html = "\n".join(html)
            st.download_button("Download HTML report", report_html, file_name=f"report_{job.get('name')}.html", mime="text/html")

if menu == "View Jobs & Reports":
    st.header("Jobs")
    jobs = list(jobs_col.find())
    for j in jobs:
        st.subheader(j.get("name"))
        st.write("Flows:", j.get("flow_ids"))
        last = j.get("last_run")
        if last:
            st.write("Last run at:", time.ctime(j.get("last_run_ts")))
            st.json(last)
            if st.button(f"Download last report for {j.get('name')}", key=f"dl_{j['_id']}"):
                # recreate report
                htmlparts = ["<html><body>"]
                htmlparts.append(f"<h1>Job {j.get('name')}</h1>")
                for f in last["flows"]:
                    htmlparts.append(f"<h2>Flow: {f['flow_name']}</h2>")
                    for step in f["results"]:
                        htmlparts.append(f"<h3>Step: {step.get('step')}</h3>")
                        # New code start
                        htmlparts.append(f"<p><b>Method:</b> {step.get('method')}</p>")
                        htmlparts.append(f"<p><b>URL:</b> {step.get('url')}</p>")
                        htmlparts.append(f"<p><b>Status:</b> {step.get('status_code')}</p>")

                        htmlparts.append("<h4>Request Headers</h4>")
                        htmlparts.append("<pre>" + json.dumps(step.get("request_headers", {}), indent=2) + "</pre>")

                        htmlparts.append("<h4>Request Payload</h4>")
                        htmlparts.append("<pre>" + json.dumps(step.get("request_payload", {}), indent=2) + "</pre>")

                        htmlparts.append("<h4>Response</h4>")
                        htmlparts.append("<pre>" + (step.get("response_text")[:5000] if step.get("response_text") else "") + "</pre>")

                        if step.get("error"):
                            htmlparts.append("<h4 style='color:red'>Error</h4>")
                            htmlparts.append("<pre style='color:red'>" + str(step.get("error")) + "</pre>")

                        # New code end
                htmlparts.append("</body></html>")
                st.download_button("Download", "\n".join(htmlparts), file_name=f"report_{j.get('name')}.html", mime="text/html")



