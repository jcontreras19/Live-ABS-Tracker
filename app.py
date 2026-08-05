"""
THE STRIKE ZONE REPORT — Live ABS Shadow Zone Tracker
A broadcast-style dashboard reporting on MLB's Automated Ball-Strike system,
updated nightly from FFDB.
"""
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go
from datetime import datetime, timedelta

from utils import (
    get_connection, load_status, get_called_pitches,
    get_challenge_summary, get_latest_game_date, PLATE_HALF, SHADOW_ZONE,
    get_challenge_leaderboard, get_hot_umpire_games, get_challenge_of_the_day
)

st.set_page_config(
    page_title="The Strike Zone Report",
    page_icon="⚾",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ── Broadcast-style theming ────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Teko:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

.stApp {
    background-color: #0A1128;
}

/* Ticker bar */
.ticker-bar {
    background: #C8102E;
    color: white;
    font-family: 'Teko', sans-serif;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 2px;
    padding: 8px 20px;
    border-radius: 4px;
    margin-bottom: 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.live-dot {
    height: 10px;
    width: 10px;
    background-color: #39FF6A;
    border-radius: 50%;
    display: inline-block;
    margin-right: 8px;
    box-shadow: 0 0 8px #39FF6A;
}

/* Headline */
.headline {
    font-family: 'Teko', sans-serif;
    font-weight: 700;
    font-size: 64px;
    color: white;
    line-height: 1;
    letter-spacing: 1px;
    margin-bottom: 0px;
}
.subheadline {
    font-family: 'IBM Plex Mono', monospace;
    color: #8C9BAB;
    font-size: 14px;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 24px;
}

/* Scoreboard cards */
.score-card {
    background: #131C3B;
    border: 1px solid #24304F;
    border-radius: 6px;
    padding: 18px 20px;
    text-align: center;
}
.score-label {
    font-family: 'IBM Plex Mono', monospace;
    color: #8C9BAB;
    font-size: 12px;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-bottom: 6px;
}
.score-value {
    font-family: 'Teko', sans-serif;
    font-weight: 700;
    font-size: 52px;
    color: #FFC72C;
    line-height: 1;
}
.score-delta-up { color: #39FF6A; font-family: 'IBM Plex Mono', monospace; font-size: 13px; }
.score-delta-down { color: #C8102E; font-family: 'IBM Plex Mono', monospace; font-size: 13px; }

/* Section divider */
.section-label {
    font-family: 'Teko', sans-serif;
    font-weight: 600;
    font-size: 28px;
    color: white;
    border-left: 5px solid #C8102E;
    padding-left: 12px;
    margin: 30px 0 10px 0;
}

/* Challenge review box */
.challenge-box {
    background: linear-gradient(135deg, #131C3B 0%, #1A2547 100%);
    border: 2px solid #FFC72C;
    border-radius: 8px;
    padding: 20px;
}
.challenge-title {
    font-family: 'Teko', sans-serif;
    font-weight: 700;
    font-size: 24px;
    color: #FFC72C;
    letter-spacing: 2px;
}
</style>
""", unsafe_allow_html=True)

# ── Data loading (cached so repeated interactions don't re-hit the DB) ────────
@st.cache_data(ttl=3600)
def load_data():
    conn = get_connection()
    status = load_status()
    latest_date = get_latest_game_date(conn)

    yesterday = latest_date
    week_start = yesterday - timedelta(days=7) if yesterday else None

    pitches_yesterday = get_called_pitches(conn, start_date=yesterday, end_date=yesterday)
    pitches_week = get_called_pitches(conn, start_date=week_start, end_date=yesterday)
    pitches_season = get_called_pitches(conn, season=yesterday.year if yesterday else 2026)

    challenges_yesterday = get_challenge_summary(conn, start_date=yesterday, end_date=yesterday)
    challenges_season = get_challenge_summary(conn, start_date=f"{yesterday.year}-01-01", end_date=yesterday)

    batter_leaderboard = get_challenge_leaderboard(conn, season_start := f"{yesterday.year}-01-01", yesterday, role='batter')
    pitcher_leaderboard = get_challenge_leaderboard(conn, season_start, yesterday, role='pitcher')
    hot_ump_games = get_hot_umpire_games(conn, week_start, yesterday)
    cotd = get_challenge_of_the_day(conn, yesterday)

    conn.close()
    return {
        "status": status,
        "latest_date": latest_date,
        "pitches_yesterday": pitches_yesterday,
        "pitches_week": pitches_week,
        "pitches_season": pitches_season,
        "challenges_yesterday": challenges_yesterday,
        "challenges_season": challenges_season,
        "batter_leaderboard": batter_leaderboard,
        "pitcher_leaderboard": pitcher_leaderboard,
        "hot_ump_games": hot_ump_games,
        "cotd": cotd,
    }


def shadow_csr(df):
    shadow = df[df['in_shadow']]
    if len(shadow) == 0:
        return None, 0
    return shadow['is_called_strike'].mean(), len(shadow)


def make_heatmap(df, title):
    fig, ax = plt.subplots(figsize=(6, 5.5))
    fig.patch.set_facecolor('#0A1128')
    ax.set_facecolor('#0A1128')

    x_bins = np.linspace(-2.0, 2.0, 31)
    z_bins = np.linspace(0.5, 5.0, 31)
    d = df.copy()
    d['x_bin'] = pd.cut(d['p_x'], bins=x_bins, labels=False)
    d['z_bin'] = pd.cut(d['p_z'], bins=z_bins, labels=False)
    grid = d.groupby(['x_bin', 'z_bin'])['is_called_strike'].mean().unstack()

    im = ax.imshow(grid.T, origin='lower', extent=[-2.0, 2.0, 0.5, 5.0],
                    aspect='auto', cmap='RdYlGn', vmin=0, vmax=1)
    rect = plt.Rectangle((-PLATE_HALF, 1.5), PLATE_HALF * 2, 2.0,
                          linewidth=2, edgecolor='white', facecolor='none', linestyle='--')
    ax.add_patch(rect)

    ax.set_title(title, color='white', fontsize=13, fontweight='bold')
    ax.set_xlabel('Horizontal (ft)', color='#8C9BAB')
    ax.set_ylabel('Vertical (ft)', color='#8C9BAB')
    ax.tick_params(colors='#8C9BAB')
    for spine in ax.spines.values():
        spine.set_color('#24304F')

    cbar = fig.colorbar(im, ax=ax, label='P(Called Strike)')
    cbar.ax.yaxis.label.set_color('#8C9BAB')
    cbar.ax.tick_params(colors='#8C9BAB')

    plt.tight_layout()
    return fig


def make_3d_heatmap(df, sz_top=3.5, sz_bottom=1.5, min_count=5):
    """Interactive 3D strike-zone surface, viewed from roughly where a pitcher
    stands on the mound looking in at the plate. Height of the surface =
    P(called strike) at that location. Drag to orbit, scroll to zoom, hover
    for exact values -- all native to Plotly, no extra wiring needed."""
    x_bins = np.linspace(-2.0, 2.0, 26)
    z_bins = np.linspace(0.5, 5.0, 26)
    d = df.copy()
    d['x_bin'] = pd.cut(d['p_x'], bins=x_bins, labels=False)
    d['z_bin'] = pd.cut(d['p_z'], bins=z_bins, labels=False)

    x_centers = (x_bins[:-1] + x_bins[1:]) / 2
    z_centers = (z_bins[:-1] + z_bins[1:]) / 2

    rate_grid = d.groupby(['x_bin', 'z_bin'])['is_called_strike'].mean().unstack()
    count_grid = d.groupby(['x_bin', 'z_bin']).size().unstack()

    # reindex onto the full grid so missing bins show as gaps, not shifted data
    rate_grid = rate_grid.reindex(index=range(len(x_centers)), columns=range(len(z_centers)))
    count_grid = count_grid.reindex(index=range(len(x_centers)), columns=range(len(z_centers)))

    Z = rate_grid.T.values  # rows = height (z), cols = horizontal (x) for surface orientation
    counts = count_grid.T.values
    Z = np.where((counts >= min_count) & (~np.isnan(Z)), Z, np.nan)

    hover_counts = np.nan_to_num(counts, nan=0).astype(int)

    surface = go.Surface(
        x=x_centers, y=z_centers, z=Z,
        colorscale=[[0, "#C8102E"], [0.5, "#FFC72C"], [1, "#39FF6A"]],
        cmin=0, cmax=1,
        colorbar=dict(title=dict(text="P(Strike)", font=dict(color="#8C9BAB")), tickfont=dict(color="#8C9BAB")),
        customdata=hover_counts,
        hovertemplate=(
            "Horizontal: %{x:.2f} ft<br>"
            "Height: %{y:.2f} ft<br>"
            "P(Called Strike): %{z:.1%}<br>"
            "Pitches in bin: %{customdata}<extra></extra>"
        ),
        showscale=True,
        opacity=0.95,
    )

    # rulebook strike zone outline, drawn as a floating rectangle just above the surface
    rect_x = [-PLATE_HALF, PLATE_HALF, PLATE_HALF, -PLATE_HALF, -PLATE_HALF]
    rect_y = [sz_bottom, sz_bottom, sz_top, sz_top, sz_bottom]
    rect_z = [1.05] * 5
    zone_outline = go.Scatter3d(
        x=rect_x, y=rect_y, z=rect_z,
        mode="lines",
        line=dict(color="white", width=6, dash="dash"),
        hoverinfo="skip",
        name="Rulebook Zone"
    )

    fig = go.Figure(data=[surface, zone_outline])

    fig.update_layout(
        scene=dict(
            xaxis=dict(title="Horizontal (ft)", color="#8C9BAB", gridcolor="#24304F", backgroundcolor="#0A1128"),
            yaxis=dict(title="Height (ft)", color="#8C9BAB", gridcolor="#24304F", backgroundcolor="#0A1128"),
            zaxis=dict(title="P(Strike)", color="#8C9BAB", gridcolor="#24304F", backgroundcolor="#0A1128",
                       range=[0, 1.1]),
            # camera positioned roughly where a pitcher stands on the rubber,
            # looking in toward home plate -- drag with mouse to orbit freely
            camera=dict(
                eye=dict(x=0, y=-2.4, z=0.9),
                up=dict(x=0, y=0, z=1),
                center=dict(x=0, y=0, z=-0.1)
            ),
            aspectmode="manual",
            aspectratio=dict(x=1, y=1, z=0.6),
        ),
        paper_bgcolor="#0A1128",
        plot_bgcolor="#0A1128",
        font=dict(color="#8C9BAB"),
        margin=dict(l=0, r=0, t=10, b=0),
        height=550,
        showlegend=False,
    )
    return fig


# ── Load and render ────────────────────────────────────────────────────────────
data = load_data()
status = data["status"]
latest_date = data["latest_date"]

last_refresh = status.get("last_refresh_time")
last_refresh_str = "unknown" if not last_refresh else datetime.fromisoformat(last_refresh).strftime("%b %d, %Y \u2014 %I:%M %p")
refresh_ok = status.get("last_success", False)

# Ticker bar
st.markdown(f"""
<div class="ticker-bar">
    <div><span class="live-dot"></span>THE STRIKE ZONE REPORT &nbsp;|&nbsp; LATEST GAMES: {latest_date.strftime('%B %d, %Y') if latest_date else 'N/A'}</div>
    <div>DATA REFRESHED: {last_refresh_str} {'✅' if refresh_ok else '⚠️'}</div>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="headline">ABS SHADOW ZONE TRACKER</div>', unsafe_allow_html=True)
st.markdown('<div class="subheadline">Tracking the Automated Ball-Strike Challenge System, one game at a time</div>', unsafe_allow_html=True)

# ── Scoreboard row ─────────────────────────────────────────────────────────────
csr_yesterday, n_yesterday = shadow_csr(data["pitches_yesterday"])
csr_week, n_week = shadow_csr(data["pitches_week"])
csr_season, n_season = shadow_csr(data["pitches_season"])

col1, col2, col3, col4 = st.columns(4)

with col1:
    val = f"{csr_yesterday*100:.1f}%" if csr_yesterday is not None else "—"
    st.markdown(f"""
    <div class="score-card">
        <div class="score-label">Shadow Zone CSR — Last Games</div>
        <div class="score-value">{val}</div>
    </div>
    """, unsafe_allow_html=True)

with col2:
    val = f"{csr_week*100:.1f}%" if csr_week is not None else "—"
    st.markdown(f"""
    <div class="score-card">
        <div class="score-label">7-Day Rolling CSR</div>
        <div class="score-value">{val}</div>
    </div>
    """, unsafe_allow_html=True)

with col3:
    val = f"{csr_season*100:.1f}%" if csr_season is not None else "—"
    st.markdown(f"""
    <div class="score-card">
        <div class="score-label">Season Shadow Zone CSR</div>
        <div class="score-value">{val}</div>
    </div>
    """, unsafe_allow_html=True)

with col4:
    challenges = data["challenges_season"]
    st.markdown(f"""
    <div class="score-card">
        <div class="score-label">Season ABS Overturn Rate</div>
        <div class="score-value">{challenges['pct_overturned']}%</div>
    </div>
    """, unsafe_allow_html=True)

# ── Challenge Review box (yesterday) ──────────────────────────────────────────
st.markdown('<div class="section-label">CHALLENGE REVIEW — LAST NIGHT</div>', unsafe_allow_html=True)
cy = data["challenges_yesterday"]
st.markdown(f"""
<div class="challenge-box">
    <div class="challenge-title">🔍 {cy['total_challenges']} CHALLENGES CALLED</div>
    <p style="color:#C9D4E3; font-family:'IBM Plex Mono',monospace; font-size:14px; margin-top:10px;">
    {cy['overturned']} overturned ({cy['pct_overturned']}%) — the robot disagreed with the human
    on {'more than half' if cy['pct_overturned'] > 50 else 'a notable share of'} contested calls.
    </p>
</div>
""", unsafe_allow_html=True)

# ── Challenge of the Day ───────────────────────────────────────────────────────
cotd = data["cotd"]
if cotd is not None:
    st.markdown('<div class="section-label">🎯 CHALLENGE OF THE DAY</div>', unsafe_allow_html=True)
    outcome = "STRIKE" if cotd['code'] == 'C' else "BALL"
    inside_text = "just inside the zone" if cotd['dist_to_edge'] >= 0 else "just outside the zone"
    st.markdown(f"""
    <div class="challenge-box">
        <div class="challenge-title">⚾ {cotd['batter_name']} vs. {cotd['pitcher_name']}</div>
        <p style="color:#C9D4E3; font-family:'IBM Plex Mono',monospace; font-size:14px; margin-top:10px;">
        The closest overturned call of the day — a pitch {inside_text}, only
        {abs(cotd['dist_to_edge'])*12:.1f} inches from the rulebook edge.
        The umpire's original call was overturned to a {outcome} on review.
        </p>
    </div>
    """, unsafe_allow_html=True)

# ── Player leaderboards (tabbed) ────────────────────────────────────────────────
st.markdown('<div class="section-label">📋 MOST-CHALLENGED PLAYERS — THIS SEASON</div>', unsafe_allow_html=True)
tab1, tab2 = st.tabs(["🧢 Batters", "⚾ Pitchers"])

def render_leaderboard(df):
    if len(df) == 0:
        st.info("No challenge data available yet for this range.")
        return
    for i, row in df.iterrows():
        cols = st.columns([0.5, 3, 1.5, 1.5, 1.5])
        cols[0].markdown(f"<div style='font-family:Teko,sans-serif;font-size:26px;color:#8C9BAB;'>#{i+1}</div>", unsafe_allow_html=True)
        cols[1].markdown(f"<div style='font-family:Teko,sans-serif;font-size:22px;color:white;padding-top:4px;'>{row['full_name']}</div>", unsafe_allow_html=True)
        cols[2].markdown(f"<div style='font-family:IBM Plex Mono,monospace;color:#FFC72C;padding-top:8px;'>{row['challenges']} challenges</div>", unsafe_allow_html=True)
        cols[3].markdown(f"<div style='font-family:IBM Plex Mono,monospace;color:#39FF6A;padding-top:8px;'>{row['overturned']} won</div>", unsafe_allow_html=True)
        cols[4].markdown(f"<div style='font-family:IBM Plex Mono,monospace;color:#8C9BAB;padding-top:8px;'>{row['pct_overturned']}%</div>", unsafe_allow_html=True)

with tab1:
    render_leaderboard(data["batter_leaderboard"])
with tab2:
    render_leaderboard(data["pitcher_leaderboard"])

# ── Hot Ump Games ────────────────────────────────────────────────────────────────
st.markdown('<div class="section-label">🔥 ROUGHEST NIGHTS FOR THE UMPS — LAST 7 DAYS</div>', unsafe_allow_html=True)
hot_games = data["hot_ump_games"]
if len(hot_games) == 0:
    st.info("No overturned calls in the last 7 days.")
else:
    cols = st.columns(len(hot_games))
    for col, (_, row) in zip(cols, hot_games.iterrows()):
        with col:
            away = row['away_team'] or 'Away'
            home = row['home_team'] or 'Home'
            st.markdown(f"""
            <div class="score-card">
                <div class="score-label">{row['game_date']}</div>
                <div style="font-family:'IBM Plex Mono',monospace; color:white; font-size:13px; margin:6px 0;">{away} @ {home}</div>
                <div class="score-value" style="font-size:36px;">{row['overturned']}</div>
                <div class="score-label">overturned / {row['challenges']} challenged</div>
            </div>
            """, unsafe_allow_html=True)


st.markdown('<div class="section-label">STRIKE ZONE HEATMAP — LAST 7 DAYS</div>', unsafe_allow_html=True)
st.markdown(
    "<div style='font-family:IBM Plex Mono,monospace; color:#8C9BAB; font-size:12px; margin-bottom:8px;'>"
    "🖱️ Drag to orbit &nbsp;·&nbsp; Scroll to zoom &nbsp;·&nbsp; Hover a cell for exact rate</div>",
    unsafe_allow_html=True
)
if len(data["pitches_week"]) > 0:
    week_df = data["pitches_week"]
    avg_sz_top = week_df['strike_zone_top'].mean() if 'strike_zone_top' in week_df else 3.5
    avg_sz_bottom = week_df['strike_zone_bottom'].mean() if 'strike_zone_bottom' in week_df else 1.5
    fig3d = make_3d_heatmap(week_df, sz_top=avg_sz_top, sz_bottom=avg_sz_bottom)
    st.plotly_chart(fig3d, use_container_width=True, theme=None)
else:
    st.info("No recent pitch data available yet — check back after tonight's refresh.")

# ── Footer ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="font-family:'IBM Plex Mono',monospace; color:#4A5A73; font-size:11px; text-align:center; margin-top:40px;">
    Data: FFDB (Four-seam Fast Database) via MLB Stats API &nbsp;|&nbsp; Shadow zone = 1 ball-width beyond rulebook edge
</div>
""", unsafe_allow_html=True)
