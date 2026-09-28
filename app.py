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
TREND_ACTOR = "scraping_solutions~instagram-boolean-search-scraper-posts-reels"
PROFILE_ACTOR = "apify~instagram-profile-scraper"
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

    # V1.5: stronger "now" weighting. Freshness halves roughly every 30 hours,
    # so an older mega-hit needs exceptional current velocity to stay on top.
    freshness=0.5**(age_h/30.0)
    velocity_component=min(1, math.log10(max(velocity,1))/5.3)
    engagement_component=min(1, engagement/0.10)
    popularity_component=min(1, math.log10(max(views,1))/8.0)

    return round(min(99,100*(
        0.42*velocity_component +
        0.18*engagement_component +
        0.32*freshness +
        0.08*popularity_component
    )),1)

def signal(score, age_h, velocity):
    if age_h <= 24 and (score >= 72 or velocity >= 10000): return "🔥 Breakout"
    if age_h <= 72 and (score >= 58 or velocity >= 2000): return "⚡ Rising"
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
def trending_search(token, query, limit, days, min_views):
    """
    Discover recent public Instagram Reels using a Reel-only Boolean/keyword search.
    The Actor applies a lower publication-date bound and can use recent hashtag feeds
    for one-word topics. We still calculate our own Viral Score after retrieval.
    """
    after=(datetime.now(timezone.utc)-timedelta(days=days)).date().isoformat()
    payload={
        "searchQuery": query.strip(),
        "resultsLimit": int(limit),
        "contentType": "reels_only",
        "hashtagFeedType": "recent",
        "searchCoverage": "efficient",
        "oldestPostDate": after,
        "minimumViews": int(min_views or 0),
    }

    headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}
    r=requests.post(f"{BASE}/{TREND_ACTOR}/runs",headers=headers,json=payload,timeout=30)
    if r.status_code>=400:
        raise RuntimeError(f"Trending run start error {r.status_code}: {r.text[:450]}")

    body=r.json()
    run=(body.get("data") or {}) if isinstance(body,dict) else {}
    run_id=run.get("id")
    if not run_id:
        raise RuntimeError("Apify started no usable run ID for Trending Now.")

    deadline=time.time()+420
    terminal={"SUCCEEDED","FAILED","TIMED-OUT","ABORTED"}
    status=run.get("status","READY")
    status_message=run.get("statusMessage") or ""

    while time.time()<deadline:
        sr=requests.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}",
            headers={"Authorization":f"Bearer {token}"},
            params={"waitForFinish":20},
            timeout=30,
        )
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
        raise RuntimeError(
            f"Trending search is still processing after 7 minutes (run {run_id}). "
            "Do not start another duplicate run; check the existing run in Apify Console."
        )

    if status!="SUCCEEDED":
        raise RuntimeError(f"Trending Actor ended with {status}: {status_message or 'No status message'}")

    dataset_id=run.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError("Trending run succeeded but no default dataset ID was returned.")

    dr=requests.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
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
        views=n(x.get("videoPlayCount") or x.get("videoViewCount") or x.get("playCount") or x.get("viewCount") or x.get("plays") or x.get("views"))
        likes=n(x.get("likesCount") or x.get("likeCount") or x.get("likes")); comments=n(x.get("commentsCount") or x.get("commentCount") or x.get("comments"))
        ts=x.get("timestamp") or x.get("publishedAt") or x.get("posted_at") or x.get("takenAt") or x.get("takenAtIso") or x.get("date")
        age=hours_old(ts); velocity=round(views/max(age,1)); score=score_reel(views,likes,comments,age)
        rows.append({
            "signal":signal(score,age,velocity),"creator":x.get("ownerUsername") or author.get("username") or x.get("author_username") or x.get("username") or "Unknown",
            "caption":(x.get("caption") or x.get("text") or "")[:650],"age_hours":age,"views":views,"likes":likes,"comments":comments,
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
            st.info("No matching Reels returned. Try a broader keyword, a longer freshness window, or a lower minimum views threshold. Instagram discovery is bounded, so zero results does not prove that no such Reels exist.")
        else:
            st.caption("Run a search to load live Reel data.")
        return
    out=df.copy(); out["Age"]=out.age_hours.map(lambda h:f"{h}h" if h<48 else f"{h/24:.0f}d")
    out["Views"]=out.views.map(compact); out["Likes"]=out.likes.map(compact); out["Comments"]=out.comments.map(compact)
    out["Views/hr"]=out.velocity.map(compact); out["Engagement"]=out.engagement.map(lambda x:f"{x:.2f}%"); out["Score"]=out.score.map(lambda x:f"{x:.1f}")
    st.dataframe(out[["signal","creator","caption","Age","Views","Likes","Comments","Views/hr","Engagement","Score","url"]],use_container_width=True,hide_index=True,
        column_config={"signal":"Signal","creator":"Creator","caption":"Content","url":st.column_config.LinkColumn("Instagram")})



def actor_dataset(token, actor_id, payload, label="Actor", timeout_seconds=420):
    headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}
    r=requests.post(f"{BASE}/{actor_id}/runs",headers=headers,json=payload,timeout=30)
    if r.status_code>=400:
        raise RuntimeError(f"{label} start error {r.status_code}: {r.text[:450]}")
    run=(r.json().get("data") or {})
    run_id=run.get("id")
    if not run_id:
        raise RuntimeError(f"{label} returned no run ID.")
    deadline=time.time()+timeout_seconds
    while time.time()<deadline:
        sr=requests.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}",
            headers={"Authorization":f"Bearer {token}"},
            params={"waitForFinish":20},timeout=30
        )
        if sr.status_code>=400:
            raise RuntimeError(f"{label} status error {sr.status_code}: {sr.text[:450]}")
        info=(sr.json().get("data") or {})
        status=info.get("status")
        if status in {"SUCCEEDED","FAILED","TIMED-OUT","ABORTED"}:
            if status!="SUCCEEDED":
                raise RuntimeError(f"{label} ended with {status}: {info.get('statusMessage','')}")
            ds=info.get("defaultDatasetId")
            if not ds: return []
            dr=requests.get(
                f"https://api.apify.com/v2/datasets/{ds}/items",
                headers={"Authorization":f"Bearer {token}"},
                params={"clean":"true","format":"json"},timeout=60
            )
            if dr.status_code>=400:
                raise RuntimeError(f"{label} dataset error {dr.status_code}: {dr.text[:450]}")
            return dr.json() if isinstance(dr.json(),list) else []
        time.sleep(2)
    raise RuntimeError(f"{label} is still processing after {timeout_seconds//60} minutes.")

