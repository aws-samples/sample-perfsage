"""
PerfSage TestGen — Demo UI
A simple Streamlit app to demonstrate the TestGen agent.

Usage:
    streamlit run demo_ui.py

Requires:
    - AWS credentials exported (Isengard)
    - streamlit installed (pip install streamlit)
"""
import json
import time
import streamlit as st
import boto3
from botocore.config import Config

st.set_page_config(page_title="PerfSage TestGen", page_icon="🔥", layout="wide")

st.title("🔥 PerfSage — AI-Powered Load Test Generator")
st.markdown("**Transform API specs + natural language into executable k6 load test scripts**")
st.divider()

# --- Sidebar: AWS Config ---
with st.sidebar:
    st.header("⚙️ Configuration")
    region = st.text_input("AWS Region", value="us-east-1")
    function_name = st.text_input("Lambda Function", value="perfsage-testgen-dev")
    st.divider()
    st.markdown("**How it works:**")
    st.markdown("""
    1. Upload/paste your OpenAPI spec
    2. Describe what you want to test
    3. Click Generate
    4. Get an executable k6 script
    """)

# --- Main: Two columns ---
col1, col2 = st.columns([1, 1])

with col1:
    st.header("📥 Input")

    # Spec input
    spec_input_method = st.radio("API Spec input method:", ["Paste", "Upload file", "Use example"])

    spec_content = ""
    spec_format = "yaml"

    if spec_input_method == "Paste":
        spec_content = st.text_area(
            "Paste your OpenAPI/Swagger spec (YAML or JSON):",
            height=300,
            placeholder="openapi: '3.0.3'\ninfo:\n  title: My API\n  version: '1.0'\npaths:\n  /users:\n    get:\n      ..."
        )
        if spec_content.strip().startswith("{"):
            spec_format = "json"

    elif spec_input_method == "Upload file":
        uploaded = st.file_uploader("Upload spec file", type=["yaml", "yml", "json"])
        if uploaded:
            spec_content = uploaded.read().decode("utf-8")
            spec_format = "json" if uploaded.name.endswith(".json") else "yaml"
            st.success(f"Loaded: {uploaded.name} ({len(spec_content)} chars)")

    elif spec_input_method == "Use example":
        example = st.selectbox("Choose example spec:", [
            "HR System (company → department → employee, 3-level hierarchy)",
            "DummyJSON (products, search, auth, carts)",
            "Petstore (Swagger 2.0, CRUD)",
            "E-Commerce (Bearer auth, checkout flow)",
        ])
        example_files = {
            "HR System (company → department → employee, 3-level hierarchy)": "tests/fixtures/complex/hr_system.yaml",
            "DummyJSON (products, search, auth, carts)": "tests/fixtures/simple/dummyjson.yaml",
            "Petstore (Swagger 2.0, CRUD)": "tests/fixtures/complex/petstore_swagger2.json",
            "E-Commerce (Bearer auth, checkout flow)": "tests/fixtures/medium/ecommerce_api.yaml",
        }
        spec_path = example_files[example]
        with open(spec_path) as f:
            spec_content = f.read()
        spec_format = "json" if spec_path.endswith(".json") else "yaml"
        st.info(f"Using: {spec_path} ({len(spec_content)} chars)")

    st.divider()

    # NL prompt
    st.subheader("💬 What do you want to test?")
    user_prompt = st.text_area(
        "Describe your load test in plain English:",
        height=100,
        placeholder="e.g., Stress test with 200 users ramping over 3 minutes. Include edge cases for invalid auth and large payloads."
    )

    st.divider()

    # Resource Dependencies (required)
    st.subheader("🔗 Resource Dependencies")
    st.caption("Define how resources depend on each other (e.g., employee needs department_id)")

    num_deps = st.number_input("Number of dependencies", min_value=0, max_value=10, value=0)
    dependencies = []
    for i in range(num_deps):
        cols = st.columns(3)
        with cols[0]:
            parent = st.text_input(f"Parent resource #{i+1}", key=f"parent_{i}", placeholder="company")
        with cols[1]:
            child = st.text_input(f"Child resource #{i+1}", key=f"child_{i}", placeholder="department")
        with cols[2]:
            via = st.text_input(f"Foreign key field #{i+1}", key=f"via_{i}", placeholder="company_id")
        if parent and child and via:
            dependencies.append({"parent": parent, "child": child, "via": via})

    if not dependencies:
        st.info("No dependencies provided. Resources will be tested independently.")

    st.divider()

    # Records (required)
    st.subheader("📊 Number of Records")
    st.caption("How many records to create for each resource during test setup")

    records_text = st.text_area(
        "Records per resource (one per line: resource_name: count)",
        height=100,
        placeholder="company: 10\ndepartment: 50\nemployee: 500"
    )
    records = {}
    if records_text.strip():
        for line in records_text.strip().split("\n"):
            if ":" in line:
                parts = line.split(":", 1)
                name = parts[0].strip()
                try:
                    count = int(parts[1].strip())
                    records[name] = count
                except ValueError:
                    pass

    st.divider()

    # Context (required)
    st.subheader("📝 Resource Context")
    st.caption("Describe your resources and domain — helps generate meaningful realistic data")
    context = st.text_area(
        "Business context:",
        height=100,
        placeholder="Enterprise HR system. Companies are large corporations. Departments are divisions like Engineering, Sales, HR. Employees have roles like Senior Engineer, Product Manager."
    )

    # Parameters (optional)
    with st.expander("Advanced parameters (optional)"):
        col_a, col_b = st.columns(2)
        with col_a:
            vus = st.number_input("Virtual Users", min_value=1, max_value=10000, value=50)
        with col_b:
            duration = st.text_input("Duration", value="5m")

        if vus or duration:
            if user_prompt and f"{vus} users" not in user_prompt and f"{duration}" not in user_prompt:
                user_prompt += f" Use {vus} users for {duration}."

    # Generate button
    st.divider()
    generate_clicked = st.button("🚀 Generate Load Test", type="primary", use_container_width=True)

