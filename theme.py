"""
theme.py
--------
Custom visual identity for the Department Management System.
 
Direction: this is a factory-floor control tool for department heads
(Welding, Assembly, Logistics...), not a generic SaaS dashboard -- so the
look leans "industrial control panel" rather than default Streamlit white:
steel-navy as the primary color, a single safety-amber accent (used
sparingly, like hazard signage), condensed headers, and monospace for
employee IDs / data tables so they read like a readout.
 
Usage: call apply_theme() once near the top of app.py, right after
st.set_page_config().
"""
 
import streamlit as st
 
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
 
:root {
    --dms-navy: #1B4B66;
    --dms-navy-dark: #123449;
    --dms-amber: #E8A33D;
    --dms-bg: #F4F5F7;
    --dms-surface: #FFFFFF;
    --dms-border: #D8DCE1;
    --dms-text: #1A1D23;
    --dms-muted: #6B7280;
    --dms-success: #2F855A;
    --dms-danger: #B3261E;
}
 
/* ---- base type ---- */
html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}
.stApp {
    background-color: var(--dms-bg);
}
h1, h2, h3 {
    font-family: 'Oswald', sans-serif !important;
    letter-spacing: 0.02em;
    text-transform: uppercase;
    color: var(--dms-navy-dark);
}
h1 {
    border-bottom: 4px solid var(--dms-amber);
    padding-bottom: 0.4rem;
    display: inline-block;
}
 
/* ---- eyebrow label (used above the login title) ---- */
.dms-eyebrow {
    font-family: 'Oswald', sans-serif;
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--dms-amber);
    margin-bottom: 0.25rem;
}
 
/* ---- sidebar: styled as a dark control panel ---- */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, var(--dms-navy) 0%, var(--dms-navy-dark) 100%);
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span {
    color: #F4F5F7 !important;
    border-bottom: none;
}
section[data-testid="stSidebar"] .stButton > button {
    background-color: var(--dms-amber);
    color: var(--dms-navy-dark);
    width: 100%;
    font-family: 'Oswald', sans-serif;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    font-weight: 600;
    border: none;
}
section[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #ffffff;
    color: var(--dms-navy-dark);
}
 
/* ---- buttons (main area) ---- */
.stButton > button, .stFormSubmitButton > button {
    background-color: var(--dms-navy);
    color: #ffffff;
    border: none;
    border-radius: 3px;
    font-family: 'Oswald', sans-serif;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    font-weight: 600;
    font-size: 0.85rem;
    padding: 0.5rem 1.25rem;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
    background-color: var(--dms-amber);
    color: var(--dms-navy-dark);
}
 
/* ---- forms rendered as cards ---- */
[data-testid="stForm"] {
    background-color: var(--dms-surface);
    border: 1px solid var(--dms-border);
    border-top: 4px solid var(--dms-amber);
    border-radius: 4px;
    padding: 1.5rem;
}
 
/* ---- data tables: monospace, like a readout ---- */
[data-testid="stDataFrame"] {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
}
 
/* ---- KPI readout cards (dashboard) ---- */
.dms-kpi-row {
    display: flex;
    gap: 1rem;
    margin-bottom: 1.5rem;
    flex-wrap: wrap;
}
.dms-kpi-card {
    flex: 1;
    min-width: 150px;
    background-color: var(--dms-surface);
    border: 1px solid var(--dms-border);
    border-top: 4px solid var(--dms-amber);
    border-radius: 4px;
    padding: 1rem 1.25rem;
}
.dms-kpi-card.dms-working { border-top-color: var(--dms-success); }
.dms-kpi-card.dms-not-working { border-top-color: var(--dms-danger); }
.dms-kpi-label {
    font-family: 'Oswald', sans-serif;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--dms-muted);
    margin-bottom: 0.3rem;
}
.dms-kpi-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2rem;
    font-weight: 500;
    color: var(--dms-navy-dark);
    line-height: 1;
}
</style>
"""
 
 
def apply_theme():
    """Inject the custom CSS once. Call right after st.set_page_config()."""
    st.markdown(CSS, unsafe_allow_html=True)
 
 
def eyebrow(text: str):
    """Small uppercase amber label used above section titles, e.g. on the login page."""
    st.markdown(f'<div class="dms-eyebrow">{text}</div>', unsafe_allow_html=True)
 
 
def render_kpi_cards(stats: dict):
    """
    Render the four dashboard readout cards (Total / Working / Not Working /
    Departments) as styled HTML rather than st.metric, so they follow the
    theme's card/typography tokens exactly.
    """
    html = f"""
    <div class="dms-kpi-row">
        <div class="dms-kpi-card">
            <div class="dms-kpi-label">Total Employees</div>
            <div class="dms-kpi-value">{stats['total']}</div>
        </div>
        <div class="dms-kpi-card dms-working">
            <div class="dms-kpi-label">Working</div>
            <div class="dms-kpi-value">{stats['working']}</div>
        </div>
        <div class="dms-kpi-card dms-not-working">
            <div class="dms-kpi-label">Not Working</div>
            <div class="dms-kpi-value">{stats['not_working']}</div>
        </div>
        <div class="dms-kpi-card">
            <div class="dms-kpi-label">Departments</div>
            <div class="dms-kpi-value">{stats['departments']}</div>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
 