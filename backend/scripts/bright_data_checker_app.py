"""Streamlit UI for checking a Bright Data API key against Airbnb imports.

Run (from backend/):
    pip install streamlit httpx    # dev-only, not in requirements.txt
    streamlit run scripts/bright_data_checker_app.py

Same checks as scripts/check_bright_data.py. "Run diagnostics" is free
(read-only calls, no job started); "Import URLs" starts a real (billed)
scrape for the pasted listings.
"""

import importlib
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_bright_data as bd  # noqa: E402

# Streamlit re-executes this file on every rerun but keeps imported modules
# cached, so edits to check_bright_data.py wouldn't show up without this.
bd = importlib.reload(bd)

st.set_page_config(page_title="Bright Data key checker", page_icon="🔑")
st.title("Bright Data key checker")
st.caption(f"Airbnb dataset `{bd.DATASET_ID}`")

key = st.text_input("API key", type="password", help="Paste the key from Railway or backend/.env").strip()
if key:
    st.caption(f"Key: `{bd.mask(key)}`")

st.subheader("1. Diagnose key & account (free)")
if st.button("Run diagnostics", disabled=not key):
    with bd.make_client(key) as client, st.spinner("Checking..."):
        results = bd.diagnose(client)

    failed = [r for r in results if r["status"] == "fail"]
    if failed:
        st.error("Imports will fail: " + "; ".join(f"**{r['check']}** -- {r['summary']}" for r in failed))
    else:
        st.success("Key authenticates and the account is active -- imports should work.")

    icons = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    for r in results:
        with st.expander(f"{icons[r['status']]} {r['check']}: {r['summary']}", expanded=r["status"] == "fail"):
            if r["hint"]:
                (st.error if r["status"] == "fail" else st.info)(r["hint"])
            if r["http"]:
                st.code(r["http"], language=None)
            if r["body"]:
                st.code(r["body"], language="json")

st.subheader("2. Import URLs (billed, a few cents per listing)")
raw_urls = st.text_area("Airbnb listing URLs, one per line", height=120)
lines = [line.strip() for line in raw_urls.splitlines() if line.strip()]

if lines:
    rows = [{"pasted": line, "sent to Bright Data": bd.normalize_url(line) or "-- rejected: no /rooms/<id>"} for line in lines]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

valid_urls = list(dict.fromkeys(u for u in (bd.normalize_url(line) for line in lines) if u))
wait = st.checkbox("Wait for results (polls up to 5 minutes)", value=True)

if st.button(f"Import {len(valid_urls)} URL(s)", disabled=not (key and valid_urls), type="primary"):
    with bd.make_client(key) as client:
        snapshot_id, message = bd.start_scrape(client, valid_urls)
        if not snapshot_id:
            st.error(message)
            st.caption("Run diagnostics above for the account status behind this error.")
            st.stop()
        st.success(message)
        if not wait:
            st.stop()

        status_box = st.empty()
        deadline = time.monotonic() + 300
        started = time.monotonic()
        status = "running"
        while time.monotonic() < deadline:
            status = bd.get_status(client, snapshot_id)
            status_box.info(f"Status: **{status}** ({int(time.monotonic() - started)}s)")
            if status != "running":
                break
            time.sleep(5)

        if status == "failed":
            status_box.error("Scrape failed on Bright Data's side.")
            st.stop()
        if status != "ready":
            status_box.warning(f"Still running after 5 minutes. Snapshot id: `{snapshot_id}`")
            st.stop()

        records = bd.get_records(client, snapshot_id)

    status_box.success("Scrape finished")
    if not isinstance(records, list) or not records:
        st.error("No records returned")
        st.json(records)
        st.stop()

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "name": r.get("name"),
                    "price": r.get("price"),
                    "location": r.get("location"),
                    "url": r.get("url"),
                    "error": r.get("error"),
                }
                for r in records
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    with st.expander("Raw records"):
        st.json(records)