# --- Right column: Output ---
with col2:
    st.header("📤 Output")

    if generate_clicked:
        if not spec_content:
            st.error("Please provide an API spec (paste, upload, or use example)")
        elif not user_prompt:
            st.error("Please describe what you want to test")
        elif not context:
            st.error("Please provide resource context (describe your resources and domain)")
        elif not records:
            st.error("Please provide number of records (e.g., company: 10)")
        else:
            with st.spinner("🧠 AI agent is generating your k6 script... (30-200 seconds)"):
                try:
                    lambda_client = boto3.client(
                        "lambda",
                        region_name=region,
                        config=Config(read_timeout=300, retries={"max_attempts": 1})
                    )

                    payload = json.dumps({
                        "body": json.dumps({
                            "spec": spec_content,
                            "prompt": user_prompt,
                            "format": spec_format,
                            "dependencies": dependencies,
                            "records": records,
                            "context": context,
                        })
                    })

                    start_time = time.time()
                    response = lambda_client.invoke(
                        FunctionName=function_name,
                        Payload=payload.encode(),
                    )
                    elapsed = time.time() - start_time

                    result = json.loads(response["Payload"].read())
                    body = json.loads(result.get("body", "{}"))

                    if result.get("statusCode") == 200 and "script" in body:
                        script = body["script"]
                        config = body.get("config", {})
                        hierarchy = body.get("hierarchy", {})
                        disclaimer = body.get("disclaimer")

                        st.success(f"✅ Generated in {elapsed:.1f}s — {len(script.splitlines())} lines")

                        if disclaimer:
                            st.warning(f"⚠️ {disclaimer}")

                        # Config summary
                        if config:
                            st.subheader("📊 Test Configuration")
                            config_cols = st.columns(3)
                            with config_cols[0]:
                                st.metric("Test Type", config.get("test_type", "—"))
                            with config_cols[1]:
                                st.metric("Executor", config.get("executor", {}).get("type", "—"))
                            with config_cols[2]:
                                st.metric("Auth", config.get("auth_type", "none"))

                        # Hierarchy
                        if hierarchy and hierarchy.get("order"):
                            st.subheader("🔗 Resource Hierarchy")
                            st.markdown(f"**Creation order:** {' → '.join(hierarchy['order'])}")
                            st.markdown(f"**Delete order:** {' → '.join(hierarchy.get('delete_order', []))}")
                            if hierarchy.get("records"):
                                st.markdown(f"**Records:** {hierarchy['records']}")

                        # Script
                        st.subheader("📜 Generated k6 Script")
                        st.code(script, language="javascript")

                        # Download button
                        st.download_button(
                            label="⬇️ Download script",
                            data=script,
                            file_name="loadtest.js",
                            mime="text/javascript",
                        )

                        # Run instructions
                        st.subheader("▶️ How to run")
                        st.code("k6 run --vus 5 --iterations 5 loadtest.js", language="bash")

                        # Full config + hierarchy JSON
                        with st.expander("View full config JSON"):
                            st.json(config)
                        with st.expander("View hierarchy JSON (for Executor agent)"):
                            st.json(hierarchy)

                    else:
                        error = body.get("error", "Unknown error")
                        st.error(f"❌ Agent error: {error}")

                except Exception as e:
                    st.error(f"❌ Failed: {str(e)}")
                    st.info("Make sure your AWS credentials are exported in the terminal where you ran `streamlit run demo_ui.py`")

    else:
        st.info("👈 Fill in the spec and prompt on the left, then click **Generate**")
        st.markdown("""
        **The agent will:**
        1. Parse your OpenAPI spec (endpoints, auth, schemas)
        2. Interpret your natural language request
        3. Generate a production-quality k6 script
        4. Include edge cases (large payloads, timeouts, invalid auth)
        5. Validate the script before returning

        **Output includes:**
        - Executable k6 JavaScript script
        - Test configuration JSON (executor, VUs, thresholds)
        """)
