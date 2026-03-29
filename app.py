from flask import Flask, render_template, redirect, url_for, flash, send_file, abort, jsonify, session, request
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import datetime
import os
import threading
import uuid
from functools import wraps

from arabic_agent import generate_arabic_content, create_lesson_html, create_quiz_html, init_database, save_to_database, get_used_words, get_db

app = Flask(__name__)
app.secret_key = "arabic-agent-secret-2024"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LESSONS_DIR = os.path.join(BASE_DIR, "lessons")
QUIZZES_DIR = os.path.join(BASE_DIR, "quizzes")

os.makedirs(LESSONS_DIR, exist_ok=True)
os.makedirs(QUIZZES_DIR, exist_ok=True)

try:
    init_database()
except Exception as e:
    print(f"Warning: init_database() failed at startup: {e}")

jobs = {}
jobs_lock = threading.Lock()

SECURITY_QUESTIONS = [
    "מה שם החיה הראשונה שלך?",
    "מה שם בית הספר היסודי שלך?",
    "מה שם העיר שבה נולדת?",
    "מה השם הפרטי של אמא שלך?",
    "מה הייתה המכונית הראשונה שלך?",
    "מה שם החבר הכי טוב שלך מהילדות?",
]

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def get_stats(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM words WHERE user_id = %s", (user_id,))
    total_words = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM lessons WHERE user_id = %s", (user_id,))
    total_lessons = c.fetchone()[0]
    c.execute("SELECT date FROM lessons WHERE user_id = %s ORDER BY date DESC", (user_id,))
    dates = [row[0] for row in c.fetchall()]
    conn.close()
    streak = 0
    today = datetime.date.today()
    for i in range(len(dates)):
        expected = (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
        if i < len(dates) and dates[i] == expected:
            streak += 1
        else:
            break
    return total_words, total_lessons, streak

def get_lessons(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, date, topic FROM lessons WHERE user_id = %s ORDER BY date DESC", (user_id,))
    lessons = []
    for row in c.fetchall():
        lesson_id, date, topic = row
        filename = None
        if os.path.exists(LESSONS_DIR):
            for f in os.listdir(LESSONS_DIR):
                if f.startswith(f"lesson_{user_id}_{date}"):
                    filename = f
                    break
        lessons.append({"id": lesson_id, "date": date, "topic": topic, "filename": filename or ""})
    conn.close()
    return lessons

def get_leaderboard():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT u.id, u.username,
               COUNT(DISTINCT l.id) AS total_lessons,
               COUNT(DISTINCT w.id) AS total_words
        FROM users u
        JOIN lessons l ON l.user_id = u.id
        LEFT JOIN words w ON w.user_id = u.id
        GROUP BY u.id, u.username
        HAVING COUNT(DISTINCT l.id) >= 1
    """)
    users = c.fetchall()
    c.execute("SELECT user_id, date FROM lessons ORDER BY user_id, date DESC")
    all_dates = c.fetchall()
    conn.close()
    from collections import defaultdict
    user_dates = defaultdict(list)
    for uid, date in all_dates:
        user_dates[uid].append(date)
    today = datetime.date.today()
    result = []
    for uid, username, total_lessons, total_words in users:
        dates = user_dates[uid]
        streak = 0
        for i in range(len(dates)):
            expected = (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            if i < len(dates) and dates[i] == expected:
                streak += 1
            else:
                break
        result.append({"username": username, "streak": streak,
                       "total_words": total_words, "total_lessons": total_lessons})
    result.sort(key=lambda x: (-x["streak"], -x["total_words"]))
    return result

def get_quiz_topics(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT DISTINCT topic FROM words WHERE user_id = %s ORDER BY topic", (user_id,))
    topics = [row[0] for row in c.fetchall()]
    conn.close()
    return topics

def run_lesson_job(job_id, user_id, topic=None, custom_words=None):
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        today_hebrew = datetime.date.today().strftime("%d.%m.%Y")
        used_words = get_used_words(user_id)
        data = generate_arabic_content(used_words, topic=topic, custom_words=custom_words)
        timestamp = datetime.datetime.now().strftime("%H%M%S")
        filename = f"lesson_{user_id}_{today}_{timestamp}.html"
        filepath = os.path.join(LESSONS_DIR, filename)
        create_lesson_html(data, today_hebrew, filepath)
        save_to_database(data, today, user_id)
        with jobs_lock:
            jobs[job_id] = {"status": "done", "filename": filename, "topic": data["topic_hebrew"], "error": None}
    except Exception as e:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "filename": None, "topic": None, "error": str(e)}

# ── Auth routes ──

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        question = request.form.get("security_question", "").strip()
        answer = request.form.get("security_answer", "").strip().lower()
        if not username or not password or not question or not answer:
            flash("יש למלא את כל השדות.", "error")
            return render_template("signup.html", questions=SECURITY_QUESTIONS)
        if len(password) < 6:
            flash("הסיסמה חייבת להכיל לפחות 6 תווים.", "error")
            return render_template("signup.html", questions=SECURITY_QUESTIONS)
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username = %s", (username,))
        if c.fetchone():
            conn.close()
            flash("שם המשתמש כבר תפוס.", "error")
            return render_template("signup.html", questions=SECURITY_QUESTIONS)
        c.execute("""
            INSERT INTO users (username, password_hash, security_question, security_answer_hash, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            username,
            generate_password_hash(password),
            question,
            generate_password_hash(answer),
            datetime.date.today().strftime("%Y-%m-%d")
        ))
        conn.commit()
        c.execute("SELECT id FROM users WHERE username = %s", (username,))
        user_id = c.fetchone()[0]
        conn.close()
        session["user_id"] = user_id
        session["username"] = username
        flash(f"ברוך הבא, {username}!", "success")
        return redirect(url_for("index"))
    return render_template("signup.html", questions=SECURITY_QUESTIONS)

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
        row = c.fetchone()
        conn.close()
        if not row or not check_password_hash(row[1], password):
            flash("שם משתמש או סיסמה שגויים.", "error")
            return render_template("login.html")
        session["user_id"] = row[0]
        session["username"] = username
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id, security_question FROM users WHERE username = %s", (username,))
        row = c.fetchone()
        conn.close()
        if not row:
            flash("שם המשתמש לא נמצא.", "error")
            return render_template("forgot_password.html", step="username")
        return render_template("forgot_password.html", step="answer",
                               username=username, question=row[1])
    return render_template("forgot_password.html", step="username")

@app.route("/reset-password/<username>", methods=["POST"])
def reset_password(username):
    answer = request.form.get("answer", "").strip().lower()
    new_password = request.form.get("new_password", "").strip()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, security_question, security_answer_hash FROM users WHERE username = %s", (username,))
    row = c.fetchone()
    if not row or not check_password_hash(row[2], answer):
        conn.close()
        flash("התשובה לשאלת האבטחה שגויה.", "error")
        return render_template("forgot_password.html", step="answer",
                               username=username, question=row[1] if row else "")
    if len(new_password) < 6:
        conn.close()
        flash("הסיסמה חייבת להכיל לפחות 6 תווים.", "error")
        return render_template("forgot_password.html", step="answer",
                               username=username, question=row[1])
    c.execute("UPDATE users SET password_hash = %s WHERE username = %s",
              (generate_password_hash(new_password), username))
    conn.commit()
    conn.close()
    flash("הסיסמה עודכנה! אפשר להתחבר.", "success")
    return redirect(url_for("login"))

# ── App routes ──

@app.route("/")
@login_required
def index():
    user_id = session["user_id"]
    total_words, total_lessons, streak = get_stats(user_id)
    lessons = get_lessons(user_id)
    topics = get_quiz_topics(user_id)
    return render_template("index.html",
        username=session["username"],
        total_words=total_words,
        total_lessons=total_lessons,
        streak=streak,
        lessons=lessons,
        topics=topics
    )

ADMIN_USERNAME = "amit shania"
DAILY_LESSON_LIMIT = 3

@app.route("/generate-lesson", methods=["GET", "POST"])
@login_required
def generate_lesson():
    user_id = session["user_id"]
    username = session["username"]
    if request.method == "POST":
        topic = request.form.get("topic", "").strip() or None
        custom_words = request.form.get("words", "").strip() or None
    else:
        topic = request.args.get("topic", "").strip() or None
        custom_words = request.args.get("words", "").strip() or None
    print(f"[generate_lesson] method={request.method} topic={repr(topic)}", flush=True)
    if username != ADMIN_USERNAME:
        today = datetime.date.today().strftime("%Y-%m-%d")
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM lessons WHERE user_id = %s AND date = %s", (user_id, today))
        count = c.fetchone()[0]
        conn.close()
        if count >= DAILY_LESSON_LIMIT:
            flash("הגעת למגבלה היומית של 3 שיעורים. חזור מחר!", "error")
            return redirect(url_for("index"))
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "pending", "filename": None, "topic": None, "error": None}
    t = threading.Thread(target=run_lesson_job, args=(job_id, user_id), kwargs={"topic": topic, "custom_words": custom_words}, daemon=True)
    t.start()
    return redirect(url_for("loading", job_id=job_id))

@app.route("/loading/<job_id>")
@login_required
def loading(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        flash("השיעור לא נמצא.", "error")
        return redirect(url_for("index"))
    return render_template("loading.html", job_id=job_id)

@app.route("/job-status/<job_id>")
@login_required
def job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"status": "error", "error": "job not found"})
    return jsonify(job)

