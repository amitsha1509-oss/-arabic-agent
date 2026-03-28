from flask import Flask, render_template, redirect, url_for, flash, send_file, abort
import sqlite3
import datetime
import os
import sys

sys.path.insert(0, 'C:\\arabic-agent')
from arabic_agent import generate_arabic_content, create_lesson_html, create_quiz_html, init_database, save_to_database, get_used_words

app = Flask(__name__)
app.secret_key = "arabic-agent-secret"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "arabic_words.db")
LESSONS_DIR = os.path.join(BASE_DIR, "lessons")
QUIZZES_DIR = os.path.join(BASE_DIR, "quizzes")

os.makedirs(LESSONS_DIR, exist_ok=True)
os.makedirs(QUIZZES_DIR, exist_ok=True)

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM words")
    total_words = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM lessons")
    total_lessons = c.fetchone()[0]
    c.execute("SELECT date FROM lessons ORDER BY date DESC")
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

def get_lessons():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, date, topic FROM lessons ORDER BY date DESC")
    lessons = []
    for row in c.fetchall():
        lesson_id, date, topic = row
        filename = None
        if os.path.exists(LESSONS_DIR):
            for f in os.listdir(LESSONS_DIR):
                if f.startswith(f"lesson_{date}"):
                    filename = f
                    break
        lessons.append({"id": lesson_id, "date": date, "topic": topic, "filename": filename or ""})
    conn.close()
    return lessons

def get_quiz_topics():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT topic FROM words ORDER BY topic")
    topics = [row[0] for row in c.fetchall()]
    conn.close()
    return topics

@app.route("/")
def index():
    init_database()
    total_words, total_lessons, streak = get_stats()
    lessons = get_lessons()
    topics = get_quiz_topics()
    return render_template("index.html",
        total_words=total_words,
        total_lessons=total_lessons,
        streak=streak,
        lessons=lessons,
        topics=topics
    )

@app.route("/generate-lesson")
def generate_lesson():
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        today_hebrew = datetime.date.today().strftime("%d.%m.%Y")
        used_words = get_used_words()
        data = generate_arabic_content(used_words)
        timestamp = datetime.datetime.now().strftime("%H%M%S")
        filename = f"lesson_{today}_{timestamp}.html"
        filepath = os.path.join(LESSONS_DIR, filename)
        create_lesson_html(data, today_hebrew, filepath)
        save_to_database(data, today)
        flash(f"שיעור חדש נוצר! נושא: {data['topic_hebrew']}", "success")
        return redirect(url_for("lesson_view", filename=filename))
    except Exception as e:
        flash(f"שגיאה: {str(e)}", "error")
        return redirect(url_for("index"))

@app.route("/lesson/<filename>")
def lesson_view(filename):
    filepath = os.path.join(LESSONS_DIR, filename)
    if not os.path.exists(filepath):
        abort(404)
    return send_file(filepath)

@app.route("/delete-lesson/<int:lesson_id>")
def delete_lesson(lesson_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT date, topic FROM lessons WHERE id = ?", (lesson_id,))
    row = c.fetchone()
    if row:
        date, topic = row
        c.execute("DELETE FROM words WHERE date = ? AND topic = ?", (date, topic))
        c.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
        conn.commit()
        # Delete HTML file
        if os.path.exists(LESSONS_DIR):
            for f in os.listdir(LESSONS_DIR):
                if f.startswith(f"lesson_{date}"):
                    os.remove(os.path.join(LESSONS_DIR, f))
    conn.close()
    flash("השיעור נמחק.", "success")
    return redirect(url_for("index"))

@app.route("/generate-quiz")
@app.route("/generate-quiz/<topic>")
def generate_quiz(topic=None):
    try:
        today = datetime.date.today().strftime("%Y-%m-%d")
        today_hebrew = datetime.date.today().strftime("%d.%m.%Y")
        timestamp = datetime.datetime.now().strftime("%H%M%S")
        filename = f"quiz_{today}_{timestamp}.html"
        filepath = os.path.join(QUIZZES_DIR, filename)
        result = create_quiz_html(today_hebrew, filepath, topic=topic)
        if result:
            return send_file(filepath)
        else:
            flash("אין מילים בדאטהבייס עדיין. צור שיעור קודם!", "error")
            return redirect(url_for("index"))
    except Exception as e:
        flash(f"שגיאה: {str(e)}", "error")
        return redirect(url_for("index"))

if __name__ == "__main__":
    init_database()
    app.run(debug=True, port=5000)