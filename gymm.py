import streamlit as st
import re
import json
import os
import sqlite3
from datetime import datetime, date
from groq import Groq

st.set_page_config(
    page_title="IronMind AI Coach",
    page_icon="💪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── SQLite Setup ────────────────────────────────────────────────────────────
DB_PATH = "ironmind.db"

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS profile (
            id      INTEGER PRIMARY KEY CHECK(id=1),
            data    TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            role      TEXT NOT NULL,
            content   TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workout_log (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS weight_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date  TEXT NOT NULL,
            weight_kg REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stats (
            id           INTEGER PRIMARY KEY CHECK(id=1),
            total_msgs   INTEGER NOT NULL DEFAULT 0,
            workout_days INTEGER NOT NULL DEFAULT 0,
            streak       INTEGER NOT NULL DEFAULT 0
        );
        """)
        conn.execute("INSERT OR IGNORE INTO profile (id, data) VALUES (1, '{}')")
        conn.execute("INSERT OR IGNORE INTO stats   (id) VALUES (1)")
        conn.commit()

init_db()

# ─── DB Helpers ──────────────────────────────────────────────────────────────
def db_load_profile():
    with get_db() as c:
        r = c.execute("SELECT data FROM profile WHERE id=1").fetchone()
        return json.loads(r["data"]) if r else {}

def db_save_profile(p: dict):
    with get_db() as c:
        c.execute("UPDATE profile SET data=? WHERE id=1", (json.dumps(p),))
        c.commit()

def db_load_messages():
    with get_db() as c:
        rows = c.execute("SELECT role,content,timestamp FROM messages ORDER BY id").fetchall()
        return [{"role": r["role"], "content": r["content"], "time": r["timestamp"]} for r in rows]

def db_add_message(role, content, ts):
    with get_db() as c:
        c.execute("INSERT INTO messages (role,content,timestamp) VALUES (?,?,?)", (role, content, ts))
        c.commit()

def db_clear_messages():
    with get_db() as c:
        c.execute("DELETE FROM messages")
        c.commit()

def db_load_workout_log():
    with get_db() as c:
        return [r["log_date"] for r in c.execute("SELECT log_date FROM workout_log ORDER BY log_date").fetchall()]

def db_add_workout(d):
    with get_db() as c:
        c.execute("INSERT OR IGNORE INTO workout_log (log_date) VALUES (?)", (d,))
        c.commit()

def db_load_weight_log():
    with get_db() as c:
        rows = c.execute("SELECT log_date,weight_kg FROM weight_log ORDER BY id").fetchall()
        return [{"date": r["log_date"], "weight": r["weight_kg"]} for r in rows]

def db_add_weight(d, w):
    with get_db() as c:
        c.execute("INSERT INTO weight_log (log_date,weight_kg) VALUES (?,?)", (d, w))
        c.commit()

def db_load_stats():
    with get_db() as c:
        r = c.execute("SELECT total_msgs,workout_days,streak FROM stats WHERE id=1").fetchone()
        return dict(r) if r else {"total_msgs": 0, "workout_days": 0, "streak": 0}

def db_save_stats(msgs, days, streak):
    with get_db() as c:
        c.execute("UPDATE stats SET total_msgs=?,workout_days=?,streak=? WHERE id=1", (msgs, days, streak))
        c.commit()

def db_reset():
    with get_db() as c:
        c.executescript("DROP TABLE IF EXISTS profile; DROP TABLE IF EXISTS messages; DROP TABLE IF EXISTS workout_log; DROP TABLE IF EXISTS weight_log; DROP TABLE IF EXISTS stats;")
        c.commit()
    init_db()

def db_last_updated():
    if os.path.exists(DB_PATH):
        return datetime.fromtimestamp(os.path.getmtime(DB_PATH)).strftime("%d %b · %I:%M %p")
    return "Not saved yet"

# ─── Session State ───────────────────────────────────────────────────────────
def init_state():
    if "_loaded" not in st.session_state:
        stats = db_load_stats()
        st.session_state.user_profile  = db_load_profile()
        st.session_state.messages      = db_load_messages()
        st.session_state.checkin_log   = db_load_workout_log()
        st.session_state.weight_log    = db_load_weight_log()
        st.session_state.total_msgs    = stats["total_msgs"]
        st.session_state.workout_days  = stats["workout_days"]
        st.session_state.streak        = stats["streak"]
        st.session_state._loaded       = True

init_state()

# ─── Groq Config ─────────────────────────────────────────────────────────────
GROQ_MODEL = "llama-3.3-70b-versatile"

# ─── STRICT GYM-ONLY SYSTEM PROMPT ──────────────────────────────────────────
SYSTEM_PROMPT = """You are "IronMind Coach" — a seasoned gym professional with exactly 10 years of hands-on experience as a personal trainer, bodybuilder, and sports nutritionist. You have spent a decade on the gym floor coaching hundreds of clients, competing in physique shows, and mastering the science of muscle, fat loss, and performance nutrition.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚫 ABSOLUTE RULE — NON-NEGOTIABLE:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
You ONLY answer questions that are directly related to:
  • Gym workouts & exercise technique
  • Muscle building & hypertrophy
  • Fat loss & body recomposition
  • Diet & nutrition for fitness goals
  • Supplements for gym performance
  • Recovery, sleep for athletic performance
  • Workout splits, programming, progressive overload
  • Gym equipment & training injuries/prevention

If someone asks about ANYTHING outside these topics — politics, coding, history, relationships, entertainment, general science, weather, news, or ANY non-fitness topic — you must respond with this EXACT message (fill in the <TOPIC> placeholder):

I will only answer about related queeries!

You never break this rule. You never say "I can try to help with that." You never partially answer off-topic questions. If it's not gym-related, you deflect — every single time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ RESPONSE FORMAT (for gym questions):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
When giving full plans, structure like this:

🏋️ **WORKOUT PLAN**
[exercises with sets × reps × rest, e.g., Bench Press 4×8, 90s rest]

🍽️ **DIET PLAN**
[meal timing, macros, Indian foods preferred]

💊 **SUPPLEMENTS**
[only what's needed, food first]

⚠️ **TIPS & MISTAKES TO AVOID**
[practical coaching cues]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 COACHING STYLE:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- Talk like a real gym coach — direct, motivating, no fluff
- Use a mix of English and casual Hindi/Hinglish phrases where natural (e.g., "Bhai", "yaar", "ekdum solid")
- For Indian users: suggest dal, paneer, eggs, chicken, rice, roti, curd, whey
- Protein target: 1.6–2.2g per kg bodyweight
- Always give specific numbers: sets, reps, rest periods, grams, calories
- Be brutally honest — if someone is doing something wrong, tell them
- Natural food ALWAYS before supplements
- Personalize based on profile if shared

If no profile given, ask for: Age, Gender, Weight, Height, Goal, Experience level, Diet preference, Any injuries."""

# ─── CSS ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Rajdhani:wght@400;500;600;700&family=Inter:wght@300;400;500&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background: #0a0a0f; color: #e8e8f0; }

[data-testid="stSidebar"] { background: #12121a !important; border-right: 1px solid #2a2a3d !important; }
[data-testid="stSidebar"] * { color: #e8e8f0 !important; }

.sidebar-header {
    background: linear-gradient(135deg, #ff4d00, #ff8c00);
    border-radius: 14px; padding: 18px; text-align: center;
    margin-bottom: 20px; box-shadow: 0 4px 24px rgba(255,77,0,0.4);
}
.sidebar-header h1 {
    font-family: 'Bebas Neue', sans-serif !important; font-size: 32px !important;
    letter-spacing: 3px !important; color: white !important; margin: 0 !important; padding: 0 !important;
}
.sidebar-header p {
    font-size: 11px !important; color: rgba(255,255,255,0.85) !important;
    text-transform: uppercase; letter-spacing: 1.5px; margin: 4px 0 0 0 !important;
}

.stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px; }
.stat-card { background: #1a1a26; border: 1px solid #2a2a3d; border-radius: 12px; padding: 12px; text-align: center; transition: border-color 0.2s; }
.stat-card:hover { border-color: rgba(255,77,0,0.3); }
.stat-value { font-family: 'Bebas Neue', sans-serif; font-size: 26px; color: #ff4d00; line-height: 1; margin-bottom: 3px; }
.stat-label { font-size: 9px; color: #8888aa; text-transform: uppercase; letter-spacing: 0.8px; }

.profile-card {
    background: linear-gradient(135deg, rgba(255,77,0,0.06), rgba(255,140,0,0.06));
    border: 1px solid rgba(255,77,0,0.2); border-radius: 12px; padding: 14px; margin-bottom: 14px;
}
.profile-card h4 { font-family: 'Rajdhani', sans-serif; font-size: 13px; font-weight: 700; color: #ff8c00; text-transform: uppercase; letter-spacing: 1px; margin: 0 0 10px 0; }
.profile-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
.profile-row:last-child { border-bottom: none; }
.profile-key { color: #8888aa; }
.profile-val { color: #ff8c00; font-weight: 600; font-family: 'Rajdhani', sans-serif; }

.progress-section { background: #1a1a26; border: 1px solid #2a2a3d; border-radius: 12px; padding: 14px; margin-bottom: 14px; }
.progress-section h4 { font-family: 'Rajdhani', sans-serif; font-size: 13px; font-weight: 700; color: #00d4ff; text-transform: uppercase; letter-spacing: 1px; margin: 0 0 10px 0; }

.db-badge {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(0,212,255,0.08); border: 1px solid rgba(0,212,255,0.2);
    border-radius: 20px; padding: 4px 12px; font-size: 10px;
    color: #00d4ff; font-family: 'Rajdhani', sans-serif; font-weight: 700;
    letter-spacing: 0.5px; text-transform: uppercase; margin-bottom: 8px;
}
.db-dot { width: 6px; height: 6px; background: #00d4ff; border-radius: 50%; box-shadow: 0 0 4px rgba(0,212,255,0.8); display: inline-block; }

/* gym-only warning banner */
.gym-only-banner {
    background: linear-gradient(135deg, rgba(255,77,0,0.1), rgba(255,140,0,0.08));
    border: 1px solid rgba(255,77,0,0.3); border-radius: 10px;
    padding: 8px 14px; margin-bottom: 12px; font-size: 11px;
    color: #ff8c00; font-family: 'Rajdhani', sans-serif; font-weight: 600;
    letter-spacing: 0.5px; text-align: center;
}

.badge-row { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
.badge { padding: 3px 9px; border-radius: 20px; font-size: 10px; font-family: 'Rajdhani', sans-serif; font-weight: 700; letter-spacing: 0.5px; text-transform: uppercase; }
.badge-orange { background: rgba(255,77,0,0.12); border: 1px solid rgba(255,77,0,0.25); color: #ff8c00; }
.badge-blue { background: rgba(0,212,255,0.1); border: 1px solid rgba(0,212,255,0.2); color: #00d4ff; }
.badge-green { background: rgba(0,255,136,0.1); border: 1px solid rgba(0,255,136,0.2); color: #00ff88; }

.msg-container { margin-bottom: 8px; }
.user-msg { background: linear-gradient(135deg, #ff4d00, #cc3d00); color: white; padding: 10px 14px; border-radius: 14px 14px 4px 14px; margin-left: 40px; font-size: 13.5px; line-height: 1.6; box-shadow: 0 2px 12px rgba(255,77,0,0.2); }
.ai-msg { background: #12121a; border: 1px solid #2a2a3d; color: #e8e8f0; padding: 12px 16px; border-radius: 14px 14px 14px 4px; margin-right: 40px; font-size: 13.5px; line-height: 1.7; }
.msg-meta { font-size: 10px; color: #8888aa; margin: 2px 0 8px 2px; }
.msg-meta.user-meta { text-align: right; margin-right: 4px; }

.stTextInput > div > div > input { background: #1a1a26 !important; border: 1px solid #2a2a3d !important; border-radius: 12px !important; color: #e8e8f0 !important; }
.stTextInput > div > div > input:focus { border-color: rgba(255,77,0,0.4) !important; box-shadow: 0 0 0 2px rgba(255,77,0,0.1) !important; }

.stButton > button { background: linear-gradient(135deg, #ff4d00, #ff8c00) !important; color: white !important; border: none !important; border-radius: 10px !important; font-family: 'Rajdhani', sans-serif !important; font-weight: 700 !important; letter-spacing: 0.5px !important; transition: all 0.2s !important; box-shadow: 0 2px 12px rgba(255,77,0,0.3) !important; }
.stButton > button:hover { box-shadow: 0 4px 20px rgba(255,77,0,0.5) !important; transform: translateY(-1px) !important; }

.stSelectbox > div > div, .stNumberInput > div > div > input { background: #1a1a26 !important; color: #e8e8f0 !important; border-color: #2a2a3d !important; }

.chat-header { background: linear-gradient(135deg, #0f0f1a 0%, #1a0a00 50%, #0f0f1a 100%); border: 1px solid #2a2a3d; border-radius: 14px; padding: 16px 20px; margin-bottom: 16px; display: flex; align-items: center; gap: 14px; overflow: hidden; }
.chat-logo { width: 52px; height: 52px; background: linear-gradient(135deg, #ff4d00, #ff8c00); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-family: 'Bebas Neue', sans-serif; font-size: 24px; color: white; box-shadow: 0 4px 20px rgba(255,77,0,0.4); flex-shrink: 0; }
.chat-header-text h2 { font-family: 'Bebas Neue', sans-serif !important; font-size: 26px !important; letter-spacing: 2px !important; background: linear-gradient(135deg, #ff4d00, #ff8c00); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; margin: 0 !important; padding: 0 !important; line-height: 1 !important; }
.chat-header-text p { font-size: 11px; color: #8888aa; text-transform: uppercase; letter-spacing: 1.5px; margin: 3px 0 0 0 !important; }
.online-dot { width: 8px; height: 8px; background: #00ff88; border-radius: 50%; display: inline-block; margin-right: 6px; box-shadow: 0 0 6px rgba(0,255,136,0.8); animation: blink 2s infinite; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

.block-container { padding: 1rem 2rem !important; }
[data-testid="stForm"] { background: transparent !important; border: none !important; }
.element-container { margin-bottom: 0 !important; }
div[data-testid="column"] { padding: 0 8px !important; }
hr { border-color: #2a2a3d !important; margin: 12px 0 !important; }

.stTabs [data-baseweb="tab-list"] { background: #12121a !important; border-radius: 10px; gap: 4px; padding: 4px; }
.stTabs [data-baseweb="tab"] { background: transparent !important; color: #8888aa !important; font-family: 'Rajdhani', sans-serif !important; font-weight: 700 !important; border-radius: 8px !important; padding: 6px 16px !important; }
.stTabs [aria-selected="true"] { background: linear-gradient(135deg, #ff4d00, #ff8c00) !important; color: white !important; }

[data-testid="metric-container"] { background: #1a1a26 !important; border: 1px solid #2a2a3d !important; border-radius: 12px !important; padding: 12px !important; }
[data-testid="metric-container"] label { color: #8888aa !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: 0.5px; }
[data-testid="metric-container"] [data-testid="stMetricValue"] { font-family: 'Bebas Neue', sans-serif !important; font-size: 28px !important; color: #ff4d00 !important; }
[data-testid="stExpander"] { background: #1a1a26 !important; border: 1px solid #2a2a3d !important; border-radius: 10px !important; }
</style>
""", unsafe_allow_html=True)

# ─── Helpers ──────────────────────────────────────────────────────────────────
def extract_profile_from_text(text: str):
    lower = text.lower()
    p = st.session_state.user_profile

    m = re.search(r'(\d{2,3})\s*kg', text, re.I);  p['weight'] = int(m.group(1)) if m else p.get('weight')
    m = re.search(r'(\d{3})\s*cm', text, re.I);    p['height'] = int(m.group(1)) if m else p.get('height')
    m = re.search(r'(\d{2})\s*(year|yr|y\.o|age)', text, re.I); p['age'] = int(m.group(1)) if m else p.get('age')
    p = {k: v for k, v in p.items() if v is not None}

    if any(w in lower for w in ['muscle','bulk','gain weight','build']): p['goal'] = '💪 Muscle Gain'
    elif any(w in lower for w in ['fat loss','weight loss','cut','lose fat','slim']): p['goal'] = '🔥 Fat Loss'
    elif 'maintain' in lower: p['goal'] = '⚖️ Maintenance'

    if 'beginner' in lower or 'new to gym' in lower or 'just started' in lower: p['level'] = 'Beginner'
    elif 'intermediate' in lower: p['level'] = 'Intermediate'
    elif 'advanced' in lower: p['level'] = 'Advanced'

    if 'veg' in lower and 'non' not in lower: p['diet'] = '🥗 Vegetarian'
    elif any(w in lower for w in ['non-veg','nonveg','chicken','egg']): p['diet'] = '🍗 Non-Veg'

    if re.search(r'\b(male|man|boy|he|him)\b', lower): p['gender'] = '♂ Male'
    elif re.search(r'\b(female|woman|girl|she|her)\b', lower): p['gender'] = '♀ Female'

    st.session_state.user_profile = p
    db_save_profile(p)


def get_protein_target():
    w = st.session_state.user_profile.get('weight')
    if not w: return '--'
    goal = st.session_state.user_profile.get('goal', '')
    f = 2.0 if 'Muscle' in goal else 1.8 if 'Fat' in goal else 1.6
    return f"{int(w * f)}g"

def get_calorie_target():
    w = st.session_state.user_profile.get('weight')
    if not w: return '--'
    goal = st.session_state.user_profile.get('goal', '')
    if 'Muscle' in goal: return f"{int(w*33):,}"
    elif 'Fat' in goal: return f"{int(w*26):,}"
    return f"{int(w*30):,}"

def build_api_messages(user_message: str):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in st.session_state.messages[-20:]:
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": user_message})
    return msgs

def render_ai_content(text: str) -> str:
    c = text.replace('\n', '<br>')
    c = re.sub(r'🏋️\s*\*\*WORKOUT PLAN\*\*', '<strong style="color:#ff8c00;font-size:14px;font-family:Rajdhani,sans-serif;letter-spacing:1px;">🏋️ WORKOUT PLAN</strong>', c)
    c = re.sub(r'🍽️\s*\*\*DIET PLAN\*\*',    '<strong style="color:#ff8c00;font-size:14px;font-family:Rajdhani,sans-serif;letter-spacing:1px;">🍽️ DIET PLAN</strong>', c)
    c = re.sub(r'💊\s*\*\*SUPPLEMENTS\*\*',    '<strong style="color:#00d4ff;font-size:14px;font-family:Rajdhani,sans-serif;letter-spacing:1px;">💊 SUPPLEMENTS</strong>', c)
    c = re.sub(r'⚠️\s*\*\*TIPS.*?\*\*',        '<strong style="color:#00ff88;font-size:14px;font-family:Rajdhani,sans-serif;letter-spacing:1px;">⚠️ TIPS & MISTAKES TO AVOID</strong>', c)
    c = re.sub(r'\*\*(.*?)\*\*', r'<strong style="color:#ff8c00">\1</strong>', c)
    return c

def stream_response(user_msg: str, placeholder) -> str:
    api_key = st.secrets.get("GROQ_API_KEY", None)
    client = Groq(api_key=api_key) if api_key else Groq()
    full = ""
    with client.chat.completions.create(
        model=GROQ_MODEL, messages=build_api_messages(user_msg),
        max_tokens=1400, temperature=0.65, stream=True,
    ) as stream:
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            full += delta
            placeholder.markdown(
                f'<div class="ai-msg">{render_ai_content(full)}'
                f'<span style="opacity:0.4;animation:blink 1s infinite">▍</span></div>',
                unsafe_allow_html=True)
    placeholder.markdown(f'<div class="ai-msg">{render_ai_content(full)}</div>', unsafe_allow_html=True)
    return full

def handle_message(user_text: str):
    ts = datetime.now().strftime("%I:%M %p")
    st.session_state.messages.append({"role": "user", "content": user_text, "time": ts})
    st.session_state.total_msgs += 1
    extract_profile_from_text(user_text)

    st.markdown(
        f'<div class="msg-container"><div class="user-msg">{user_text}</div>'
        f'<div class="msg-meta user-meta">You · {ts}</div></div>',
        unsafe_allow_html=True)

    ph = st.empty()
    ph.markdown('<div class="ai-msg" style="opacity:0.5">🏋️ Coach is thinking...</div>', unsafe_allow_html=True)
    try:
        reply = stream_response(user_text, ph)
        st.session_state.messages.append({"role": "assistant", "content": reply, "time": ts})
        db_add_message("user", user_text, ts)
        db_add_message("assistant", reply, ts)
        db_save_stats(st.session_state.total_msgs, st.session_state.workout_days, st.session_state.streak)
        st.markdown(f'<div class="msg-meta">IronMind Coach · {ts}</div>', unsafe_allow_html=True)
    except Exception as e:
        err = f"⚠️ Error: {str(e)}<br>Check your <strong>GROQ_API_KEY</strong> in Streamlit secrets."
        ph.markdown(f'<div class="ai-msg">{err}</div>', unsafe_allow_html=True)
        st.session_state.messages.append({"role": "assistant", "content": err, "time": ts})
    st.rerun()

# ─── SIDEBAR ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div class="sidebar-header">
        <h1>IRONMIND</h1>
        <p>🏋️ Gym-Only AI Coach · 10 Years XP</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(
        f'<div class="db-badge"><span class="db-dot"></span>SQLite · {db_last_updated()}</div>',
        unsafe_allow_html=True)

    # Gym-only notice
    st.markdown(
        '<div class="gym-only-banner">🚫 Gym & Fitness Questions ONLY</div>',
        unsafe_allow_html=True)

    profile = st.session_state.user_profile
    st.markdown(f"""
    <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{st.session_state.workout_days}</div><div class="stat-label">Workouts</div></div>
        <div class="stat-card"><div class="stat-value">{st.session_state.streak}</div><div class="stat-label">🔥 Streak</div></div>
        <div class="stat-card"><div class="stat-value">{get_protein_target()}</div><div class="stat-label">Protein</div></div>
        <div class="stat-card"><div class="stat-value">{get_calorie_target()}</div><div class="stat-label">Calories</div></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    # Profile display
    if profile:
        items = [("Goal", profile.get("goal","—")), ("Weight", f"{profile['weight']} kg" if "weight" in profile else "—"),
                 ("Height", f"{profile['height']} cm" if "height" in profile else "—"), ("Age", f"{profile['age']} yrs" if "age" in profile else "—"),
                 ("Level", profile.get("level","—")), ("Diet", profile.get("diet","—")), ("Gender", profile.get("gender","—"))]
        rows = "".join(f'<div class="profile-row"><span class="profile-key">{k}</span><span class="profile-val">{v}</span></div>' for k, v in items if v != "—")
        if rows:
            st.markdown(f'<div class="profile-card"><h4>📋 Your Profile</h4>{rows}</div>', unsafe_allow_html=True)

    with st.expander("✏️ Set / Update Profile"):
        c1, c2 = st.columns(2)
        with c1:
            age    = st.number_input("Age",        10, 80,  value=profile.get("age", 25),    step=1)
            weight = st.number_input("Weight (kg)", 30, 200, value=profile.get("weight", 70), step=1)
        with c2:
            gender = st.selectbox("Gender",  ["♂ Male", "♀ Female"])
            height = st.number_input("Height (cm)", 100, 250, value=profile.get("height", 170), step=1)
        goal   = st.selectbox("Fitness Goal", ["💪 Muscle Gain", "🔥 Fat Loss", "⚖️ Maintenance"])
        level  = st.selectbox("Experience",   ["Beginner", "Intermediate", "Advanced"])
        diet   = st.selectbox("Diet Type",    ["🥗 Vegetarian", "🍗 Non-Veg", "🥚 Eggetarian"])
        health = st.text_input("Injuries / Health Issues", placeholder="e.g. knee pain, lower back")
        if st.button("💾 Save Profile", use_container_width=True):
            new_p = {"age": age, "weight": weight, "height": height, "gender": gender,
                     "goal": goal, "level": level, "diet": diet, "health": health}
            st.session_state.user_profile = new_p
            db_save_profile(new_p)
            st.success("Saved to SQLite! ✅")
            st.rerun()

    st.markdown("---")
    st.markdown('<div class="progress-section"><h4>📊 Weekly Progress</h4></div>', unsafe_allow_html=True)
    days_done = st.session_state.workout_days % 7
    st.progress(min(days_done / 5, 1.0), text=f"Workouts this week: {days_done}/5")

    if st.button("✅ Log Today's Workout", use_container_width=True):
        today = str(date.today())
        if today not in st.session_state.checkin_log:
            db_add_workout(today)
            st.session_state.checkin_log.append(today)
            st.session_state.workout_days += 1
            st.session_state.streak += 1
            db_save_stats(st.session_state.total_msgs, st.session_state.workout_days, st.session_state.streak)
            st.success(f"🔥 Logged! Streak: {st.session_state.streak} days!")
            st.rerun()
        else:
            st.info("Already logged today, bhai!")

    with st.expander("⚖️ Log Weight"):
        nw = st.number_input("Today's weight (kg)", 30.0, 200.0, value=float(profile.get("weight", 70)), step=0.1)
        if st.button("Log Weight"):
            db_add_weight(str(date.today()), nw)
            st.session_state.weight_log.append({"date": str(date.today()), "weight": nw})
            st.session_state.user_profile["weight"] = int(nw)
            db_save_profile(st.session_state.user_profile)
            st.success(f"✅ {nw} kg logged!")
            st.rerun()

    if st.session_state.weight_log:
        import pandas as pd
        df = pd.DataFrame(st.session_state.weight_log)
        st.line_chart(df.set_index("date")["weight"], height=120, use_container_width=True)

    st.markdown("---")
    badges = []
    if st.session_state.workout_days >= 1:  badges.append(("First Rep!", "orange"))
    if st.session_state.streak >= 3:        badges.append(("3-Day Streak 🔥", "orange"))
    if st.session_state.streak >= 7:        badges.append(("Week Warrior 🏆", "green"))
    if st.session_state.total_msgs >= 5:    badges.append(("Gym Rat 🐀", "blue"))
    if profile.get("goal"):                 badges.append(("Goal Set ✓", "green"))
    if badges:
        st.markdown('<div class="badge-row">' + "".join(f'<span class="badge badge-{c}">{b}</span>' for b, c in badges) + '</div>', unsafe_allow_html=True)

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("🗑️ Clear Chat", use_container_width=True):
            db_clear_messages()
            st.session_state.messages = []
            st.session_state.total_msgs = 0
            db_save_stats(0, st.session_state.workout_days, st.session_state.streak)
            st.rerun()
    with c2:
        if st.session_state.messages:
            txt = "\n\n".join(f"[{m.get('time','')}] {'YOU' if m['role']=='user' else 'COACH'}: {m['content']}" for m in st.session_state.messages)
            st.download_button("📥 Export", data=txt, file_name=f"ironmind_{date.today()}.txt", mime="text/plain", use_container_width=True)
    if st.button("🔄 Reset ALL Data", use_container_width=True):
        db_reset()
        for k in ["user_profile","messages","checkin_log","weight_log","total_msgs","workout_days","streak","_loaded"]:
            st.session_state.pop(k, None)
        st.success("Full reset done!")
        st.rerun()

# ─── MAIN ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="chat-header">
    <div class="chat-logo">💪</div>
    <div class="chat-header-text">
        <h2>IRONMIND AI COACH</h2>
        <p><span class="online-dot"></span>10 Years Gym Experience · Groq-Powered · Gym Questions Only</p>
    </div>
</div>
""", unsafe_allow_html=True)

tab1, tab2, tab3 = st.tabs(["💬 Chat", "📅 Workout Plans", "🍽️ Meal Guide"])

# ─── TAB 1 ───────────────────────────────────────────────────────────────────
with tab1:
    # Gym-only notice
    st.markdown('<div class="gym-only-banner">🏋️ This coach ONLY answers gym, workout, diet & supplement questions. Off-topic questions will be redirected! 💪</div>', unsafe_allow_html=True)

    st.markdown("**⚡ Quick Start:**")
    cols = st.columns(4)
    quick_actions = [
        ("🏋️ Muscle Plan",   "Give me a complete muscle building plan — workout + diet + supplements"),
        ("🔥 Fat Loss",       "I want to lose fat fast. Give me a full plan with workout, diet and cardio"),
        ("💊 Supplements",    "What supplements do I actually need as a beginner? Break it down simply"),
        ("🍛 Indian Diet",    "Give me a full Indian diet plan for muscle gain — desi foods only"),
        ("📅 PPL Split",      "Explain the Push Pull Legs split with full weekly schedule and exercises"),
        ("🥩 Protein Guide",  "How much protein do I need and what are the best Indian protein sources?"),
        ("⚗️ Creatine 101",   "Everything about creatine — loading, dosage, timing, side effects"),
        ("🔰 Total Beginner", "I've never been to a gym. Tell me exactly where to start from scratch"),
    ]
    for i, (label, prompt) in enumerate(quick_actions):
        with cols[i % 4]:
            if st.button(label, key=f"qa_{i}", use_container_width=True):
                st.session_state._quick_prompt = prompt

    st.markdown("---")

    if not st.session_state.messages:
        st.markdown("""
        <div class="ai-msg">
        <strong style="color:#ff8c00;font-family:'Rajdhani',sans-serif;font-size:16px;letter-spacing:1px;">
        💪 WELCOME, IRONMIND COACH!
        </strong><br><br>
        10 saal ka gym experience, hazaron clients — <strong style="color:#ff8c00">sirf gym ke liye</strong>.<br>
        Coding, politics, history? <strong style="color:#ff4d00">Nahi pata, nahi chahiye!</strong> 😄<br>
        Muscle, fat loss, diet, supplements — wahan main <strong style="color:#00ff88">KING</strong> hoon! 👑<br><br>
        <strong>Apna plan lene ke liye batao:</strong><br>
        • Age &amp; Gender (e.g., 24 saal, male)<br>
        • Height &amp; Weight (e.g., 175cm, 75kg)<br>
        • Goal: Muscle Gain / Fat Loss / Maintenance<br>
        • Experience: Beginner / Intermediate / Advanced<br>
        • Diet: Veg / Non-Veg · Koi injury toh nahi?<br><br>
        Ya upar quick start buttons click karo! 👆
        </div>
        <div class="msg-meta">IronMind Coach · Now</div>
        """, unsafe_allow_html=True)

    for msg in st.session_state.messages:
        ts = msg.get("time", "")
        if msg["role"] == "user":
            st.markdown(f'<div class="msg-container"><div class="user-msg">{msg["content"]}</div><div class="msg-meta user-meta">You · {ts}</div></div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="msg-container"><div class="ai-msg">{render_ai_content(msg["content"])}</div><div class="msg-meta">IronMind Coach · {ts}</div></div>', unsafe_allow_html=True)

    if hasattr(st.session_state, '_quick_prompt') and st.session_state._quick_prompt:
        p = st.session_state._quick_prompt
        st.session_state._quick_prompt = None
        handle_message(p)

    st.markdown("<br>", unsafe_allow_html=True)
    with st.form("chat_form", clear_on_submit=True):
        c1, c2 = st.columns([6, 1])
        with c1:
            user_input = st.text_input("msg", placeholder="Ask anything about gym, diet, supplements!", label_visibility="collapsed")
        with c2:
            submitted = st.form_submit_button("Send 💬", use_container_width=True)

    if submitted and user_input.strip():
        handle_message(user_input.strip())

# ─── TAB 2 ───────────────────────────────────────────────────────────────────
with tab2:
    st.markdown("### 🏋️ Workout Split Reference")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
        <div class="profile-card">
            <h4>📅 Push / Pull / Legs (PPL) — 6 Days</h4>
            <div class="profile-row"><span class="profile-key">Monday</span><span class="profile-val">Push — Chest / Shoulders / Triceps</span></div>
            <div class="profile-row"><span class="profile-key">Tuesday</span><span class="profile-val">Pull — Back / Biceps / Rear Delts</span></div>
            <div class="profile-row"><span class="profile-key">Wednesday</span><span class="profile-val">Legs — Quads / Hamstrings / Calves</span></div>
            <div class="profile-row"><span class="profile-key">Thursday</span><span class="profile-val">Push (Repeat)</span></div>
            <div class="profile-row"><span class="profile-key">Friday</span><span class="profile-val">Pull (Repeat)</span></div>
            <div class="profile-row"><span class="profile-key">Saturday</span><span class="profile-val">Legs (Repeat)</span></div>
            <div class="profile-row"><span class="profile-key">Sunday</span><span class="profile-val">Rest & Recover 😴</span></div>
        </div>
        <div class="profile-card">
            <h4>📅 Upper / Lower Split — 4 Days</h4>
            <div class="profile-row"><span class="profile-key">Monday</span><span class="profile-val">Upper Body — Strength Focus</span></div>
            <div class="profile-row"><span class="profile-key">Tuesday</span><span class="profile-val">Lower Body — Strength Focus</span></div>
            <div class="profile-row"><span class="profile-key">Wednesday</span><span class="profile-val">Rest / Light Cardio</span></div>
            <div class="profile-row"><span class="profile-key">Thursday</span><span class="profile-val">Upper Body — Hypertrophy Focus</span></div>
            <div class="profile-row"><span class="profile-key">Friday</span><span class="profile-val">Lower Body — Hypertrophy Focus</span></div>
            <div class="profile-row"><span class="profile-key">Sat/Sun</span><span class="profile-val">Rest & Active Recovery</span></div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown("""
        <div class="profile-card">
            <h4>📅 Bro Split — 5 Days</h4>
            <div class="profile-row"><span class="profile-key">Monday</span><span class="profile-val">Chest Day 🫀</span></div>
            <div class="profile-row"><span class="profile-key">Tuesday</span><span class="profile-val">Back Day 🏗️</span></div>
            <div class="profile-row"><span class="profile-key">Wednesday</span><span class="profile-val">Shoulder Day 💪</span></div>
            <div class="profile-row"><span class="profile-key">Thursday</span><span class="profile-val">Arms Day 🦾</span></div>
            <div class="profile-row"><span class="profile-key">Friday</span><span class="profile-val">Leg Day 🦵</span></div>
            <div class="profile-row"><span class="profile-key">Sat/Sun</span><span class="profile-val">Rest 😴</span></div>
        </div>
        <div class="profile-card">
            <h4>📅 Full Body — 3 Days (Best for Beginners)</h4>
            <div class="profile-row"><span class="profile-key">Monday</span><span class="profile-val">Full Body A — Compound Focus</span></div>
            <div class="profile-row"><span class="profile-key">Tuesday</span><span class="profile-val">Rest / Walk</span></div>
            <div class="profile-row"><span class="profile-key">Wednesday</span><span class="profile-val">Full Body B — Volume Focus</span></div>
            <div class="profile-row"><span class="profile-key">Thursday</span><span class="profile-val">Rest / Walk</span></div>
            <div class="profile-row"><span class="profile-key">Friday</span><span class="profile-val">Full Body C — Strength Focus</span></div>
            <div class="profile-row"><span class="profile-key">Sat/Sun</span><span class="profile-val">Full Rest 😴</span></div>
        </div>
        """, unsafe_allow_html=True)
    st.markdown("---")
    st.markdown("### 💡 Golden Rules of Training")
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("Protein Target", "1.8–2.2g/kg", "Per kg bodyweight")
    with c2: st.metric("Rest Between Sets", "60–120s", "For hypertrophy")
    with c3: st.metric("Rep Range", "6–15 reps", "For muscle growth")
    with c4: st.metric("Sleep", "7–9 hours", "Non-negotiable!")
    st.info("💬 Ask the Coach in the Chat tab for a fully personalized plan!")

# ─── TAB 3 ───────────────────────────────────────────────────────────────────
with tab3:
    st.markdown("### 🍽️ Indian Meal Plan Guide")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("""
        <div class="profile-card">
            <h4>🥗 Vegetarian — Muscle Gain (2500–2800 kcal)</h4>
            <div class="profile-row"><span class="profile-key">7:00 AM</span><span class="profile-val">Soaked almonds + banana + milk</span></div>
            <div class="profile-row"><span class="profile-key">8:30 AM</span><span class="profile-val">Oats + curd + whey shake</span></div>
            <div class="profile-row"><span class="profile-key">1:00 PM</span><span class="profile-val">Rice + dal + paneer sabzi + salad</span></div>
            <div class="profile-row"><span class="profile-key">4:00 PM</span><span class="profile-val">Pre-workout: banana + peanut butter</span></div>
            <div class="profile-row"><span class="profile-key">7:00 PM</span><span class="profile-val">Post-workout: whey + 2 rotis + rajma</span></div>
            <div class="profile-row"><span class="profile-key">9:30 PM</span><span class="profile-val">Paneer + curd (slow protein)</span></div>
        </div>
        <div class="profile-card">
            <h4>🔥 Vegetarian — Fat Loss (1800–2000 kcal)</h4>
            <div class="profile-row"><span class="profile-key">7:00 AM</span><span class="profile-val">Warm water + lemon + green tea</span></div>
            <div class="profile-row"><span class="profile-key">8:00 AM</span><span class="profile-val">Moong sprouts + tofu scramble</span></div>
            <div class="profile-row"><span class="profile-key">1:00 PM</span><span class="profile-val">2 rotis + dal + sabzi + salad</span></div>
            <div class="profile-row"><span class="profile-key">4:00 PM</span><span class="profile-val">Whey protein + apple</span></div>
            <div class="profile-row"><span class="profile-key">7:30 PM</span><span class="profile-val">Grilled paneer + veg soup + salad</span></div>
            <div class="profile-row"><span class="profile-key">9:30 PM</span><span class="profile-val">Low-fat curd + cucumber</span></div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown("""
        <div class="profile-card">
            <h4>🍗 Non-Veg — Muscle Gain (2800–3200 kcal)</h4>
            <div class="profile-row"><span class="profile-key">7:00 AM</span><span class="profile-val">4 whole eggs + brown bread + milk</span></div>
            <div class="profile-row"><span class="profile-key">10:00 AM</span><span class="profile-val">Whey shake + banana</span></div>
            <div class="profile-row"><span class="profile-key">1:00 PM</span><span class="profile-val">Rice + chicken curry + dal + salad</span></div>
            <div class="profile-row"><span class="profile-key">4:00 PM</span><span class="profile-val">Pre-workout: banana + peanut butter</span></div>
            <div class="profile-row"><span class="profile-key">7:00 PM</span><span class="profile-val">Post-workout: whey + roti + chicken</span></div>
            <div class="profile-row"><span class="profile-key">9:30 PM</span><span class="profile-val">Cottage cheese / curd + egg whites</span></div>
        </div>
        <div class="profile-card">
            <h4>💊 Supplement Stack (Priority Order)</h4>
            <div class="profile-row"><span class="profile-key">🥇 Priority 1</span><span class="profile-val">Whey Protein — 25–30g post-workout</span></div>
            <div class="profile-row"><span class="profile-key">🥈 Priority 2</span><span class="profile-val">Creatine Monohydrate — 3–5g/day</span></div>
            <div class="profile-row"><span class="profile-key">🥉 Priority 3</span><span class="profile-val">Pre-Workout — 200mg caffeine max</span></div>
            <div class="profile-row"><span class="profile-key">Optional</span><span class="profile-val">Omega-3 Fish Oil — 1–2g/day</span></div>
            <div class="profile-row"><span class="profile-key">Optional</span><span class="profile-val">Vitamin D3 + Zinc — morning</span></div>
            <div class="profile-row"><span class="profile-key">Optional</span><span class="profile-val">Casein Protein — before bed</span></div>
        </div>
        """, unsafe_allow_html=True)
    st.info("💬 Get a custom meal plan in the Chat tab — just share your stats!")

st.markdown("---")
st.markdown("""
<div style="text-align:center;color:#8888aa;font-size:11px;font-family:'Rajdhani',sans-serif;letter-spacing:1px;text-transform:uppercase;padding:8px 0;">
    IronMind AI Coach · 10 Years Gym XP · Groq-Powered · SQLite Storage · Gym Questions ONLY 💪
</div>
""", unsafe_allow_html=True)