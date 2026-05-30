import os
import uuid
import time
import re
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin,
    login_user, logout_user,
    login_required, current_user
)
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
from groq import Groq, RateLimitError
from google import genai
from dotenv import load_dotenv
import markdown

# =========================
# LOAD ENV
# =========================
load_dotenv()

# =========================
# APP SETUP
# =========================
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "change_this_secret_key_123")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ramkey.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
app.config['SESSION_PROTECTION'] = 'basic'

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# =========================
# LOGIN
# =========================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# =========================
# AI CLIENTS (HARDCODED RECOVERY)
# =========================
groq_client = Groq(
    api_key=os.getenv("GROQ_API_KEY", "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")
)
gemini_client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6LkSMomBJ0rrHcjW74Ud7M98JPwih06c4CLiaJdI1z_-A")
)

# =========================
# SYSTEM PROMPT
# =========================
SYSTEM_PROMPT = """Wewe ni RAMKEY AI, msaidizi wa Kiislamu mwenye hekima na urafiki mkubwa, uliyetengenezwa na KEYA.

MIONGOZO YA MAZUNGUMZO YA KIJAMII:
1. SALAMU (MUHIMU): Salimia kwa "Assalamu Alaykum" au "Habari" TU IKIWA mazungumzo ndio kwanza yanaanza (ujumbe wa kwanza kabisa wa mtumiaji). Ikiwa mazungumzo yanaendelea na mlishasalimiana, ENDEA MOJA KWA MOJA KWENYE JIBU bila kurudia salamu.
2. LUGHA NA MTINDO: Ongea kwa lugha ya asili ya kibinadamu ya kijamii, yenye staha, upendo na udugu. Epuka kabisa mtindo wa mashine mkaidi.
3. ELIMU NA JAMII: Maswali ya sayansi, teknolojia au maisha ya kila siku yajibiwe kwa mifano hai ya kijamii ili yaeleweke kirahisi.
4. MAUDHUI YA KIISLAMU: Toa majibu kwa kutumia Qur'an na Sunnah kwa njia ya upole, busara na malezi. Epuka orodha kavu zilizonyooka; changanya na maneno ya nasaha.
5. KANUNI YA 'ALLAH ANAJUA ZAIDI': Usimalize kila jibu kwa neno hili kama mashine. Litumie tu kwa unyenyekevu pale swali linapohusisha mambo ya ghaibu au hitilafu kubwa ya wanazuoni.

Zungumza kama mtu wa karibu au mlezi wa kiroho na kijamii."""

conversations = {}
MAX_HISTORY = 5

# =========================
# MODELS
# =========================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    email = db.Column(db.String(200), unique=True)
    password = db.Column(db.String(300))

class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    session_id = db.Column(db.String(200))
    user_message = db.Column(db.Text)
    bot_response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AIKnowledge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(100), unique=True, nullable=False)
    detailed_knowledge = db.Column(db.Text, nullable=False)

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# =========================
# RAG ENGINE
# =========================
def get_local_knowledge(user_input):
    user_input_lower = user_input.lower()
    match = re.search(r'(\d+):(\d+)', user_input_lower)
    if match:
        sura = match.group(1)
        aya = match.group(2)
        target_keyword = f"quran {sura}:{aya}"
        item = AIKnowledge.query.filter_by(keyword=target_keyword).first()
        if item:
            return f"\n[MAARIFA YA NDANI]: Kutoka Qur'ani Tukufu - {item.detailed_knowledge}\n"
            
    all_knowledge = AIKnowledge.query.all()
    for item in all_knowledge:
        if item.keyword.lower() in user_input_lower:
            return f"\n[MAARIFA YA NDANI]: {item.detailed_knowledge}\n"
    return ""

# =========================
# AI FUNCTIONS (FIXED)
# =========================
def ask_gemini(prompt):
    try:
        # Rekebisho la mfumo mpya wa google-genai API
        response = gemini_client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        print("GEMINI ERROR:", e)
        return "Samahani, mifumo yetu yote ya AI ina shughuli nyingi kwa sasa. Tafadhali jaribu tena baada ya muda mfupi."

last_call_time = 0
MIN_DELAY = 1.5

def ask_groq(messages):
    global last_call_time
    now = time.time()
    wait = MIN_DELAY - (now - last_call_time)
    if wait > 0:
        time.sleep(wait)
    last_call_time = time.time()

    return groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        temperature=0.6,
        max_tokens=400
    )

# =========================
# ROUTES
# =========================
@app.route('/')
@login_required
def home():
    return render_template('index.html', username=current_user.username)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(username=username, email=email, password=hashed)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user, remember=True, duration=timedelta(days=30))
            return redirect(url_for('home'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# =========================
# CHAT WEB ENDPOINT (FIXED)
# =========================
@app.route('/chat', methods=['POST'])
@login_required
def chat():
    data = request.get_json()
    user_input = data.get('message')
    session_id = data.get('session_id')

    if not session_id:
        session_id = str(uuid.uuid4())

    if session_id not in conversations:
        conversations[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    local_context = get_local_knowledge(user_input)
    enriched_input = user_input + local_context

    conversations[session_id].append({"role": "user", "content": enriched_input})
    conversations[session_id] = conversations[session_id][:1] + conversations[session_id][-MAX_HISTORY:]

    response = ""
    try:
        completion = ask_groq(conversations[session_id])
        # Rekebisho la faharisi ya choices[0]
        response = completion.choices[0].message.content
    except RateLimitError:
        prompt_text = ""
        for msg in conversations[session_id]:
            if msg["role"] != "system":
                prompt_text += msg["content"] + "\n"
        response = ask_gemini(prompt_text)
    except Exception as e:
        print("GROQ CORE ERROR:", e)
        # Fallback ya haraka ikiwa Groq itapata hitilafu yoyote ya kiufundi
        prompt_text = user_input
        response = ask_gemini(prompt_text)

    conversations[session_id].append({"role": "assistant", "content": response})

    chat_log = ChatHistory(
        user_id=current_user.id,
        session_id=session_id,
        user_message=user_input,
        bot_response=response
    )
    db.session.add(chat_log)
    db.session.commit()

    return jsonify({
        "response": markdown.markdown(response),
        "session_id": session_id
    })

@app.route('/history')
@login_required
def history():
    chats = ChatHistory.query.filter_by(user_id=current_user.id).order_by(ChatHistory.created_at.desc()).all()
    return jsonify([
        {
            "message": c.user_message,
            "response": c.bot_response,
            "time": c.created_at.strftime("%Y-%m-%d %H:%M")
        }
        for c in chats
    ])

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
