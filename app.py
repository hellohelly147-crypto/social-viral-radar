import math, os
from datetime import datetime, timezone
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Social Viral Radar", page_icon="🔥", layout="wide")
st.markdown("""
<style>
.block-container{padding-top:1.2rem;max-width:1450px}.hero{padding:20px 24px;border:1px solid #e5e7eb;border-radius:18px;background:linear-gradient(135deg,#fff7ed,#fff);margin-bottom:16px}.hero h1{margin:0}.muted{color:#64748b}.pill{display:inline-block;padding:4px 9px;border-radius:999px;background:#f1f5f9;font-size:.82rem;margin-right:5px}
</style>
<div class="hero"><h1>🔥 Social Viral Radar</h1><div class="muted">Discover popular Instagram Reels, compare engagement and spot breakout content.</div></div>
""", unsafe_allow_html=True)

ACTOR = "apify~instagram-search-scraper"
API = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
GENERAL_TOPICS = ["travel","food","fashion","fitness","technology","AI","business","marketing","entertainment","lifestyle"]

def secret(name, default=""):
    try: return st.secrets.get(name, default)
    except Exception: return os.getenv(name, default)

def n(v):
    try: return int(v or 0)
    except Exception: return 0

def hours_old(v):
    if not v: return 99999
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        return max(1, int((datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds()/3600))
    except Exception: return 99999

def label(score, age_h):
    if age_h <= 72 and score >= 75: return "🚀 Rising"
    if score >= 75: return "🔥 Viral"
    if age_h <= 168: return "✨ Fresh"
    return "Popular"

def viral_score(views, likes, comments, age_h):
    views=max(views,1); age_h=max(age_h,1)
    velocity=views/age_h
    engagement=(likes + 2*comments)/views
    freshness=max(0,1-age_h/(24*30))
    v=min(1, math.log10(max(velocity,1))/5.5)
    e=min(1, engagement/0.10)
    return round(min(99, 100*(0.45*v+0.35*e+0.20*freshness)),1)

@st.cache_data(ttl=1800, show_spinner=False)
def apify_search(token, query, limit):
    payload={"search":query,"searchType":"popular","searchLimit":int(limit),"liveSearch":False}
    r=requests.post(API, params={"token":token}, json=payload, timeout=180)
    if r.status_code >= 400:
        raise RuntimeError(f"Apify API error {r.status_code}: {r.text[:350]}")
    data=r.json()
    return data if isinstance(data,list) else []

def normalize(items):
    rows=[]
    for x in items:
        if not isinstance(x,dict): continue
        views=n(x.get("videoPlayCount") or x.get("videoViewCount") or x.get("views"))
        likes=n(x.get("likesCount") or x.get("likes"))
        comments=n(x.get("commentsCount") or x.get("comments"))
        ts=x.get("timestamp") or x.get("takenAt") or x.get("date")
        age=hours_old(ts)
        score=viral_score(views,likes,comments,age)
        rows.append({
            "creator": x.get("ownerUsername") or x.get("username") or "Unknown",
            "creator_name": x.get("ownerFullName") or "",
            "caption": (x.get("caption") or "")[:700],
            "views": views,"likes":likes,"comments":comments,
            "timestamp": ts or "","age_hours":age,
            "velocity": round(views/max(age,1)),"viral_score":score,
            "status":label(score,age),"url":x.get("url") or "",
            "thumbnail":x.get("displayUrl") or x.get("thumbnailUrl") or "",
            "input_topic": (x.get("inputUrl") or "").rstrip("/").split("/")[-1] or "",
        })
    df=pd.DataFrame(rows)
    if not df.empty:
        df=df.drop_duplicates(subset=["url"],keep="first").sort_values(["viral_score","views"],ascending=False)
    return df

def compact(v):
    v=n(v)
    if v>=1_000_000_000:return f"{v/1_000_000_000:.1f}B"
    if v>=1_000_000:return f"{v/1_000_000:.1f}M"
    if v>=1_000:return f"{v/1_000:.1f}K"
    return str(v)

def render(df):
    if df.empty:
        st.info("No Reels returned for this search. Try a broader keyword.")
        return
    out=df.copy()
    out["Views"]=out.views.map(compact); out["Likes"]=out.likes.map(compact); out["Comments"]=out.comments.map(compact)
    out["Views/hr"]=out.velocity.map(compact); out["Score"]=out.viral_score.map(lambda x:f"{x:.1f}/100")
    st.dataframe(out[["status","creator","caption","age_hours","Views","Likes","Comments","Views/hr","Score","url"]],
        use_container_width=True,hide_index=True,
        column_config={"status":"Signal","creator":"Creator","caption":"Content","age_hours":"Age (hrs)","url":st.column_config.LinkColumn("Instagram")})

TOKEN=secret("APIFY_API_TOKEN")
with st.sidebar:
    st.header("Controls")
    limit=st.slider("Results per search",5,50,10,5)
    window=st.selectbox("Time window",["All available","24 Hours","3 Days","7 Days","30 Days"],index=0)
    st.divider()
    if TOKEN: st.success("Apify API connected")
    else: st.error("APIFY_API_TOKEN missing")
    st.caption("Country filter is intentionally not shown: this Apify popular-Reels search does not provide a reliable country parameter.")
    if st.button("Clear cached results"):
        st.cache_data.clear(); st.success("Cache cleared")

WINDOWS={"24 Hours":24,"3 Days":72,"7 Days":168,"30 Days":720}
def apply_window(df):
    if window=="All available" or df.empty:return df
    return df[df.age_hours<=WINDOWS[window]]

t1,t2=st.tabs(["🔥 General Viral Dashboard","🎯 Niche Explorer"])
with t1:
    st.subheader("General Viral Dashboard")
    st.caption("Choose a broad topic, or use General Mix to scan several categories. Results are global popular Reels, not country-specific.")
    topic=st.selectbox("Category",["General Mix"]+[x.title() for x in GENERAL_TOPICS])
    mix_per_topic=st.slider("General Mix results per category",1,10,3,1,disabled=topic!="General Mix")
    if st.button("Fetch viral Reels",type="primary",disabled=not TOKEN):
        q=",".join(GENERAL_TOPICS) if topic=="General Mix" else topic.lower()
        lim=mix_per_topic if topic=="General Mix" else limit
        try:
            with st.spinner("Fetching popular Instagram Reels…"):
                st.session_state.general=normalize(apify_search(TOKEN,q,lim))
        except Exception as e: st.error(str(e))
    df=apply_window(st.session_state.get("general",pd.DataFrame()))
    if not df.empty:
        a,b,c,d=st.columns(4)
        a.metric("Reels",len(df)); b.metric("Total Views",compact(df.views.sum())); c.metric("Best Score",f"{df.viral_score.max():.1f}"); d.metric("Rising/Viral",int(df.status.isin(["🚀 Rising","🔥 Viral"]).sum()))
    render(df)

with t2:
    st.subheader("Niche Viral Explorer")
    niche=st.text_input("Keyword / niche",placeholder="e.g. travel, digital marketing, industrial valves")
    if st.button("Search popular Reels",type="primary",disabled=(not TOKEN or not niche.strip())):
        try:
            with st.spinner(f"Searching Instagram for '{niche}'…"):
                st.session_state.niche=normalize(apify_search(TOKEN,niche.strip(),limit)); st.session_state.niche_q=niche.strip()
        except Exception as e: st.error(str(e))
    df2=apply_window(st.session_state.get("niche",pd.DataFrame())) if st.session_state.get("niche_q")==niche.strip() else pd.DataFrame()
    if not df2.empty:
        a,b,c=st.columns(3); a.metric("Matching Reels",len(df2)); b.metric("Total Views",compact(df2.views.sum())); c.metric("Best Score",f"{df2.viral_score.max():.1f}")
    render(df2)

st.divider()
st.caption("V1.2 • Instagram popular-Reels discovery via Apify • Viral Score is an internal heuristic using view velocity, engagement and freshness. Popular search can surface older global Reels; it is not a complete Instagram firehose.")
