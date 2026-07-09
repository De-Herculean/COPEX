import pandas as pd
import streamlit as st

st.set_page_config(page_title="Scenario Compare", page_icon="🔄", layout="wide")
st.title("🔄 Scenario Compare")
st.caption(
    "Save the current plan as a named scenario, then compare it against another — useful for "
    "'what changed after I edited capacity/cost/demand' during a live review."
)

results = st.session_state.get("results")
scenarios = st.session_state.setdefault("scenarios", {})

st.subheader("Save current plan as a scenario")
c1, c2 = st.columns([3, 1])
with c1:
    name = st.text_input("Scenario name", placeholder="e.g. Baseline, -20% AHM capacity, +10% demand")
with c2:
    st.write("")
    st.write("")
    save_clicked = st.button("💾 Save current plan", use_container_width=True, disabled=results is None)

if save_clicked:
    if not name:
        st.error("Give the scenario a name first.")
    elif results is None:
        st.error("Run a plan on the Home page first.")
    else:
        scenarios[name] = results
        st.success(f"Saved scenario '{name}'. You now have {len(scenarios)} saved scenario(s).")

if not scenarios:
    st.info("No scenarios saved yet. Run a plan, save it here, tweak the Data Inputs, run again, and save a second one to compare.")
    st.stop()

st.divider()
st.subheader("Compare two scenarios")

names = list(scenarios.keys())
c1, c2 = st.columns(2)
with c1:
    left_name = st.selectbox("Scenario A", names, index=0)
with c2:
    right_name = st.selectbox("Scenario B", names, index=min(1, len(names) - 1))

left = scenarios[left_name]
right = scenarios[right_name]


def _total_cost(res):
    cs = res.cost_summary
    if cs.empty:
        return float("nan")
    row = cs[cs["Cost Component"] == "Total Cost"]
    return float(row["Amount (INR)"].iloc[0]) if not row.empty else float("nan")


m1, m2, m3, m4 = st.columns(4)
m1.metric(f"{left_name}: Total Cost", f"₹{_total_cost(left):,.0f}")
m2.metric(f"{right_name}: Total Cost", f"₹{_total_cost(right):,.0f}",
          delta=f"₹{_total_cost(right) - _total_cost(left):,.0f}", delta_color="inverse")
m3.metric(f"{left_name}: Fill Rate", f"{left.fill_rate:.2%}")
m4.metric(f"{right_name}: Fill Rate", f"{right.fill_rate:.2%}",
          delta=f"{(right.fill_rate - left.fill_rate) * 100:.2f} pp")

m5, m6 = st.columns(2)
m5.metric(f"{left_name}: Hub Service Level", f"{left.hub_service_level:.0%}")
m6.metric(f"{right_name}: Hub Service Level", f"{right.hub_service_level:.0%}")

st.divider()
st.subheader("Cost component comparison")

if left_name == right_name and len(names) > 1:
    st.info("Scenario A and B are the same — pick two different saved scenarios to see a diff.")

l_cs = left.cost_summary.set_index("Cost Component")["Amount (INR)"]
r_cs = right.cost_summary.set_index("Cost Component")["Amount (INR)"]
combined = pd.DataFrame({"__A__": l_cs, "__B__": r_cs})
combined["Δ"] = combined["__B__"] - combined["__A__"]
combined = combined.rename(columns={"__A__": left_name, "__B__": right_name})
# If both selections are the same scenario, left_name == right_name would collide again --
# disambiguate the display labels in that case only.
if left_name == right_name:
    combined.columns = [f"{left_name} (A)", f"{right_name} (B)", "Δ"]

st.dataframe(
    combined.style.format("₹{:,.0f}"),
    use_container_width=True,
)
st.bar_chart(combined.iloc[:, [0, 1]])

st.divider()
st.subheader("Unmet demand comparison")
l_unmet = left.total_unmet_kl
r_unmet = right.total_unmet_kl
c1, c2 = st.columns(2)
c1.metric(f"{left_name}: Unmet (kL)", f"{l_unmet:,.1f}")
c2.metric(f"{right_name}: Unmet (kL)", f"{r_unmet:,.1f}", delta=f"{r_unmet - l_unmet:,.1f}", delta_color="inverse")

if scenarios:
    st.divider()
    if st.button("🗑 Clear all saved scenarios"):
        st.session_state["scenarios"] = {}
        st.rerun()