def _country_strings(obj, path=""):
    """Find country-like values without assuming one fixed Apify output schema."""
    found=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=f"{path}.{k}" if path else k
            lk=k.lower()
            if "country" in lk and isinstance(v,(str,int,float)):
                found.append((p,str(v)))
            found.extend(_country_strings(v,p))
    elif isinstance(obj,list):
        for i,v in enumerate(obj):
            found.extend(_country_strings(v,f"{path}[{i}]"))
    return found

def verify_india_creators(token, creators):
    creators=[str(x).strip().lstrip("@") for x in creators if str(x).strip() and str(x)!="Unknown"]
    creators=list(dict.fromkeys(creators))
    if not creators: return {}, []
    profiles=actor_dataset(
        token, PROFILE_ACTOR,
        {"usernames":creators,"includeAboutSection":True},
        label="India profile verification"
    )
    verdict={}
    evidence=[]
    for p in profiles:
        username=(p.get("username") or p.get("userName") or p.get("input") or "").strip().lstrip("@")
        pairs=_country_strings(p)
        india_hits=[(k,v) for k,v in pairs if v.strip().lower() in {"india","in","ind"} or "india" in v.lower()]
        other=[(k,v) for k,v in pairs if v.strip()]
        is_india=bool(india_hits)
        if username:
            verdict[username.lower()]=is_india
            evidence.append({
                "creator":username,
                "india_verified":is_india,
                "geo_evidence":"; ".join([f"{k}: {v}" for k,v in india_hits]) if india_hits else
                               ("Country metadata present but not India" if other else "Country of origin unavailable")
            })
    return verdict, evidence

