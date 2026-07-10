"""
Levisol Supply Chain Planning Tool — navigation entry point.

Menu labels and icons are set here via st.Page(title=..., icon=...) rather
than encoded into filenames (the previous "1_📊_Data_Inputs.py" pattern).
Emoji in filenames can get mangled by zip/OS encoding on some systems
(e.g. extracting on Windows without UTF-8 filename support) -- since the
label now lives in Python source, it always renders correctly regardless
of the OS or how the folder was unzipped.
"""

import streamlit as st

st.set_page_config(
    page_title="Levisol Supply Chain Planner",
    page_icon="📦",
    layout="wide",
)

pages = [
    st.Page("pages/home.py", title="Home", icon="🏠", default=True),
    st.Page("pages/data_inputs.py", title="Data Inputs", icon="📊"),
    st.Page("pages/inventory_norms.py", title="Inventory Norms", icon="📦"),
    st.Page("pages/production_plan.py", title="Production Plan", icon="🏭"),
    st.Page("pages/routing_map.py", title="Routing & Map", icon="🗺️"),
    st.Page("pages/cost_summary.py", title="Cost Summary", icon="💰"),
    st.Page("pages/scenario_compare.py", title="Scenario Compare", icon="🔄"),
]

nav = st.navigation(pages)
nav.run()
