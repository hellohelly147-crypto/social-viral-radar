import math, os, time
from datetime import datetime, timezone, timedelta
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Social Viral Radar", page_icon="🔥", layout="wide")
st.markdown("""
<style>
.block-container{padding-top:1.2rem;max-width:1450px}.hero{padding:20px 24px;border:1px solid #e5e7eb;border-radius:18px;background:linear-gradient(135deg,#fff7ed,#fff);margin-bottom:16px}.hero h1{margin:0}.muted{color:#64748b}.small{font-size:.86rem;color:#64748b}
</style>
<div class="hero"><h1>🔥 Social Viral Radar</h1><div class="muted">Separate proven popular Reels from fresh, fast-moving Reels.</div></div>
""", unsafe_allow_html=True)

POPULAR_ACTOR = "apify~instagram-search-scraper"
TREND_ACTOR = "maximedupre~instagram-reels-search-scraper"
BASE = "https://api.apify.com/v2/acts"
GENERAL_TOPICS = ["travel","food","fashion","fitness","technology","AI","business","marketing","entertainment","lifestyle"]

def secret(name, default=""):
    try: return st.secrets.get(name, default)
    except Exception: return os.getenv(name, default)

def n(v):
    try: return int(v or 0)
    except Exception: return 0

def parse_dt(v):
    if not v: return None
    try:
        if isinstance(v,(int,float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        s=str(v).replace("Z", "+00:00")
        dt=datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception: return None

def hours_old(v):
    dt=parse_dt(v)
    if not dt: return 99999
    return max(1, int((datetime.now(timezone.utc)-dt).total_seconds()/3600))

def score_reel(views, likes, comments, age_h):
    views=max(views,1); age_h=max(age_h,1)
    velocity=views/age_h
    engagement=(likes+2*comments)/views
    # Freshness intentionally decays quickly: ~1 at new, 0 at 30 days.
    freshness=max(0, 1-age_h/720)
    velocity_component=min(1, math.log10(max(velocity,1))/5.3)
    engagement_component=min(1, engagement/0.10)
    return round(min(99,100*(0.48*velocity_component+0.30*engagement_component+0.22*freshness)),1)

def signal(score, age_h, velocity):
    if age_h <= 72 and score >= 72: return "🚀 Rising"
    if age_h <= 168 and score >= 62: return "⚡ Trending"
    if score >= 75: return "🔥 Viral"
    if age_h <= 168: return "✨ Fresh"
    return "Popular"

@st.cache_data(ttl=1800, show_spinner=False)
def popular_search(token, query, limit):
    url=f"{BASE}/{POPULAR_ACTOR}/run-sync-get-dataset-items"
    payload={"search":query,"searchType":"popular","searchLimit":int(limit),"liveSearch":False}
    r=requests.post(url,params={"token":token},json=payload,timeout=180)
    if r.status_code>=400: raise RuntimeError(f"Popular search error {r.status_code}: {r.text[:450]}")
    data=r.json(); return data if isinstance(data,list) else []

@st.cache_data(ttl=900, show_spinner=False)
def trending_search(token, terms, limit, days, min_plays):
    # This Actor can take longer than a synchronous HTTP request. Start the run,
    # poll its status, then fetch the default dataset when it succeeds.
    after=(datetime.now(timezone.utc)-timedelta(days=days)).date().isoformat()
    payload={"searchTerms":terms,"maxItems":int(limit),"includeTranscripts":False,"publishedAfter":after}
    if min_plays>0: payload["minPlays"]=int(min_plays)

    headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}
    start_url=f"{BASE}/{TREND_ACTOR}/runs"
    r=requests.post(start_url,headers=headers,json=payload,timeout=30)
    if r.status_code>=400:
        raise RuntimeError(f"Trending run start error {r.status_code}: {r.text[:450]}")
    body=r.json()
    run=(body.get("data") or {}) if isinstance(body,dict) else {}
    run_id=run.get("id")
    if not run_id:
        raise RuntimeError("Apify started no usable run ID for Trending Now.")

    deadline=time.time()+420  # allow up to 7 minutes for the community Actor
    terminal={"SUCCEEDED","FAILED","TIMED-OUT","ABORTED"}
    status=run.get("status","READY")
    status_message=run.get("statusMessage") or ""
    while time.time()<deadline:
        sr=requests.get(f"https://api.apify.com/v2/actor-runs/{run_id}",headers={"Authorization":f"Bearer {token}"},params={"waitForFinish":20},timeout=30)
        if sr.status_code>=400:
            raise RuntimeError(f"Trending status error {sr.status_code}: {sr.text[:450]}")
        info=(sr.json().get("data") or {})
        status=info.get("status",status)
        status_message=info.get("statusMessage") or status_message
        if status in terminal:
            run=info
            break
        time.sleep(2)
    else:
        raise RuntimeError(f"Trending run is still processing after 7 minutes (run {run_id}). Try again later; the existing Apify run may still finish.")

    if status!="SUCCEEDED":
        raise RuntimeError(f"Trending Actor ended with {status}: {status_message or 'No status message'}")

    dr=requests.get(
        f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items",
        headers={"Authorization":f"Bearer {token}"},
        params={"clean":"true","limit":int(limit)},
        timeout=60,
    )
    if dr.status_code>=400:
        raise RuntimeError(f"Trending dataset error {dr.status_code}: {dr.text[:450]}")
    data=dr.json()
    return data if isinstance(data,list) else []

def normalize(items, source):
    rows=[]
    for x in items:
        if not isinstance(x,dict): continue
        author=x.get("author") if isinstance(x.get("author"),dict) else {}
        media=x.get("media") if isinstance(x.get("media"),dict) else {}
        views=n(x.get("videoPlayCount") or x.get("videoViewCount") or x.get("plays") or x.get("views"))
        likes=n(x.get("likesCount") or x.get("likes")); comments=n(x.get("commentsCount") or x.get("comments"))
        ts=x.get("timestamp") or x.get("publishedAt") or x.get("takenAt") or x.get("takenAtIso") or x.get("date")
        age=hours_old(ts); velocity=round(views/max(age,1)); score=score_reel(views,likes,comments,age)
        rows.append({
            "signal":signal(score,age,velocity),"creator":x.get("ownerUsername") or author.get("username") or x.get("username") or "Unknown",
            "caption":(x.get("caption") or "")[:650],"age_hours":age,"views":views,"likes":likes,"comments":comments,
            "engagement":round(100*(likes+comments)/max(views,1),2),"velocity":velocity,"score":score,
            "url":x.get("url") or x.get("permalink") or "","thumbnail":x.get("displayUrl") or x.get("thumbnailUrl") or media.get("thumbnailUrl") or "",
            "source":source,"timestamp":ts or ""
        })
    df=pd.DataFrame(rows)
    if not df.empty:
        if "url" in df: df=df.drop_duplicates(subset=["url"],keep="first")
        df=df.sort_values(["score","velocity","views"],ascending=False)
    return df

def compact(v):
    v=n(v)
    if v>=1_000_000_000:return f"{v/1_000_000_000:.1f}B"
    if v>=1_000_000:return f"{v/1_000_000:.1f}M"
    if v>=1_000:return f"{v/1_000:.1f}K"
    return str(v)

def render(df, attempted=False):
    if df.empty:
        if attempted:
            st.info("No matching Reels returned. Try a broader keyword, a longer freshness window, or a lower minimum views threshold.")
        else:
            st.caption("Run a search to load live Reel data.")
        return
    out=df.copy(); out["Age"]=out.age_hours.map(lambda h:f"{h}h" if h<48 else f"{h/24:.0f}d")
    out["Views"]=out.views.map(compact); out["Likes"]=out.likes.map(compact); out["Comments"]=out.comments.map(compact)
    out["Views/hr"]=out.velocity.map(compact); out["Engagement"]=out.engagement.map(lambda x:f"{x:.2f}%"); out["Score"]=out.score.map(lambda x:f"{x:.1f}")
    st.dataframe(out[["signal","creator","caption","Age","Views","Likes","Comments","Views/hr","Engagement","Score","url"]],use_container_width=True,hide_index=True,
        column_config={"signal":"Signal","creator":"Creator","caption":"Content","url":st.column_config.LinkColumn("Instagram")})

APP_TOKEN = secret("APIFY_API_TOKEN")

def masked_token(token):
    if not token:
        return ""
    if len(token) <= 8:
        return "••••••••"
    return f"{token[:4]}••••••••{token[-4:]}"

def validate_apify_token(token):
    """Lightweight authentication check; does not start an Actor run."""
    try:
        r = requests.get(
            "https://api.apify.com/v2/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code == 200:
            return True, ""
        if r.status_code in (401, 403):
            return False, "Apify rejected this API token."
        return False, f"Apify connection check returned HTTP {r.status_code}."
    except requests.RequestException as e:
        return False, f"Could not reach Apify: {e}"

# BYOK state is session-only. We never write the user's token to disk or GitHub.
if "user_apify_token" not in st.session_state:
    st.session_state.user_apify_token = ""
if "user_token_validated" not in st.session_state:
    st.session_state.user_token_validated = False

with st.sidebar:
    st.header("Controls")
    limit=st.slider("Results",5,50,10,5)

    st.divider()
    st.subheader("API Settings")

    api_choices = ["Use my own Apify API"]
    if APP_TOKEN:
        api_choices.append("Use app API")

    default_choice = "Use app API" if APP_TOKEN and not st.session_state.user_apify_token else "Use my own Apify API"
    api_source = st.radio(
        "API source",
        api_choices,
        index=api_choices.index(default_choice),
        help="Your own token is kept only in this Streamlit session. The app API comes from Streamlit Secrets.",
    )

    if api_source == "Use my own Apify API":
        entered_token = st.text_input(
            "Apify API token",
            type="password",
            value=st.session_state.user_apify_token,
            placeholder="apify_api_...",
            help="The token is used for Apify requests in this session and is not written to GitHub or app files.",
        ).strip()

        if entered_token != st.session_state.user_apify_token:
            st.session_state.user_apify_token = entered_token
            st.session_state.user_token_validated = False

        c_test, c_clear = st.columns(2)
        if c_test.button("Test connection", use_container_width=True, disabled=not entered_token):
            ok, msg = validate_apify_token(entered_token)
            st.session_state.user_token_validated = ok
            if ok:
                st.success("Your Apify API connected")
            else:
                st.error(msg)

        if c_clear.button("Clear key", use_container_width=True, disabled=not st.session_state.user_apify_token):
            st.session_state.user_apify_token = ""
            st.session_state.user_token_validated = False
            st.cache_data.clear()
            st.rerun()

        TOKEN = st.session_state.user_apify_token
        if TOKEN:
            if st.session_state.user_token_validated:
                st.success("Using your Apify API")
            else:
                st.info("API key entered. Test connection is recommended before searching.")
                st.caption(f"Session key: {masked_token(TOKEN)}")
        else:
            st.warning("Enter your Apify API token to search.")
    else:
        TOKEN = APP_TOKEN
        st.success("Using app API")
        st.caption("This token is stored in Streamlit Secrets and is never shown in the app.")

    st.caption("API keys entered here are kept in Streamlit session state only. Avoid sharing screenshots that expose tokens.")
    st.caption("Country is not claimed because these search surfaces do not provide a reliable country-only filter.")

    if st.button("Clear cached results"):
        st.cache_data.clear()
        st.success("Cache cleared")

t_pop,t_now,t_niche=st.tabs(["🔥 Popular Reels","⚡ Trending Now","🎯 Niche Explorer"])

with t_pop:
    st.subheader("Popular Reels")
    st.caption("Best for proven high-performing content. These results can be older because Instagram's popular surface is not a freshness feed.")
    topic=st.selectbox("Category",[x.title() for x in GENERAL_TOPICS],key="pop_topic")
    if st.button("Fetch popular Reels",type="primary",disabled=not TOKEN,key="pop_btn"):
        try:
            with st.spinner("Fetching popular Reels…"):
                raw = popular_search(TOKEN,topic.lower(),limit)
                st.session_state.pop_raw_count = len(raw)
                st.session_state.pop=normalize(raw,"Popular")
                st.session_state.pop_attempted=True
        except Exception as e: st.error(str(e))
    df=st.session_state.get("pop",pd.DataFrame())
    if not df.empty:
        a,b,c,d=st.columns(4); a.metric("Reels",len(df)); b.metric("Total Views",compact(df.views.sum())); c.metric("Best Score",f"{df.score.max():.1f}"); d.metric("Top Views/hr",compact(df.velocity.max()))
    render(df, st.session_state.get("pop_attempted", False))
    if st.session_state.get("pop_attempted", False):
        st.caption(f"API records received: {st.session_state.get('pop_raw_count', 0)}")

with t_now:
    st.subheader("Trending Now")
    st.caption("Searches the currently ranked public Reels surface and applies a publication-date filter. This is the tab to use for fresh/rising content.")
    c1,c2,c3=st.columns([2,1,1])
    trend_q=c1.text_input("Topic / keyword",value="travel",key="trend_q")
    days=c2.selectbox("Freshness",[1,3,7,14,30],index=2,format_func=lambda x:f"Last {x} day" if x==1 else f"Last {x} days")
    min_views=c3.selectbox("Min views",[0,1000,10000,50000,100000,500000],index=2,format_func=lambda x:"Any" if x==0 else compact(x))
    if st.button("Find fresh trending Reels",type="primary",disabled=(not TOKEN or not trend_q.strip()),key="trend_btn"):
        try:
            with st.spinner("Scanning fresh Reels…"):
                raw = trending_search(TOKEN,[trend_q.strip()],limit,days,min_views)
                st.session_state.trend_raw_count = len(raw)
                st.session_state.trend=normalize(raw,"Trending Now")
                st.session_state.trend_attempted=True
        except Exception as e:
            st.session_state.trend_attempted=False
            st.session_state.trend=pd.DataFrame()
            st.error(str(e))
    df=st.session_state.get("trend",pd.DataFrame())
    if not df.empty:
        a,b,c,d=st.columns(4); a.metric("Fresh Reels",len(df)); b.metric("Rising/Trending",int(df.signal.isin(["🚀 Rising","⚡ Trending"]).sum())); c.metric("Best Score",f"{df.score.max():.1f}"); d.metric("Top Views/hr",compact(df.velocity.max()))
    render(df, st.session_state.get("trend_attempted", False))
    if st.session_state.get("trend_attempted", False):
        st.caption(f"API records received: {st.session_state.get('trend_raw_count', 0)}")

with t_niche:
    st.subheader("Niche Explorer")
    st.caption("Compare proven popularity with recent momentum for a specific niche.")
    niche=st.text_input("Keyword / niche",placeholder="e.g. digital marketing, industrial valves, gujarati food",key="niche")
    mode=st.radio("Discovery mode",["Trending Now","Popular"],horizontal=True)
    if mode=="Trending Now":
        ndays=st.selectbox("Published within",[3,7,14,30],index=1,key="ndays")
    if st.button("Search niche",type="primary",disabled=(not TOKEN or not niche.strip()),key="niche_btn"):
        try:
            with st.spinner(f"Searching '{niche}'…"):
                if mode=="Trending Now": raw=trending_search(TOKEN,[niche.strip()],limit,ndays,0); src="Trending Now"
                else: raw=popular_search(TOKEN,niche.strip(),limit); src="Popular"
                st.session_state.niche_raw_count = len(raw)
                st.session_state.niche_df=normalize(raw,src)
                st.session_state.niche_attempted=True
        except Exception as e: st.error(str(e))
    df=st.session_state.get("niche_df",pd.DataFrame())
    if not df.empty:
        a,b,c=st.columns(3); a.metric("Matching Reels",len(df)); b.metric("Total Views",compact(df.views.sum())); c.metric("Best Score",f"{df.score.max():.1f}")
    render(df, st.session_state.get("niche_attempted", False))
    if st.session_state.get("niche_attempted", False):
        st.caption(f"API records received: {st.session_state.get('niche_raw_count', 0)}")

st.divider()
st.caption("V1.3.3 • Popular and Trending Now are intentionally separate. Viral Score is an internal heuristic based on view velocity, engagement and freshness; it is not an Instagram-provided metric.")