def niche_benchmark(df):
    """Benchmark each Reel against only the returned niche sample."""
    if df.empty:
        return df
    d=df.copy()
    med_v=max(float(d.velocity.median()),1.0)
    med_e=max(float(d.engagement.median()),0.01)
    med_views=max(float(d.views.median()),1.0)

    d["peer_velocity_x"]=(d.velocity/med_v).round(2)
    d["velocity_pct"]=d.velocity.rank(pct=True,method="average")*100
    d["engagement_pct"]=d.engagement.rank(pct=True,method="average")*100
    d["views_pct"]=d.views.rank(pct=True,method="average")*100
    d["niche_score"]=(0.55*d.velocity_pct+0.30*d.engagement_pct+0.15*d.views_pct).round(1)

    def bucket(r):
        if r.niche_score>=82 and r.peer_velocity_x>=1.8: return "🔥 Viral / Breakout"
        if r.niche_score>=65 and r.peer_velocity_x>=1.15: return "⚡ Rising"
        if r.niche_score<35 and r.peer_velocity_x<0.75: return "🔴 Low Performing"
        return "🟢 Normal"
    d["performance"]=d.apply(bucket,axis=1)
    return d.sort_values(["niche_score","velocity"],ascending=False)

def render_niche(df, attempted=False):
    if df.empty:
        if attempted: st.info("No matching niche content returned. Try a broader niche phrase or a longer freshness window.")
        else: st.caption("Run a niche scan to load recent public Reel data.")
        return
    out=df.copy()
    out["Age"]=out.age_hours.map(lambda h:f"{h}h" if h<48 else f"{h/24:.0f}d")
    out["Views"]=out.views.map(compact); out["Likes"]=out.likes.map(compact); out["Comments"]=out.comments.map(compact)
    out["Views/hr"]=out.velocity.map(compact); out["Engagement"]=out.engagement.map(lambda x:f"{x:.2f}%")
    out["vs Niche"]=out.peer_velocity_x.map(lambda x:f"{x:.1f}×")
    out["Niche Score"]=out.niche_score.map(lambda x:f"{x:.1f}")
    st.dataframe(
        out[["performance","creator","caption","Age","Views","Likes","Comments","Views/hr","Engagement","vs Niche","Niche Score","url"]],
        use_container_width=True,hide_index=True,
        column_config={"performance":"Performance","creator":"Creator","caption":"Content","url":st.column_config.LinkColumn("Instagram")}
    )

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
    st.caption("Discovers recent public Reels and ranks current momentum. V1.5 gives stronger weight to freshness + views/hour so older mega-hits do not automatically dominate Trending Now.")
    st.caption("Signals: 🔥 Breakout = very recent + fast velocity · ⚡ Rising = recent momentum · ✨ Fresh = recent discovery")
    c1,c2,c3=st.columns([2,1,1])
    trend_q=c1.text_input("Topic / keyword",value="travel",key="trend_q")
    days=c2.selectbox("Freshness",[1,3,7,14,30],index=2,format_func=lambda x:f"Last {x} day" if x==1 else f"Last {x} days")
    min_views=c3.selectbox("Min views",[0,1000,10000,50000,100000,500000],index=2,format_func=lambda x:"Any" if x==0 else compact(x))
    f1,f2=st.columns(2)
    age_filter=f1.selectbox("Show age",["All returned","≤ 24 hours","≤ 48 hours","≤ 72 hours"],index=0,
        help="Client-side filter. Changing this does not start another Apify run.")
    sort_mode=f2.selectbox("Rank by",["Trending Score","Views / hour","Newest first","Engagement"],index=0,
        help="Client-side ranking. Changing this does not start another Apify run.")
    st.caption("Tip: start with 10 results + Efficient search. Age/ranking controls are client-side, so changing them does not consume another Apify run.")
    if st.button("Find fresh trending Reels",type="primary",disabled=(not TOKEN or not trend_q.strip()),key="trend_btn"):
        try:
            with st.spinner("Searching recent public Reels… This can take a few minutes."):
                raw = trending_search(TOKEN,trend_q.strip(),limit,days,min_views)
                st.session_state.trend_raw_count = len(raw)
                st.session_state.trend=normalize(raw,"Trending Now")
                st.session_state.trend_attempted=True
        except Exception as e:
            st.session_state.trend_attempted=False
            st.session_state.trend=pd.DataFrame()
            st.error(str(e))
    df=st.session_state.get("trend",pd.DataFrame())
    display_df=df.copy()
    if not display_df.empty:
        age_limits={"≤ 24 hours":24,"≤ 48 hours":48,"≤ 72 hours":72}
        if age_filter in age_limits:
            display_df=display_df[display_df.age_hours<=age_limits[age_filter]]

        if sort_mode=="Views / hour":
            display_df=display_df.sort_values(["velocity","score"],ascending=False)
        elif sort_mode=="Newest first":
            display_df=display_df.sort_values(["age_hours","score"],ascending=[True,False])
        elif sort_mode=="Engagement":
            display_df=display_df.sort_values(["engagement","score"],ascending=False)
        else:
            display_df=display_df.sort_values(["score","velocity"],ascending=False)

        a,b,c,d=st.columns(4)
        a.metric("Reels shown",len(display_df))
        b.metric("Breakout / Rising",int(display_df.signal.isin(["🔥 Breakout","⚡ Rising"]).sum()))
        c.metric("Best Score",f"{display_df.score.max():.1f}" if not display_df.empty else "—")
        d.metric("Top Views/hr",compact(display_df.velocity.max()) if not display_df.empty else "—")

    render(display_df, st.session_state.get("trend_attempted", False))
    if st.session_state.get("trend_attempted", False):
        st.caption(f"API records received: {st.session_state.get('trend_raw_count', 0)}")