@app.route("/lesson/<filename>")
@login_required
def lesson_view(filename):
    filepath = os.path.join(LESSONS_DIR, filename)
    if not os.path.exists(filepath):
        abort(404)
    return send_file(filepath)

@app.route("/delete-lesson/<int:lesson_id>")
@login_required
def delete_lesson(lesson_id):
    user_id = session["user_id"]
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT date, topic FROM lessons WHERE id = %s AND user_id = %s", (lesson_id, user_id))
    row = c.fetchone()
    if row:
        date, topic = row
        c.execute("DELETE FROM words WHERE date = %s AND topic = %s AND user_id = %s", (date, topic, user_id))
        c.execute("DELETE FROM lessons WHERE id = %s AND user_id = %s", (lesson_id, user_id))
        conn.commit()
        if os.path.exists(LESSONS_DIR):
            for f in os.listdir(LESSONS_DIR):
                if f.startswith(f"lesson_{user_id}_{date}"):
                    os.remove(os.path.join(LESSONS_DIR, f))
    conn.close()
    flash("השיעור נמחק.", "success")
    return redirect(url_for("index"))

@app.route("/generate-quiz")
@app.route("/generate-quiz/<topic>")
@login_required
def generate_quiz(topic=None):
    try:
        user_id = session["user_id"]
        today = datetime.date.today().strftime("%Y-%m-%d")
        today_hebrew = datetime.date.today().strftime("%d.%m.%Y")
        timestamp = datetime.datetime.now().strftime("%H%M%S")
        filename = f"quiz_{user_id}_{today}_{timestamp}.html"
        filepath = os.path.join(QUIZZES_DIR, filename)
        result = create_quiz_html(today_hebrew, filepath, topic=topic, user_id=user_id)
        if result:
            return send_file(filepath)
        else:
            flash("אין מילים בדאטהבייס עדיין. צור שיעור קודם!", "error")
            return redirect(url_for("index"))
    except Exception as e:
        flash(f"שגיאה: {str(e)}", "error")
        return redirect(url_for("index"))

@app.route("/leaderboard")
@login_required
def leaderboard():
    data = get_leaderboard()
    return render_template("leaderboard.html", rows=data, username=session["username"])

if __name__ == "__main__":
    init_database()
    app.run(debug=True, port=5000)
