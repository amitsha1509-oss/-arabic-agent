import os
import anthropic
from twilio.rest import Client
from dotenv import load_dotenv
import json
import datetime
import re
import psycopg2
import psycopg2.extras
from collections import defaultdict
import urllib.parse

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LESSONS_DIR = os.path.join(BASE_DIR, "lessons")
QUIZZES_DIR = os.path.join(BASE_DIR, "quizzes")

def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "6543")),
        database=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        sslmode="require"
    )

def init_database():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            security_question TEXT NOT NULL,
            security_answer_hash TEXT NOT NULL,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS words (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            date TEXT,
            arabic TEXT,
            translation TEXT,
            transliteration TEXT,
            pronunciation TEXT,
            root TEXT,
            sentence TEXT,
            sentence_translation TEXT,
            topic TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            date TEXT,
            topic TEXT,
            article TEXT,
            article_translation TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_used_words(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT arabic FROM words WHERE user_id = %s", (user_id,))
    words = [row[0] for row in c.fetchall()]
    conn.close()
    return words

def save_to_database(data, today, user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO lessons (user_id, date, topic, article, article_translation)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, today, data["topic_hebrew"], data["article"], data["article_translation"]))
    for word in data["words"]:
        c.execute("""
            INSERT INTO words (user_id, date, arabic, translation, transliteration, pronunciation, root, sentence, sentence_translation, topic)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            today,
            word["arabic"],
            word["translation"],
            word["transliteration_hebrew"],
            word["pronunciation_hebrew"],
            word["root"],
            word["sentence"],
            word["sentence_translation"],
            data["topic_hebrew"]
        ))
    conn.commit()
    conn.close()

ARABIC_CHARS = re.compile(r'[\u0600-\u06FF]')
HEBREW_CHARS = re.compile(r'[\u05D0-\u05EA\u05F0-\u05F4\uFB1D-\uFB4E]')
LATIN_CHARS  = re.compile(r'[A-Za-z]')

def _has_arabic(s):  return bool(ARABIC_CHARS.search(s))
def _has_hebrew(s):  return bool(HEBREW_CHARS.search(s))
def _has_latin(s):   return bool(LATIN_CHARS.search(s))

def validate_word(word, index):
    errors = []
    if _has_arabic(word.get('translation','')) or _has_latin(word.get('translation','')):
        errors.append(f"word {index} 'translation' must be Hebrew only")
    if _has_arabic(word.get('transliteration_hebrew','')) or _has_latin(word.get('transliteration_hebrew','')):
        errors.append(f"word {index} 'transliteration_hebrew' must be Hebrew only")
    if _has_arabic(word.get('pronunciation_hebrew','')) or _has_latin(word.get('pronunciation_hebrew','')):
        errors.append(f"word {index} 'pronunciation_hebrew' must be Hebrew only")
    if _has_latin(word.get('root','')):
        errors.append(f"word {index} 'root' must not contain Latin characters")
    if not _has_arabic(word.get('root','')) and not _has_hebrew(word.get('root','')):
        errors.append(f"word {index} 'root' must contain Arabic root letters")
    if _has_hebrew(word.get('sentence','')) or _has_latin(word.get('sentence','')):
        errors.append(f"word {index} 'sentence' must be Arabic only")
    if not _has_arabic(word.get('sentence','')):
        errors.append(f"word {index} 'sentence' must contain Arabic text")
    if _has_arabic(word.get('sentence_translation','')) or _has_latin(word.get('sentence_translation','')):
        errors.append(f"word {index} 'sentence_translation' must be Hebrew only")
    return errors

def _build_prompt(used_sample, topic, custom_words):
    lines = []

    # Topic at the very top so Claude sees it first
    if topic:
        lines.append("=== MANDATORY TOPIC ===")
        lines.append("The article MUST be about: " + topic)
        lines.append("Do NOT write about any other subject.")
        lines.append("======================")
        lines.append("")

    lines.append("You are an expert MSA Arabic teacher. Create a daily Arabic lesson.")
    lines.append("")
    lines.append("Return ONLY a valid JSON object. No markdown, no code fences, no explanation.")
    lines.append("")
    lines.append("{")
    lines.append('  "topic_hebrew": "' + (topic if topic else "\u05E0\u05D5\u05E9\u05D0 \u05D1\u05E2\u05D1\u05E8\u05D9\u05EA") + '",')
    lines.append('  "article": "\u05DB\u05EA\u05D1\u05D4 \u05E9\u05DC 6 \u05DE\u05E9\u05E4\u05D8\u05D9\u05DD \u05D1\u05E2\u05E8\u05D1\u05D9\u05EA \u05E1\u05E4\u05E8\u05D5\u05EA\u05D9\u05EA \u05E2\u05DD \u05E0\u05D9\u05E7\u05D5\u05D3 \u05DE\u05DC\u05D0",')
    lines.append('  "article_translation": "\u05EA\u05E8\u05D2\u05D5\u05DD \u05DE\u05DC\u05D0 \u05E9\u05DC \u05D4\u05DB\u05EA\u05D1\u05D4 \u05DC\u05E2\u05D1\u05E8\u05D9\u05EA, \u05DE\u05E9\u05E4\u05D8 \u05D0\u05D7\u05E8 \u05DE\u05E9\u05E4\u05D8",')
    lines.append('  "words": [')
    lines.append("    {")
    lines.append('      "arabic": "\u05DE\u05D9\u05DC\u05D4 \u05D1\u05E2\u05E8\u05D1\u05D9\u05EA \u05E2\u05DD \u05E0\u05D9\u05E7\u05D5\u05D3",')
    lines.append('      "translation": "\u05EA\u05E8\u05D2\u05D5\u05DD \u05E2\u05D1\u05E8\u05D9 \u05D1\u05DC\u05D1\u05D3",')
    lines.append('      "transliteration_hebrew": "\u05EA\u05E2\u05EA\u05D9\u05E7 \u05D1\u05E2\u05D1\u05E8\u05D9\u05EA \u05D1\u05DC\u05D1\u05D3",')
    lines.append('      "pronunciation_hebrew": "\u05D4\u05E1\u05D1\u05E8 \u05D4\u05D2\u05D9\u05D9\u05D4 \u05D1\u05E2\u05D1\u05E8\u05D9\u05EA",')
    lines.append('      "root": "\u0641.\u0635.\u0644 \u2014 \u05DE\u05E9\u05DE\u05E2\u05D5\u05EA \u05D4\u05E9\u05D5\u05E8\u05E9",')
    lines.append('      "sentence": "\u05DE\u05E9\u05E4\u05D8 \u05E7\u05E6\u05E8 \u05D1\u05E2\u05E8\u05D1\u05D9\u05EA \u05DE\u05D4\u05DB\u05EA\u05D1\u05D4",')
    lines.append('      "sentence_translation": "\u05EA\u05E8\u05D2\u05D5\u05DD \u05D4\u05DE\u05E9\u05E4\u05D8 \u05DC\u05E2\u05D1\u05E8\u05D9\u05EA"')
    lines.append("    }")
    lines.append("  ]")
    lines.append("}")
    lines.append("")
    lines.append("LANGUAGE RULES (strict \u2014 violating these causes the lesson to be rejected):")
    lines.append("- topic_hebrew: HEBREW ONLY")
    lines.append("- article: ARABIC ONLY with full harakat, Arabic punctuation only (. \u060c \u061f !)")
    lines.append("- article_translation: HEBREW ONLY")
    lines.append("- translation: HEBREW ONLY \u2014 no Arabic, no English")
    lines.append("- transliteration_hebrew: HEBREW ONLY")
    lines.append("- pronunciation_hebrew: HEBREW ONLY")
    lines.append("- root: Arabic letters with dots (e.g. \u0641.\u0635.\u0644) then \u2014 then Hebrew meaning")
    lines.append("- sentence: ARABIC ONLY with full harakat")
    lines.append("- sentence_translation: HEBREW ONLY")
    lines.append("")
    lines.append("OTHER RULES:")
    lines.append("- Exactly 10 words")
    lines.append("- Full harakat on all Arabic")
    lines.append("- Short sentences (max 8 words)")
    lines.append("- Short article (6 sentences)")
    if custom_words:
        lines.append("- CUSTOM WORDS \u2014 these words MUST be in the 10 key words: " + custom_words)
    lines.append("- Avoid already-used words: " + used_sample)
    lines.append("")

    # Topic repeated at the bottom
    if topic:
        lines.append("REMINDER: The article topic is '" + topic + "'. Write about nothing else.")
    else:
        lines.append("Choose an interesting and varied topic for the article.")

    lines.append("")
    lines.append("OUTPUT ONLY THE JSON OBJECT, NOTHING ELSE.")

    return "\n".join(lines)


def generate_arabic_content(used_words, topic=None, custom_words=None):
    print(f"Calling Claude... topic={repr(topic)} custom_words={repr(custom_words)}")
    used_sample = ", ".join(used_words[-50:]) if used_words else "none yet"
    prompt = _build_prompt(used_sample, topic, custom_words)

    last_errors = None
    for attempt in range(3):
        if attempt > 0:
            print(f"Retrying Claude (attempt {attempt + 1}) due to: {last_errors[:120]}")
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        raw = raw.strip()
        data = json.loads(raw)
        data['topic_hebrew'] = re.sub(r'[\u0600-\u06FF]+', '', data['topic_hebrew']).strip(' \u2013\u2014-')
        all_errors = []
        for i, word in enumerate(data.get('words', []), 1):
            all_errors.extend(validate_word(word, i))
        if not all_errors:
            return data
        last_errors = "; ".join(all_errors)

    raise ValueError("Language validation failed after 3 attempts: " + last_errors)

def create_lesson_html(data, today_hebrew, filename):
    words_html = ""
    for i, word in enumerate(data["words"], 1):
        words_html += f"""
        <div class="word-card">
            <div class="word-header">
                <div class="word-right">
                    <span class="word-arabic">{word['arabic']}</span>
                    <span class="word-translation">{word['translation']}</span>
                </div>
                <span class="word-number">{i}</span>
            </div>
            <div class="word-details">
                <div class="detail-row">
                    <span class="detail-value">{word['transliteration_hebrew']}</span>
                    <span class="detail-label">תעתיק</span>
                </div>
                <div class="detail-row">
                    <span class="detail-value">{word['pronunciation_hebrew']}</span>
                    <span class="detail-label">הגייה</span>
                </div>
                <div class="detail-row">
                    <span class="detail-value">{word['root']}</span>
                    <span class="detail-label">שורש</span>
                </div>
                <div class="detail-row sentence-row">
                    <span class="detail-value arabic-text">{word['sentence']}</span>
                    <span class="detail-label">משפט</span>
                </div>
                <div class="detail-row">
                    <span class="detail-value translation-text">{word['sentence_translation']}</span>
                    <span class="detail-label">תרגום</span>
                </div>
            </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>שיעור ערבית — {today_hebrew}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #FAFAFA; color: #2D3748; direction: rtl; }}
        .container {{ max-width: 820px; margin: 0 auto; padding: 28px 20px 48px; }}
        /* ── Navbar ── */
        .navbar {{ background: white; border-bottom: 2px solid #F0F0F0; padding: 0 32px; height: 64px; display: flex; align-items: center; position: sticky; top: 0; z-index: 50; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
        .nav-inner {{ max-width: 820px; margin: 0 auto; width: 100%; display: flex; align-items: center; justify-content: space-between; }}
        .nav-brand {{ font-size: 20px; font-weight: 800; color: #58CC02; text-decoration: none; }}
        .nav-links {{ display: flex; align-items: center; gap: 10px; }}
        .nav-link {{ font-size: 14px; color: #4A5568; text-decoration: none; padding: 8px 16px; border-radius: 12px; font-weight: 600; transition: all 0.2s; }}
        .nav-link:hover {{ background: #F7F7F7; color: #2D3748; }}
        .nav-primary {{ background: #58CC02; color: white !important; }}
        .nav-primary:hover {{ background: #46A302 !important; box-shadow: 0 4px 12px rgba(88,204,2,0.3); }}
        .nav-secondary {{ background: #FFF3E0; color: #FF9600 !important; }}
        .nav-secondary:hover {{ background: #FFE0B2 !important; }}
        /* ── Page header ── */
        .page-header {{ background: white; border-radius: 24px; padding: 36px; text-align: center; margin-bottom: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.07); border: 3px solid #F0FDF4; }}
        .page-header h1 {{ font-size: 28px; color: #2D3748; font-weight: 800; margin-bottom: 6px; }}
        .page-header .date {{ font-size: 13px; color: #A0AEC0; margin-bottom: 14px; font-weight: 500; }}
        .topic-badge {{ display: inline-block; background: linear-gradient(135deg, #FFF3E0, #FFE0B2); color: #FF9600; padding: 8px 22px; border-radius: 20px; font-size: 15px; font-weight: 700; border: 2px solid #FFD580; }}
        /* ── Sections ── */
        .section {{ background: white; border-radius: 22px; padding: 28px; margin-bottom: 20px; box-shadow: 0 4px 16px rgba(0,0,0,0.06); border: 2px solid #F0F0F0; }}
        .section-title {{ font-size: 17px; font-weight: 800; color: #2D3748; margin-bottom: 18px; padding-bottom: 14px; border-bottom: 3px solid #F0FDF4; display: flex; align-items: center; gap: 8px; }}
        /* ── Article ── */
        .article-text {{ font-family: 'Traditional Arabic', 'Arial Unicode MS', Arial, sans-serif; font-size: 22px; line-height: 2.5; color: #7C3AED; direction: rtl; text-align: right; background: #FAF5FF; padding: 24px; border-radius: 16px; border-right: 4px solid #7C3AED; }}
        .article-translation {{ font-size: 14px; line-height: 2; color: #718096; font-style: italic; padding: 18px 24px; border-right: 4px solid #E9D5FF; margin-top: 16px; text-align: right; background: #FDFCFF; border-radius: 14px; }}
        /* ── Word cards ── */
        .words-grid {{ display: flex; flex-direction: column; gap: 14px; }}
        .word-card {{ border: 2px solid #F0F0F0; border-radius: 18px; overflow: hidden; background: white; transition: box-shadow 0.2s, transform 0.2s, border-color 0.2s; }}
        .word-card:hover {{ box-shadow: 0 8px 24px rgba(88,204,2,0.12); transform: translateY(-2px); border-color: #BBF7D0; }}
        .word-header {{ background: #F0FDF4; padding: 14px 20px; display: flex; align-items: center; justify-content: space-between; direction: rtl; border-bottom: 2px solid #F0F0F0; }}
        .word-right {{ display: flex; align-items: center; gap: 14px; }}
        .word-number {{ background: #58CC02; color: white; width: 30px; height: 30px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 800; flex-shrink: 0; }}
        .word-arabic {{ font-family: 'Traditional Arabic', 'Arial Unicode MS', Arial, sans-serif; font-size: 28px; color: #7C3AED; }}
        .word-translation {{ font-size: 14px; color: #718096; background: white; padding: 4px 12px; border-radius: 12px; border: 2px solid #E9D5FF; font-weight: 600; }}
        .word-details {{ padding: 6px 20px 14px; }}
        .detail-row {{ display: flex; align-items: flex-start; padding: 10px 0; border-bottom: 1px solid #F7FAFC; gap: 16px; direction: rtl; }}
        .detail-row:last-child {{ border-bottom: none; }}
        .detail-label {{ font-weight: 800; color: #A0AEC0; min-width: 80px; font-size: 11px; flex-shrink: 0; padding-top: 2px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .detail-value {{ color: #2D3748; font-size: 14px; flex: 1; text-align: right; line-height: 1.6; font-weight: 500; }}
        .arabic-text {{ font-family: 'Traditional Arabic', 'Arial Unicode MS', Arial, sans-serif; font-size: 18px; color: #7C3AED; line-height: 2; }}
        .translation-text {{ color: #718096; font-style: italic; }}
        .sentence-row {{ background: #FAF5FF; padding: 12px 14px; border-radius: 10px; margin: 4px 0; border-bottom: none !important; }}
        /* ── Footer ── */
        .footer {{ text-align: center; color: #CBD5E0; font-size: 12px; margin-top: 12px; padding: 20px; }}
    </style>
</head>
<body>
<nav class="navbar">
    <div class="nav-inner">
        <a href="/" class="nav-brand">🌸 ערבית חבצלות</a>
        <div class="nav-links">
            <a href="/" class="nav-link">דף הבית</a>
            <a href="/generate-lesson" class="nav-link nav-primary">שיעור חדש</a>
            <a href="/generate-quiz" class="nav-link nav-secondary">חידון</a>
            <a href="/leaderboard" class="nav-link" style="background:#EDE9FE;color:#7C3AED;">לוח תוצאות</a>
        </div>
    </div>
</nav>
<div class="container">
    <div class="page-header">
        <h1>📖 שיעור ערבית יומי</h1>
        <div class="date">{today_hebrew}</div>
        <div class="topic-badge">✨ {data['topic_hebrew']}</div>
    </div>
    <div class="section">
        <div class="section-title">📰 הכתבה</div>
        <div class="article-text">{data['article']}</div>
        <div class="article-translation">{data['article_translation']}</div>
    </div>
    <div class="section">
        <div class="section-title">📚 מילות המפתח</div>
        <div class="words-grid">{words_html}</div>
    </div>
    <div class="footer">נוצר אוטומטית על ידי ערבית חבצלות • {today_hebrew}</div>
</div>
</body>
</html>"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    return filename

def create_quiz_html(today_hebrew, filename, topic=None, user_id=None):
    conn = get_db()
    c = conn.cursor()
    if topic:
        c.execute("SELECT arabic, translation, transliteration, root, topic FROM words WHERE user_id = %s AND topic = %s", (user_id, topic))
    else:
        c.execute("SELECT arabic, translation, transliteration, root, topic FROM words WHERE user_id = %s", (user_id,))
    words = c.fetchall()
    conn.close()

    if not words:
        print("No words in database yet.")
        return None

    grouped = defaultdict(list)
    for arabic, translation, transliteration, root, t in words:
        grouped[t].append((arabic, translation, transliteration, root))

    sections_html = ""
    card_counter = 0
    for topic_name, topic_words in grouped.items():
        cards_html = ""
        for arabic, translation, transliteration, root in topic_words:
            card_counter += 1
            cards_html += f"""
            <div class="flip-card" onclick="this.classList.toggle('flipped')">
                <div class="flip-inner">
                    <div class="flip-front">
                        <div class="card-number">{card_counter}</div>
                        <div class="card-arabic">{arabic}</div>
                        <div class="card-hint">לחץ לגלות</div>
                    </div>
                    <div class="flip-back">
                        <div class="card-translation">{translation}</div>
                        <div class="card-transliteration">{transliteration}</div>
                        <div class="card-root">{root}</div>
                    </div>
                </div>
            </div>"""
        sections_html += f"""
        <div class="topic-section">
            <div class="topic-header">{topic_name} <span class="topic-count">{len(topic_words)} מילים</span></div>
            <div class="cards-grid">{cards_html}</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>חידון ערבית — {today_hebrew}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #FAFAFA; color: #2D3748; direction: rtl; }}
        .container {{ max-width: 960px; margin: 0 auto; padding: 28px 20px 48px; }}
        /* ── Navbar ── */
        .navbar {{ background: white; border-bottom: 2px solid #F0F0F0; padding: 0 32px; height: 64px; display: flex; align-items: center; position: sticky; top: 0; z-index: 50; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }}
        .nav-inner {{ max-width: 960px; margin: 0 auto; width: 100%; display: flex; align-items: center; justify-content: space-between; }}
        .nav-brand {{ font-size: 20px; font-weight: 800; color: #58CC02; text-decoration: none; }}
        .nav-links {{ display: flex; align-items: center; gap: 10px; }}
        .nav-link {{ font-size: 14px; color: #4A5568; text-decoration: none; padding: 8px 16px; border-radius: 12px; font-weight: 600; transition: all 0.2s; }}
        .nav-link:hover {{ background: #F7F7F7; }}
        .nav-primary {{ background: #58CC02; color: white !important; }}
        .nav-primary:hover {{ background: #46A302 !important; box-shadow: 0 4px 12px rgba(88,204,2,0.3); }}
        .nav-secondary {{ background: #FFF3E0; color: #FF9600 !important; }}
        .nav-secondary:hover {{ background: #FFE0B2 !important; }}
        /* ── Page header ── */
        .page-header {{ background: white; border-radius: 24px; padding: 32px; text-align: center; margin-bottom: 24px; box-shadow: 0 4px 20px rgba(0,0,0,0.07); border: 3px solid #F0FDF4; }}
        .page-header h1 {{ font-size: 28px; color: #2D3748; font-weight: 800; margin-bottom: 8px; }}
        .page-header p {{ color: #A0AEC0; font-size: 15px; font-weight: 500; }}
        /* ── Instructions ── */
        .instructions {{ background: #F0FDF4; border-radius: 14px; padding: 14px 22px; margin-bottom: 28px; color: #276749; font-size: 14px; font-weight: 600; text-align: center; border: 2px solid #BBF7D0; }}
        /* ── Topic sections ── */
        .topic-section {{ margin-bottom: 36px; }}
        .topic-header {{ font-size: 16px; font-weight: 800; color: #FF9600; margin-bottom: 16px; padding: 12px 20px; background: white; border-radius: 16px; border: 2px solid #FED7AA; box-shadow: 0 2px 8px rgba(255,150,0,0.1); display: flex; align-items: center; justify-content: space-between; }}
        .topic-count {{ font-size: 12px; background: #FFF3E0; color: #FF9600; padding: 4px 12px; border-radius: 12px; font-weight: 700; }}
        /* ── Flip cards ── */
        .cards-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 16px; }}
        .flip-card {{ height: 160px; cursor: pointer; perspective: 1000px; }}
        .flip-card:hover .flip-front {{ box-shadow: 0 8px 24px rgba(88,204,2,0.15); transform: translateY(-2px); border-color: #BBF7D0; }}
        .flip-inner {{ position: relative; width: 100%; height: 100%; transition: transform 0.5s cubic-bezier(0.4,0,0.2,1); transform-style: preserve-3d; }}
        .flip-card.flipped .flip-inner {{ transform: rotateY(180deg); }}
        .flip-front, .flip-back {{ position: absolute; width: 100%; height: 100%; backface-visibility: hidden; border-radius: 18px; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 16px; }}
        .flip-front {{ background: white; border: 2px solid #F0F0F0; box-shadow: 0 3px 12px rgba(0,0,0,0.07); transition: box-shadow 0.2s, transform 0.2s, border-color 0.2s; }}
        .flip-back {{ background: linear-gradient(145deg, #58CC02, #3EA801); color: white; transform: rotateY(180deg); box-shadow: 0 6px 20px rgba(88,204,2,0.35); }}
        .card-number {{ font-size: 11px; color: #CBD5E0; margin-bottom: 8px; font-weight: 600; }}
        .card-arabic {{ font-family: 'Traditional Arabic', 'Arial Unicode MS', Arial, sans-serif; font-size: 28px; color: #7C3AED; direction: rtl; }}
        .card-hint {{ font-size: 11px; color: #CBD5E0; margin-top: 10px; font-weight: 500; }}
        .card-translation {{ font-size: 18px; font-weight: 800; margin-bottom: 6px; text-align: center; }}
        .card-transliteration {{ font-size: 12px; opacity: 0.9; margin-bottom: 4px; font-weight: 500; }}
        .card-root {{ font-size: 11px; opacity: 0.75; }}
        /* ── Footer ── */
        .footer {{ text-align: center; color: #CBD5E0; font-size: 12px; margin-top: 24px; padding: 20px; }}
    </style>
</head>
<body>
<nav class="navbar">
    <div class="nav-inner">
        <a href="/" class="nav-brand">🌸 ערבית חבצלות</a>
        <div class="nav-links">
            <a href="/" class="nav-link">דף הבית</a>
            <a href="/generate-lesson" class="nav-link nav-primary">שיעור חדש</a>
            <a href="/generate-quiz" class="nav-link nav-secondary">חידון</a>
            <a href="/leaderboard" class="nav-link" style="background:#EDE9FE;color:#7C3AED;">לוח תוצאות</a>
        </div>
    </div>
</nav>
<div class="container">
    <div class="page-header">
        <h1>🧠 חידון ערבית</h1>
        <p>{today_hebrew} • {card_counter} מילים</p>
    </div>
    <div class="instructions">💡 לחץ על כל כרטיס כדי לגלות את התרגום</div>
    {sections_html}
    <div class="footer">ערבית חבצלות • {today_hebrew}</div>
</div>
</body>
</html>"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Quiz saved: {filename}")
    return filename

def send_whatsapp(message):
    twilio_client.messages.create(
        from_=os.getenv("TWILIO_WHATSAPP_FROM"),
        to=os.getenv("TWILIO_WHATSAPP_TO"),
        body=message
    )

def main():
    try:
        init_database()
        today = datetime.date.today().strftime("%Y-%m-%d")
        today_hebrew = datetime.date.today().strftime("%d.%m.%Y")
        is_sunday = datetime.date.today().weekday() == 6
        used_words = get_used_words()
        print(f"Words in database: {len(used_words)}")
        data = generate_arabic_content(used_words)
        print(f"Topic: {data['topic_hebrew']}")
        timestamp = datetime.datetime.now().strftime("%H%M%S")
        os.makedirs(LESSONS_DIR, exist_ok=True)
        lesson_file = os.path.join(LESSONS_DIR, f"lesson_{today}_{timestamp}.html")
        create_lesson_html(data, today_hebrew, lesson_file)
        save_to_database(data, today)
        send_whatsapp(f"📖 שיעור הערבית היומי שלך מוכן!\n\nנושא: {data['topic_hebrew']}\nפתח: http://localhost:5000")
        print("SUCCESS!")
        if is_sunday:
            os.makedirs(QUIZZES_DIR, exist_ok=True)
            quiz_file = os.path.join(QUIZZES_DIR, f"quiz_{today}_{timestamp}.html")
            create_quiz_html(today_hebrew, quiz_file)
            send_whatsapp(f"🧠 החידון השבועי מוכן!\nפתח: http://localhost:5000")
    except json.JSONDecodeError as e:
        print(f"JSON Error: {e}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