with t_niche:
    st.subheader("🇮🇳 India Viral Niche Radar")
    st.caption("India-only mode: candidate Reels are discovered by niche, then creator country-of-origin is checked. Only creators whose available account metadata explicitly verifies India are kept.")

    industries={
        "Industrial / Manufacturing":["Industrial Valves","Pneumatics","Valve Automation","Process Automation","Industrial Pumps","Cleanroom Equipment","Packaging Machinery","Custom niche"],
        "Digital Marketing":["SEO","Technical SEO","Google Ads","Meta Ads","Social Media Marketing","Content Marketing","Local SEO","Ecommerce Marketing","Custom niche"],
        "Travel & Hospitality":["Family Travel","Luxury Travel","Budget Travel","Hotels & Resorts","Honeymoon Travel","International Travel","Holiday Membership","Custom niche"],
        "Fashion & Apparel":["Menswear","Womenswear","T-Shirts","Ethnic Wear","Streetwear","Fashion Accessories","Custom niche"],
        "Healthcare & Aesthetics":["Laser Hair Removal","Skin Care","Medical Aesthetics","Dental","Wellness","Clinics","Custom niche"],
        "Food & Ingredients":["Food Ingredients","Dehydrated Onion","Dehydrated Garlic","Chicory","Food Manufacturing","B2B Food Supply","Custom niche"],
        "3D Printing":["3D Printed Gifts","Personalized Keychains","3D Printed Lamps","NFC Products","Bambu Lab","Custom 3D Prints","Custom niche"],
        "Custom industry":["Custom niche"]
    }

    c1,c2,c3=st.columns([1.2,1.2,.7])
    industry=c1.selectbox("Industry",list(industries),key="industry_v17")
    niche_pick=c2.selectbox("Specific niche",industries[industry],key="niche_v17")
    c3.text_input("Country",value="India 🇮🇳",disabled=True,key="country_v17")

    custom_industry=st.text_input("Custom industry",placeholder="e.g. Real Estate",key="custom_ind_v17") if industry=="Custom industry" else ""
    custom_niche=st.text_input("Custom niche",placeholder="e.g. Pneumatic actuated ball valves",key="custom_niche_v17") if niche_pick=="Custom niche" else ""
    chosen_industry=(custom_industry or industry).strip()
    chosen_niche=(custom_niche or niche_pick).strip()

    q1,q2,q3=st.columns([1.5,1,1])
    extras=q1.text_input("Extra keywords (optional)",placeholder="e.g. actuator, ball valve",key="extras_v17")
    days=q2.selectbox("Freshness",[7,14,30],index=1,format_func=lambda x:f"Last {x} days",key="days_v17")
    candidate_pool=q3.selectbox("Candidate pool",[30,50,80],index=0,key="pool_v17",
        help="More candidates can find more verified India results, but uses more Apify resources.")

    st.info("Strict verification intentionally drops creators whose country-of-origin is unavailable. So final India results can be fewer than the requested Results count.")

    if st.button("Find India viral content",type="primary",disabled=(not TOKEN or not chosen_niche),key="scan_v17"):
        terms=[chosen_niche]
        if extras.strip():
            terms += [x.strip() for x in extras.split(",") if x.strip()][:4]
        # India terms help candidate discovery, but DO NOT count as verification.
        query=" OR ".join(dict.fromkeys(terms + ["India"]))
        try:
            with st.spinner("1/2 Discovering recent niche Reels…"):
                raw=trending_search(TOKEN,query,candidate_pool,days,0)
            candidate_df=normalize(raw,"India Candidate")
            if candidate_df.empty:
                st.session_state.india_v17=pd.DataFrame()
                st.session_state.india_v17_stats={"candidates":0,"verified":0}
            else:
                with st.spinner("2/2 Verifying creator country-of-origin as India…"):
                    verdict,evidence=verify_india_creators(TOKEN,candidate_df.creator.tolist())
                candidate_df["india_verified"]=candidate_df.creator.str.lower().map(verdict).fillna(False)
                india_df=candidate_df[candidate_df.india_verified].copy()
                if not india_df.empty:
                    india_df=niche_benchmark(india_df)
                    # Viral-only output: keep strong relative momentum; if sample is tiny,
                    # use the existing momentum score as a conservative fallback.
                    if len(india_df)>=5:
                        india_df=india_df[india_df.performance.isin(["🔥 Viral / Breakout","⚡ Rising"])].copy()
                    else:
                        india_df=india_df[india_df.score>=58].copy()
                    india_df=india_df.sort_values(["score","velocity"],ascending=False).head(limit)
                st.session_state.india_v17=india_df
                st.session_state.india_v17_evidence=pd.DataFrame(evidence)
                st.session_state.india_v17_stats={
                    "candidates":len(candidate_df),
                    "verified":int(candidate_df.india_verified.sum()),
                    "viral":len(india_df)
                }
            st.session_state.india_v17_attempted=True
            st.session_state.india_v17_query=query
        except Exception as e:
            st.error(str(e))
            st.session_state.india_v17=pd.DataFrame()
            st.session_state.india_v17_attempted=True

    df=st.session_state.get("india_v17",pd.DataFrame())
    attempted=st.session_state.get("india_v17_attempted",False)
    stats=st.session_state.get("india_v17_stats",{})

    if attempted:
        m1,m2,m3=st.columns(3)
        m1.metric("Candidates scanned",stats.get("candidates",0))
        m2.metric("🇮🇳 India verified",stats.get("verified",0))
        m3.metric("🔥 Viral / Rising shown",stats.get("viral",0))

        if not df.empty:
            show=df.copy()
            show["India Match"]="🇮🇳 Verified"
            show["Age"]=show.age_hours.map(lambda h:f"{h}h" if h<48 else f"{h/24:.0f}d")
            show["Views"]=show.views.map(compact)
            show["Likes"]=show.likes.map(compact)
            show["Comments"]=show.comments.map(compact)
            show["Views/hr"]=show.velocity.map(compact)
            show["Engagement"]=show.engagement.map(lambda x:f"{x:.2f}%")
            show["Momentum Score"]=show.score.map(lambda x:f"{x:.1f}")
            st.dataframe(
                show[["India Match","creator","caption","Age","Views","Likes","Comments","Views/hr","Engagement","Momentum Score","url"]],
                use_container_width=True,hide_index=True,
                column_config={
                    "creator":"Creator","caption":"Content",
                    "url":st.column_config.LinkColumn("Instagram",display_text="Open Reel")
                }
            )
        else:
            st.warning("No strictly India-verified viral/rising Reels survived this scan. This does not mean none exist; Instagram does not expose country-of-origin for every creator. Try a larger candidate pool, longer freshness window, or broader niche.")

        with st.expander("India verification details"):
            ev=st.session_state.get("india_v17_evidence",pd.DataFrame())
            if not ev.empty:
                st.dataframe(ev,use_container_width=True,hide_index=True)
            else:
                st.caption("No profile verification records available.")

        st.caption(f"Discovery query: {st.session_state.get('india_v17_query','—')} · India is confirmed from creator account country metadata, not merely from the word 'India' in the search query.")

st.divider()
st.caption("V1.7 • Popular, Trending Now and Niche Intelligence are intentionally separate. Viral Score is an internal heuristic based on view velocity, engagement and freshness; it is not an Instagram-provided metric.")
