from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from models import db, User, Quiz, QuizResult, Connection, UserTag, Message, ChatGroup, ChatGroupMember, GroupMessage, BrainstormSession, BrainstormNote, GroupJoinRequest, Poll, PollOption, PollVote, GeneratedQuestion
import hashlib
import os
import requests as req
from dotenv import load_dotenv
import google.generativeai as genai
import random
import PyPDF2
import io
import json
import tempfile
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

# ── Startup guard ─────────────────────────────────────────────────────────────
_required_env = ['SECRET_KEY', 'GOOGLE_AI_API_KEY', 'MAIL_USERNAME', 'BREVO_API_KEY']
_missing_env  = [k for k in _required_env if not os.getenv(k)]
if _missing_env:
    raise RuntimeError(
        f"Missing required environment variables: {', '.join(_missing_env)}. "
        "Set them in your Render dashboard under Environment."
    )

app = Flask(__name__)

# ── Database ──────────────────────────────────────────────────────────────────
_db_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_UR')

if (os.getenv('RENDER') or _db_url) and _db_url:
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    if '?' in _db_url:
        _db_url = _db_url.split('?')[0]
    _db_url += '?sslmode=require'
    app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'pool_timeout': 20,
        'pool_size': 2,
        'max_overflow': 3,
    }
    try:
        _host = _db_url.split('@')[1].split('/')[0] if '@' in _db_url else 'unknown'
        print(f"Using PostgreSQL: {_host}")
    except Exception:
        print("Using PostgreSQL")
else:
    _base_dir      = os.path.dirname(os.path.abspath(__file__))
    _instance_path = os.path.join(_base_dir, 'instance')
    os.makedirs(_instance_path, exist_ok=True)
    _db_path = os.path.join(_instance_path, 'knowitnow.db').replace('\\', '/')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{_db_path}'
    print(f"Using SQLite at {_db_path}")

app.secret_key = os.getenv('SECRET_KEY', 'knowitnow_super_secret_key_change_in_production')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER']         = 'uploads'
app.config['PROFILE_UPLOAD_FOLDER'] = 'uploads/profiles'
app.config['MAX_CONTENT_LENGTH']    = 20 * 1024 * 1024  # 20 MB (images + PDFs)

app.config['SESSION_TYPE']           = 'filesystem'
_session_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flask_sessions')
os.makedirs(_session_dir, exist_ok=True)
app.config['SESSION_FILE_DIR']       = _session_dir
app.config['SESSION_PERMANENT']      = False
app.config['SESSION_USE_SIGNER']     = True
app.config['SESSION_FILE_THRESHOLD'] = 500
db.init_app(app)
Session(app)

with app.app_context():
    db.create_all()

# ── Google AI ─────────────────────────────────────────────────────────────────
genai.configure(api_key=os.getenv('GOOGLE_AI_API_KEY'))
model        = genai.GenerativeModel('gemini-2.5-flash')
vision_model = genai.GenerativeModel('gemini-2.5-flash')   # same model, supports vision

# ── Folders ───────────────────────────────────────────────────────────────────
os.makedirs('uploads/profiles', exist_ok=True)
os.makedirs('uploads',          exist_ok=True)


# ── Brevo email ───────────────────────────────────────────────────────────────
def send_email_brevo(to_email, to_name, subject, body):
    response = req.post(
        'https://api.brevo.com/v3/smtp/email',
        headers={
            'api-key': os.getenv('BREVO_API_KEY'),
            'Content-Type': 'application/json'
        },
        json={
            'sender':      {'name': 'Brainspark', 'email': os.getenv('MAIL_USERNAME')},
            'to':          [{'email': to_email, 'name': to_name}],
            'subject':     subject,
            'textContent': body
        },
        timeout=15
    )
    if response.status_code not in (200, 201):
        raise Exception(f"Brevo API error {response.status_code}: {response.text}")
    return True


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp'}


def extract_pdf_text(file_storage):
    """Extract text from a PDF FileStorage object."""
    try:
        pdf_bytes  = file_storage.read()
        pdf_stream = io.BytesIO(pdf_bytes)
        pdf_reader = PyPDF2.PdfReader(pdf_stream)
        if not pdf_reader.pages:
            return None
        text = ''
        for page in pdf_reader.pages:
            try:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + '\n'
            except Exception:
                continue
        file_storage.seek(0)
        return text.strip() if text.strip() else None
    except Exception as e:
        logger.error(f'Error extracting PDF text: {str(e)}', exc_info=True)
        return None


def extract_pdf_bytes(file_storage):
    """Return raw bytes + reset pointer."""
    pdf_bytes = file_storage.read()
    file_storage.seek(0)
    return pdf_bytes


def extract_pdf_text_simple(filepath):
    """Extract text from a PDF file path."""
    try:
        text = ""
        with open(filepath, 'rb') as f:
            pdf_reader = PyPDF2.PdfReader(f)
            for page in pdf_reader.pages[:10]:
                try:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        text += page_text + "\n"
                except Exception:
                    continue
        return text.strip()
    except Exception as e:
        logger.warning(f"PDF extraction failed: {str(e)}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    theme = session.get('theme', 'light')
    return render_template('index.html', theme=theme)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        theme = session.get('theme', 'light')
        return render_template('signup.html', theme=theme)

    try:
        name        = request.form.get('name', '').strip()
        username    = request.form.get('username', '').strip()
        email       = request.form.get('email', '').strip().lower()
        school      = request.form.get('school', '').strip()
        profession  = request.form.get('profession', '').strip()
        study_level = request.form.get('study_level', '').strip()
        country     = request.form.get('country', '').strip()
        password    = request.form.get('password', '')

        missing = []
        if not name:        missing.append('Full Name')
        if not username:    missing.append('Username')
        if not email:       missing.append('Email')
        if not school:      missing.append('School / University')
        if not study_level: missing.append('Level of Study')
        if not country:     missing.append('Country')
        if not password:    missing.append('Password')

        if missing:
            return jsonify({'success': False, 'error': f"Please fill in: {', '.join(missing)}."})

        if '@' not in email or '.' not in email.split('@')[-1]:
            return jsonify({'success': False, 'error': 'Please enter a valid email address.'})

        import re
        if not re.match(r'^[a-zA-Z0-9_]{3,50}$', username):
            return jsonify({'success': False, 'error': 'Username must be 3–50 characters and contain only letters, numbers, or underscores.'})

        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            return jsonify({'success': False, 'error': 'That username is already taken. Please choose another.'})

        if User.query.filter(db.func.lower(User.email) == email).first():
            return jsonify({'success': False, 'error': 'An account with this email already exists. Try logging in.'})

        if len(password) < 8:
            return jsonify({'success': False, 'error': 'Password must be at least 8 characters.'})
        if not re.search(r'[A-Z]', password):
            return jsonify({'success': False, 'error': 'Password must contain at least one uppercase letter.'})
        if not re.search(r'\d', password):
            return jsonify({'success': False, 'error': 'Password must contain at least one number.'})
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return jsonify({'success': False, 'error': 'Password must contain at least one special character.'})

        user = User(
            name=name, username=username, email=email,
            school=school, profession=profession,
            study_level=study_level, country=country
        )
        user.set_password(password)
        code = ''.join(random.choices('0123456789', k=6))
        user.verification_code = code

        try:
            body = (
                f"Hi {name},\n\n"
                f"Your Brainspark verification code is:\n\n"
                f"    {code}\n\n"
                f"Enter this code on the signup page to activate your account.\n"
                f"The code expires in 15 minutes.\n\n"
                f"If you didn't sign up, you can safely ignore this email.\n\n"
                f"— The Brainspark Team"
            )
            send_email_brevo(email, name, 'Brainspark — Verify Your Email', body)
        except Exception as mail_err:
            logger.error(f"Email send failed for {email}: {mail_err}", exc_info=True)
            return jsonify({'success': False, 'error': f'Could not send verification email: {str(mail_err)[:200]}. Please try again.'})

        try:
            db.session.add(user)
            db.session.flush()

            profile_file = request.files.get('profile_pic')
            if profile_file and profile_file.filename and allowed_file(profile_file.filename):
                try:
                    ext      = profile_file.filename.rsplit('.', 1)[1].lower()
                    filename = f"{user.id}.{ext}"
                    save_dir = app.config['PROFILE_UPLOAD_FOLDER']
                    os.makedirs(save_dir, exist_ok=True)
                    profile_file.save(os.path.join(save_dir, filename))
                    user.profile_pic = filename
                except Exception as pic_err:
                    logger.warning(f"Profile pic save failed (non-fatal): {pic_err}")

            db.session.commit()
        except Exception as db_err:
            db.session.rollback()
            logger.error(f"DB commit failed for {email}: {db_err}", exc_info=True)
            return jsonify({'success': False, 'error': 'Account creation failed due to a database error. Please try again.'})

        logger.info(f"New user created: {username} <{email}>")
        return jsonify({'success': True, 'email': email, 'user_id': user.id})

    except Exception as unexpected:
        logger.error(f"Unexpected error in /signup POST: {unexpected}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': f'An unexpected error occurred: {str(unexpected)[:200]}'}), 500


@app.route('/verify', methods=['POST'])
def verify():
    try:
        data  = request.get_json(silent=True) or {}
        code  = str(data.get('code', '')).strip()
        email = str(data.get('email', '')).strip().lower()

        if not code or not email:
            return jsonify({'success': False, 'error': 'Code and email are required.'}), 400

        user = User.query.filter(
            db.func.lower(User.email) == email,
            User.verification_code == code,
            User.is_verified == False
        ).first()

        if not user:
            verified_user = User.query.filter(db.func.lower(User.email) == email, User.is_verified == True).first()
            if verified_user:
                return jsonify({'success': False, 'error': 'This account is already verified. Please log in.'})
            unverified_user = User.query.filter(db.func.lower(User.email) == email, User.is_verified == False).first()
            if unverified_user:
                return jsonify({'success': False, 'error': 'Incorrect code. Please check your email and try again, or request a new code.'})
            return jsonify({'success': False, 'error': 'No account found for this email. Please sign up again.'})

        user.is_verified       = True
        user.verification_code = None
        db.session.commit()
        return jsonify({'success': True, 'message': 'Account verified! Redirecting to login…', 'redirect': '/login'})

    except Exception as e:
        logger.error(f"Unexpected error in /verify: {e}", exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Verification failed. Please try again.'}), 500


@app.route('/verify-email')
def verify_email_page():
    email = request.args.get('email', '')
    theme = session.get('theme', 'light')
    return render_template('verify_email.html', email=email, theme=theme)


@app.route('/resend-verification', methods=['POST'])
def resend_verification():
    try:
        data  = request.get_json(silent=True) or {}
        email = str(data.get('email', '')).strip().lower()

        if not email:
            return jsonify({'success': False, 'error': 'Email is required.'}), 400

        user = User.query.filter(db.func.lower(User.email) == email, User.is_verified == False).first()
        if not user:
            return jsonify({'success': False, 'error': 'No unverified account found for this email.'}), 404

        code = ''.join(random.choices('0123456789', k=6))
        user.verification_code = code
        db.session.commit()

        try:
            body = (
                f"Hi {user.name},\n\n"
                f"Your new Brainspark verification code is:\n\n"
                f"    {code}\n\n"
                f"This code expires in 15 minutes.\n\n"
                f"— The Brainspark Team"
            )
            send_email_brevo(email, user.name, 'Brainspark — New Verification Code', body)
            return jsonify({'success': True, 'message': 'A new code has been sent to your email.'})
        except Exception as mail_err:
            logger.error(f"Resend email failed for {email}: {mail_err}", exc_info=True)
            return jsonify({'success': False, 'error': f'Could not send the email: {str(mail_err)[:120]}. Please try again.'}), 500

    except Exception as e:
        logger.error(f"Unexpected error in /resend-verification: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'An unexpected error occurred.'}), 500


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user     = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                if not user.is_verified:
                    return jsonify({'success': False, 'error': 'Please verify your email before logging in.'})
                session['user_id']  = user.id
                session['username'] = user.username
                return jsonify({'success': True, 'message': 'Login successful!', 'redirect': '/dashboard'})
            else:
                return jsonify({'success': False, 'error': 'Invalid username or password.'})
        except Exception as e:
            logger.error(f"Login error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': 'Login failed. Please try again.'}), 500
    theme = session.get('theme', 'light')
    return render_template('login.html', theme=theme)


@app.route('/toggle_mode', methods=['POST'])
def toggle_mode():
    current_theme    = session.get('theme', 'light')
    new_theme        = 'dark' if current_theme == 'light' else 'light'
    session['theme'] = new_theme
    return jsonify({'theme': new_theme})


@app.route('/send_email', methods=['POST'])
def send_email():
    try:
        data = request.json
        body = f"Name: {data.get('name')}\nEmail: {data.get('email')}\nMessage: {data.get('message')}"
        send_email_brevo(
            os.getenv('MAIL_USERNAME'),
            'Brainspark Admin',
            f"Brainspark Contact: {data.get('name', 'No Name')}",
            body
        )
        return jsonify({'message': 'Message sent successfully!'})
    except Exception as e:
        return jsonify({'message': f'Error: {str(e)}'}), 500


@app.route('/upload_notes', methods=['POST'])
def upload_notes():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Please login first'}), 401

    file = None
    for field_name in ('file', 'pdf', 'notes'):
        if field_name in request.files and request.files[field_name].filename:
            file = request.files[field_name]
            break

    if file is None:
        return jsonify({'success': False, 'error': 'No file uploaded. Please select a PDF file.'}), 400
    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'Only PDF files are allowed'}), 400

    try:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{int(datetime.now().timestamp())}_{filename}")
        file.save(filepath)
        text = extract_pdf_text_simple(filepath)
        if os.path.exists(filepath):
            os.remove(filepath)

        if not text or len(text.strip()) < 50:
            return jsonify({'success': False, 'error': 'No readable text found in PDF. Try a text-based PDF (not scanned images).'}), 400

        question_type  = request.form.get('type', 'objective')
        hardness       = request.form.get('hardness', 'medium')
        question_count = int(request.form.get('question_count', 10))
        question_count = max(5, min(100, question_count))

        topics_prompt = f"""Analyze this educational text and identify the main topics, sections, or chapters.

Text:
{text[:3000]}

**OUTPUT ONLY VALID JSON** (no explanations):
{{"topics": ["Topic 1", "Topic 2", "Topic 3", ...]}}

Rules:
- Extract 3-10 distinct main topics/sections
- Use concise, clear topic names (2-6 words each)
- If the text is short or has no clear sections, return ["General Content"]"""

        response    = model.generate_content(topics_prompt)
        topics_text = response.text.strip()

        try:
            start       = topics_text.find('{')
            end         = topics_text.rfind('}') + 1
            json_str    = topics_text[start:end] if start != -1 and end > start else '{}'
            topics_data = json.loads(json_str)
        except json.JSONDecodeError:
            topics_data = {"topics": ["General Content"]}

        topics = topics_data.get('topics', ["General Content"]) or ["General Content"]

        session['pdf_text']        = text
        session['pdf_topics']      = json.dumps(topics)
        session['quiz_questions']  = None
        session['question_type']   = question_type
        session['hardness']        = hardness
        session['question_count']  = question_count
        session['pdf_source_hash'] = hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()
        session.modified = True

        return jsonify({'success': True, 'count': len(topics), 'redirect': '/quiz'})

    except Exception as e:
        logger.error(f"Upload notes error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Processing error: {str(e)[:200]}'}), 500


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash('Please login first')
        return redirect(url_for('index'))
    user_id      = session['user_id']
    user         = User.query.get(user_id)
    theme        = session.get('theme', 'light')
    current_hour = datetime.utcnow().hour
    if 5 <= current_hour < 12:    time_of_day = 'Morning'
    elif 12 <= current_hour < 18: time_of_day = 'Afternoon'
    else:                         time_of_day = 'Evening'
    return render_template('dashboard.html', user=user, theme=theme, time_of_day=time_of_day)


@app.route('/quiz')
def quiz():
    if 'user_id' not in session:
        flash('Please login first')
        return redirect(url_for('index'))
    theme = session.get('theme', 'light')
    return render_template('quiz.html', theme=theme)


@app.route('/study-buddies')
def study_buddies():
    if 'user_id' not in session:
        flash('Please login first')
        return redirect(url_for('index'))
    user_id = session['user_id']
    user    = User.query.get(user_id)
    theme   = session.get('theme', 'light')
    return render_template('study-buddies.html', user=user, theme=theme)


@app.route('/api/get-quiz-questions')
def get_quiz_questions():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    quiz_questions_raw = session.get('quiz_questions')
    if not quiz_questions_raw:
        return jsonify({'success': False, 'error': 'No quiz data found. Please upload a PDF again.'}), 400
    try:
        if isinstance(quiz_questions_raw, str):
            quiz_questions = json.loads(quiz_questions_raw)
        else:
            quiz_questions = quiz_questions_raw
        questions = quiz_questions.get('questions', []) if isinstance(quiz_questions, dict) else quiz_questions
        return jsonify({'success': True, 'questions': questions})
    except Exception as e:
        logger.error(f'Error parsing quiz_questions from session: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': 'Invalid quiz data in session. Please upload the PDF again.'}), 500


@app.route('/api/get-quiz-topics')
def get_quiz_topics():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    topics_json = session.get('pdf_topics')
    questions   = session.get('quiz_questions')
    if questions:
        return jsonify({'success': True, 'topics': [], 'already_generated': True})
    if not topics_json:
        return jsonify({'success': False, 'error': 'No topics found. Please upload a PDF again.'}), 400
    try:
        topics = json.loads(topics_json)
        return jsonify({'success': True, 'topics': topics, 'already_generated': False})
    except json.JSONDecodeError:
        return jsonify({'success': False, 'error': 'Invalid topics data'}), 500


@app.route('/api/generate-questions', methods=['POST'])
def generate_questions():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data            = request.json or {}
    selected_topics = data.get('selected_topics', 'all')
    exam_mode       = data.get('exam_mode', False)

    try:
        requested_count = int(session.get('question_count', 10) or 10)
    except Exception:
        requested_count = 10
    requested_count = max(5, min(100, requested_count))

    pdf_text = session.get('pdf_text')
    if not pdf_text:
        return jsonify({'error': 'No PDF text found. Please upload a PDF again.'}), 400

    question_type  = session.get('question_type', 'objective')
    hardness       = session.get('hardness', 'medium')
    question_count = requested_count

    try:
        pdf_source_hash = session.get('pdf_source_hash')
        if not pdf_source_hash:
            pdf_source_hash = hashlib.sha256(pdf_text.encode('utf-8', errors='ignore')).hexdigest()
            session['pdf_source_hash'] = pdf_source_hash

        existing_rows = GeneratedQuestion.query.filter_by(user_id=session['user_id'], source_hash=pdf_source_hash).all()
        existing_question_texts = {str(r.question_text).strip().lower() for r in existing_rows if r.question_text}

        if selected_topics == 'all' or not selected_topics:
            if exam_mode:
                prompt = f"""You are an exam paper setter. Generate exam-standard questions from this educational text.

{pdf_text[:3000]}

**OUTPUT ONLY VALID JSON** (no explanations):
{{"questions": [{{"question": "...", "options": ["A. option1", "B. option2", "C. option3", "D. option4"], "answer": "A", "explanation": "..."}}]}}

Rules:
- Generate EXACTLY {question_count} questions.
- Questions must test understanding of CONCEPTS, PRINCIPLES, and APPLICATION only.
- NEVER ask about: professor names, author names, book titles, page numbers, who wrote something, course codes, or any administrative/metadata details.
- Questions must be answerable from the content itself, not from knowing who wrote it.
- Cover all major topics in the text proportionally.
- Vary question types: definition, application, comparison, calculation, analysis.
- Exactly 4 options labelled A, B, C, D.
- "answer" must be exactly one letter: A, B, C, or D.
- Difficulty: {hardness}
- All questions must have unique question text."""
            else:
                prompt = f"""Generate UNIQUE questions (no repeats) for this text:

{pdf_text[:3000]}

**OUTPUT ONLY VALID JSON** (no explanations):
{{"questions": [{{"question": "...", "options": ["A. option1", "B. option2", "C. option3", "D. option4"], "answer": "A", "explanation": "..."}}]}}

Rules:
- Generate EXACTLY {question_count} questions.
- Exactly 4 options labelled A, B, C, D.
- "answer" must be exactly one letter: A, B, C, or D.
- Difficulty: {hardness}
- All questions must have unique question text."""
        else:
            topics_str = ', '.join(selected_topics) if isinstance(selected_topics, list) else str(selected_topics)
            prompt = f"""Generate UNIQUE questions (no repeats) from this text, focusing ONLY on these topics: {topics_str}

Text:
{pdf_text[:3000]}

**OUTPUT ONLY VALID JSON** (no explanations):
{{"questions": [{{"question": "...", "options": ["A. option1", "B. option2", "C. option3", "D. option4"], "answer": "A", "explanation": "..."}}]}}

Rules:
- Generate EXACTLY {question_count} questions.
- Focus ONLY on: {topics_str}
- Exactly 4 options labelled A, B, C, D.
- "answer" must be exactly one letter: A, B, C, or D.
- Difficulty: {hardness}
- All questions must have unique question text."""

        response       = model.generate_content(prompt)
        questions_text = response.text.strip()

        try:
            start          = questions_text.find('{')
            end            = questions_text.rfind('}') + 1
            json_str       = questions_text[start:end] if start != -1 and end > start else '{}'
            questions_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {str(e)}")
            return jsonify({'success': False, 'error': 'Failed to parse AI response. Try again.'}), 500

        questions = questions_data.get('questions', [])
        if not questions:
            return jsonify({'success': False, 'error': 'No questions generated. Try different topics or a different PDF.'}), 400

        def _norm(t):
            return str(t).strip().lower()

        unique_new = []
        for q in questions:
            qt = _norm(q.get('question', ''))
            if not qt or qt in existing_question_texts:
                continue
            if any(_norm(ex.get('question')) == qt for ex in unique_new):
                continue
            unique_new.append(q)
            existing_question_texts.add(qt)

        attempts = 0
        while len(unique_new) < question_count and attempts < 2:
            attempts += 1
            already    = list(existing_question_texts)[:50]
            topics_str = (', '.join(selected_topics) if isinstance(selected_topics, list) else str(selected_topics)) if selected_topics != 'all' else 'all topics'
            follow_prompt = f"""Generate MORE UNIQUE questions (no repeats from list below).
Already used (samples): {already}
Text: {pdf_text[:3000]}
Focus: {topics_str}

**OUTPUT ONLY VALID JSON**:
{{"questions": [{{"question":"...","options":["A. opt","B. opt","C. opt","D. opt"],"answer":"A","explanation":"..."}}]}}

Rules:
- Generate exactly {question_count - len(unique_new)} new questions.
- No repeated question text.
- Exactly 4 options. answer is A/B/C/D.
- Difficulty: {hardness}"""

            follow_resp = model.generate_content(follow_prompt)
            follow_text = follow_resp.text.strip()
            try:
                s = follow_text.find('{'); e = follow_text.rfind('}') + 1
                follow_data = json.loads(follow_text[s:e] if s != -1 and e > s else follow_text)
            except Exception:
                break

            for q in (follow_data.get('questions', []) if isinstance(follow_data, dict) else []):
                qt = _norm(q.get('question', ''))
                if not qt or qt in existing_question_texts:
                    continue
                if any(_norm(ex.get('question')) == qt for ex in unique_new):
                    continue
                unique_new.append(q)
                existing_question_texts.add(qt)
                if len(unique_new) >= question_count:
                    break

        unique_new = unique_new[:question_count]

        if len(unique_new) < 5:
            return jsonify({'success': False, 'error': f'Could only generate {len(unique_new)} unique questions. Try generating again or upload a longer PDF.'}), 400

        for q in unique_new:
            try:
                db.session.add(GeneratedQuestion(
                    user_id=session['user_id'],
                    question_text=q.get('question', ''),
                    options=json.dumps(q.get('options', [])),
                    correct_answer=str(q.get('answer', '')),
                    explanation=q.get('explanation', ''),
                    source_hash=pdf_source_hash,
                    difficulty=hardness,
                    question_type=question_type
                ))
            except Exception:
                continue
        db.session.commit()

        session['quiz_questions'] = json.dumps({"questions": unique_new})
        session.modified = True
        return jsonify({'success': True, 'count': len(unique_new)})

    except Exception as e:
        logger.error(f"Generate questions error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Processing error: {str(e)[:200]}'}), 500


@app.route('/api/dashboard-stats')
def dashboard_stats():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        user_id = session['user_id']
        user    = User.query.get(user_id)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        total_quizzes    = user.get_total_quizzes()
        average_score    = user.get_average_score()
        connection_count = user.get_connection_count()

        recent_results = QuizResult.query.filter_by(user_id=user_id).order_by(QuizResult.completed_at.desc()).limit(5).all()
        recent_activity = [{
            'quiz_title':   r.quiz.title,
            'score':        r.score,
            'completed_at': r.completed_at.strftime('%Y-%m-%d %H:%M:%S'),
            'time_ago':     get_time_ago(r.completed_at)
        } for r in recent_results]

        daily_scores = []
        for i in range(7):
            day       = datetime.utcnow() - timedelta(days=6-i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end   = day.replace(hour=23, minute=59, second=59, microsecond=999999)
            day_results = QuizResult.query.filter(
                QuizResult.user_id == user_id,
                QuizResult.completed_at >= day_start,
                QuizResult.completed_at <= day_end
            ).all()
            daily_scores.append({
                'day': day.strftime('%a'),
                'score': round(sum(r.score for r in day_results) / len(day_results)) if day_results else 0
            })

        return jsonify({
            'success': True,
            'stats': {'total_quizzes': total_quizzes, 'average_score': average_score, 'connection_count': connection_count},
            'recent_activity':  recent_activity,
            'performance_data': daily_scores
        })
    except Exception as e:
        logger.error(f'Error fetching dashboard stats: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


def get_time_ago(dt):
    seconds = (datetime.utcnow() - dt).total_seconds()
    if seconds < 60:     return f"{int(seconds)} seconds ago"
    if seconds < 3600:   return f"{int(seconds/60)} minutes ago"
    if seconds < 86400:  return f"{int(seconds/3600)} hours ago"
    if seconds < 604800: return f"{int(seconds/86400)} days ago"
    return dt.strftime('%Y-%m-%d')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})


@app.route('/api/find-study-buddies')
def find_study_buddies():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    user    = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    search_query   = request.args.get('search', '').lower()
    country_filter = request.args.get('country', '')
    school_filter  = request.args.get('school', '')
    level_filter   = request.args.get('level', '')

    query = User.query.filter(User.id != user_id, User.is_verified == True)
    if country_filter and country_filter != 'all':
        query = query.filter(User.country == country_filter)
    if school_filter and school_filter != 'all':
        query = query.filter(User.school == school_filter)
    if level_filter and level_filter != 'all':
        query = query.filter(User.study_level == level_filter)
    if search_query:
        query = query.filter((User.name.ilike(f'%{search_query}%')) | (User.username.ilike(f'%{search_query}%')))

    buddies      = query.limit(100).all()
    buddies_data = []
    for buddy in buddies:
        is_connected = Connection.query.filter(
            ((Connection.user_id == user_id) & (Connection.connected_user_id == buddy.id)) |
            ((Connection.user_id == buddy.id) & (Connection.connected_user_id == user_id))
        ).first() is not None

        priority = 0
        if buddy.country == user.country:         priority += 100
        if buddy.school == user.school:           priority += 50
        if buddy.study_level == user.study_level: priority += 25

        buddies_data.append({
            'id': buddy.id, 'name': buddy.name, 'username': buddy.username,
            'profile_pic': buddy.get_profile_pic_url(), 'school': buddy.school,
            'study_level': buddy.study_level, 'country': buddy.country,
            'tags': [t.tag for t in buddy.tags], 'total_quizzes': buddy.get_total_quizzes(),
            'average_score': buddy.get_average_score(), 'is_connected': is_connected, 'priority': priority
        })

    buddies_data.sort(key=lambda x: (-x['priority'], x['name']))
    for b in buddies_data:
        del b['priority']
    return jsonify({'success': True, 'buddies': buddies_data})


@app.route('/api/add-tag', methods=['POST'])
def add_tag():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data    = request.json
    tag     = data.get('tag', '').strip()
    if not tag or len(tag) > 50:
        return jsonify({'error': 'Invalid tag'}), 400
    user_id = session['user_id']
    if UserTag.query.filter_by(user_id=user_id, tag=tag).first():
        return jsonify({'error': 'Tag already exists'}), 400
    try:
        db.session.add(UserTag(user_id=user_id, tag=tag))
        db.session.commit()
        return jsonify({'success': True, 'message': f'Tag "{tag}" added!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/remove-tag/<int:tag_id>', methods=['DELETE'])
def remove_tag(tag_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    tag = UserTag.query.filter_by(id=tag_id, user_id=session['user_id']).first()
    if not tag:
        return jsonify({'error': 'Tag not found'}), 404
    try:
        db.session.delete(tag)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Tag removed'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-my-tags')
def get_my_tags():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    tags = UserTag.query.filter_by(user_id=session['user_id']).order_by(UserTag.created_at.desc()).all()
    return jsonify({'success': True, 'tags': [{'id': t.id, 'tag': t.tag} for t in tags]})


@app.route('/api/connect-user', methods=['POST'])
def connect_user():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data              = request.json
    connected_user_id = data.get('user_id')
    if not connected_user_id:
        return jsonify({'error': 'User ID required'}), 400
    user_id  = session['user_id']
    existing = Connection.query.filter(
        ((Connection.user_id == user_id) & (Connection.connected_user_id == connected_user_id)) |
        ((Connection.user_id == connected_user_id) & (Connection.connected_user_id == user_id))
    ).first()
    if existing:
        return jsonify({'error': 'Already connected with this user'}), 400
    try:
        db.session.add(Connection(user_id=user_id, connected_user_id=connected_user_id))
        db.session.add(Connection(user_id=connected_user_id, connected_user_id=user_id))
        db.session.commit()
        return jsonify({'success': True, 'message': 'Connected successfully!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/send-message', methods=['POST'])
def send_message_api():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data        = request.json
    receiver_id = data.get('receiver_id')
    content     = data.get('content', '').strip()
    if not receiver_id or not content:
        return jsonify({'error': 'Missing receiver or message content'}), 400
    if len(content) > 5000:
        return jsonify({'error': 'Message too long'}), 400
    sender_id    = session['user_id']
    is_connected = Connection.query.filter(
        ((Connection.user_id == sender_id) & (Connection.connected_user_id == receiver_id)) |
        ((Connection.user_id == receiver_id) & (Connection.connected_user_id == sender_id))
    ).first()
    if not is_connected:
        return jsonify({'error': 'You must be connected to message this user'}), 403
    try:
        message = Message(sender_id=sender_id, receiver_id=receiver_id, content=content)
        db.session.add(message)
        db.session.commit()
        return jsonify({'success': True, 'message': {
            'id': message.id, 'sender_id': sender_id, 'receiver_id': receiver_id,
            'content': content, 'created_at': message.created_at.isoformat()
        }})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-messages/<int:buddy_id>')
def get_messages(buddy_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id  = session['user_id']
    messages = Message.query.filter(
        ((Message.sender_id == user_id) & (Message.receiver_id == buddy_id)) |
        ((Message.sender_id == buddy_id) & (Message.receiver_id == user_id))
    ).order_by(Message.created_at.asc()).all()
    Message.query.filter(
        (Message.sender_id == buddy_id) & (Message.receiver_id == user_id) & (Message.is_read == False)
    ).update({Message.is_read: True})
    db.session.commit()
    return jsonify({'success': True, 'messages': [{
        'id': m.id, 'sender_id': m.sender_id, 'sender_name': m.sender.name,
        'receiver_id': m.receiver_id, 'content': m.content,
        'is_read': m.is_read, 'created_at': m.created_at.isoformat()
    } for m in messages]})


@app.route('/api/get-connections')
def get_connections():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id   = session['user_id']
    initiated = Connection.query.filter_by(user_id=user_id).all()
    received  = Connection.query.filter_by(connected_user_id=user_id).all()
    connections_data, seen_ids = [], set()

    def _append(buddy, conn_ts):
        unread = Message.query.filter(
            (Message.sender_id == buddy.id) & (Message.receiver_id == user_id) & (Message.is_read == False)
        ).count()
        connections_data.append({
            'id': buddy.id, 'name': buddy.name, 'username': buddy.username,
            'profile_pic': buddy.get_profile_pic_url(), 'study_level': buddy.study_level,
            'average_score': buddy.get_average_score(), 'tags': [t.tag for t in buddy.tags],
            'unread_count': unread, 'connected_at': conn_ts
        })

    for conn in initiated:
        if conn.connected_user_id not in seen_ids:
            seen_ids.add(conn.connected_user_id)
            _append(conn.connected_user, conn.created_at.isoformat())
    for conn in received:
        if conn.user_id not in seen_ids:
            seen_ids.add(conn.user_id)
            _append(conn.user, conn.created_at.isoformat())
    return jsonify({'success': True, 'connections': connections_data})


@app.route('/api/discussions')
def get_discussions():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    discussions = [
        {'id': 1, 'title': 'Understanding Calculus Derivatives', 'description': "Let's discuss how derivatives work", 'members': 12, 'messages': 45},
        {'id': 2, 'title': 'Physics Mechanics Help', 'description': "Need help with Newton's laws", 'members': 8, 'messages': 23},
        {'id': 3, 'title': 'Chemistry Reactions', 'description': 'Balancing equations and understanding reactions', 'members': 15, 'messages': 67}
    ]
    return jsonify({'success': True, 'discussions': discussions})


# ══════════════════════════════════════════════════════════════════════════════
#  ENHANCED AI CHAT — PDF + Image + Voice + YouTube
# ══════════════════════════════════════════════════════════════════════════════

def _build_youtube_query(question, explanation):
    """Generate a concise YouTube search query from the question + explanation topic."""
    try:
        prompt = (
            f"Given this study question and answer, generate a short (4-7 word) YouTube search query "
            f"that would find a helpful educational video about the topic.\n\n"
            f"Question: {question[:200]}\n\n"
            f"Answer summary: {explanation[:300]}\n\n"
            f"Return ONLY the search query text, nothing else."
        )
        resp = model.generate_content(prompt)
        return resp.text.strip().strip('"').strip("'")
    except Exception:
        return question[:60] if question else "study explanation"


@app.route('/api/ask-ai', methods=['POST'])
def ask_ai():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    # ── Parse incoming request (multipart or JSON) ─────────────────────────
    is_multipart = request.content_type and 'multipart/form-data' in request.content_type

    if is_multipart:
        question      = request.form.get('question', '').strip()
        reset_conv    = request.form.get('reset', '').lower() in ('true', '1')
        response_mode = request.form.get('response_mode', 'text')   # 'text' | 'audio' | 'youtube'
    else:
        data          = request.get_json(silent=True) or {}
        question      = data.get('question', '').strip()
        reset_conv    = data.get('reset', False)
        response_mode = data.get('response_mode', 'text')

    if not question and not is_multipart:
        return jsonify({'error': 'Please provide a question'}), 400

    try:
        # ── Conversation history ───────────────────────────────────────────
        if reset_conv or 'ai_conversation' not in session:
            conversation_history       = []
            session['ai_conversation'] = []
        else:
            conversation_history = session.get('ai_conversation', [])

        context = ""
        if conversation_history:
            context = "Previous conversation:\n"
            for i, ex in enumerate(conversation_history[-6:], 1):   # last 6 turns
                context += f"\nQ{i}: {ex['question']}\nA{i}: {ex['answer']}\n"
            context += "\n---\n\n"

        # ── System prompt ──────────────────────────────────────────────────
        system_prompt = (
            "You are Brainspark AI, an expert study tutor. "
            "Explain concepts clearly with examples. Use **bold** for key terms. "
            "If the user says they don't understand, rephrase with a simpler analogy. "
            "If you analyze an image or PDF, describe what you see and explain it educationally. "
            "Be warm, encouraging, and concise."
        )

        # ── Collect content parts for Gemini ──────────────────────────────
        content_parts = []

        # System + context + question text
        full_text_prompt = f"{system_prompt}\n\n{context}"
        if question:
            full_text_prompt += f"Student's question: {question}"
        else:
            full_text_prompt += "Please analyze the attached file(s) and explain the key concepts in a clear, educational way."

        content_parts.append(full_text_prompt)

        has_pdf   = False
        has_image = False

        # ── PDF attachment ─────────────────────────────────────────────────
        if is_multipart and 'pdf' in request.files:
            pdf_file = request.files['pdf']
            if pdf_file and pdf_file.filename:
                pdf_text = extract_pdf_text(pdf_file)
                if pdf_text:
                    has_pdf = True
                    content_parts.append(f"\n\n[PDF CONTENT — analyze and explain this]\n{pdf_text[:5000]}")

        # ── Image attachment ───────────────────────────────────────────────
        if is_multipart and 'image' in request.files:
            img_file = request.files['image']
            if img_file and img_file.filename:
                try:
                    img_bytes = img_file.read()
                    ext       = img_file.filename.rsplit('.', 1)[-1].lower()
                    mime_map  = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                                 'gif': 'image/gif', 'webp': 'image/webp'}
                    mime_type = mime_map.get(ext, 'image/jpeg')
                    # Gemini vision: pass image as inline data
                    content_parts.append({
                        'mime_type': mime_type,
                        'data': img_bytes
                    })
                    has_image = True
                except Exception as img_err:
                    logger.warning(f"Image processing error: {img_err}")

        # ── Voice note (transcription fallback) ───────────────────────────
        if is_multipart and 'voice_note' in request.files:
            # We can't do audio transcription without Whisper, so we acknowledge
            content_parts[0] += (
                "\n\n[Note: The user sent a voice note. Since audio transcription is not available, "
                "please respond: 'I received your voice note! Unfortunately I can\'t process audio directly. "
                "Please type your question and I\'ll be happy to help.' Then stop.]"
            )

        # ── Generate response ──────────────────────────────────────────────
        if has_image:
            # Use vision-capable call
            response = vision_model.generate_content(content_parts)
        else:
            # Text-only (content_parts is list of strings; join them)
            joined_prompt = "\n".join(str(p) for p in content_parts if isinstance(p, str))
            response      = model.generate_content(joined_prompt)

        explanation = response.text.strip()

        # ── YouTube query (only for 'youtube' mode) ────────────────────────
        # ── YouTube query (only for 'youtube' mode) ────────────────────────
        youtube_query = None
        if response_mode == 'youtube':
            q = question.strip() if question.strip() else explanation[:60]
            youtube_query = q[:80]

        # ── Save to conversation history ───────────────────────────────────
        conversation_history.append({
            'question': question or '[file attachment]',
            'answer':   explanation[:500]   # store truncated
        })
        session['ai_conversation'] = conversation_history[-20:]   # keep last 20
        session.modified = True

        return jsonify({
            'success':          True,
            'explanation':      explanation,
            'youtube_query':    youtube_query,
            'has_pdf':          has_pdf,
            'has_image':        has_image,
            'conversation_count': len(conversation_history)
        })

    except Exception as e:
        logger.error(f'Error in ask-ai: {str(e)}', exc_info=True)
        return jsonify({'error': f'AI error: {str(e)[:200]}'}), 500


# ── Group chat + brainstorm routes (unchanged) ────────────────────────────────

@app.route('/api/create-group', methods=['POST'])
def create_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id     = session['user_id']
    data        = request.json
    name        = data.get('name', '').strip()
    description = data.get('description', '').strip()
    is_private  = data.get('is_private', False)
    password    = data.get('password', '')
    if not name or len(name) < 3:
        return jsonify({'error': 'Group name must be at least 3 characters'}), 400
    try:
        group = ChatGroup(name=name, description=description, created_by=user_id, is_private=is_private)
        if is_private and password:
            group.set_password(password)
        db.session.add(group)
        db.session.flush()
        db.session.add(ChatGroupMember(group_id=group.id, user_id=user_id, role='admin'))
        db.session.commit()
        return jsonify({'success': True, 'group_id': group.id, 'message': 'Group created successfully!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-groups')
def get_groups():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id     = session['user_id']
    memberships = ChatGroupMember.query.filter_by(user_id=user_id).all()
    groups_data = []
    for m in memberships:
        g = m.group
        groups_data.append({
            'id': g.id, 'name': g.name, 'description': g.description,
            'is_private': g.is_private, 'created_by': g.created_by,
            'creator_name':  g.creator.name,
            'member_count':  ChatGroupMember.query.filter_by(group_id=g.id).count(),
            'message_count': GroupMessage.query.filter_by(group_id=g.id).count(),
            'your_role': m.role, 'created_at': g.created_at.isoformat()
        })
    return jsonify({'success': True, 'groups': groups_data})


@app.route('/api/discover-groups')
def discover_groups():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id        = session['user_id']
    user_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(user_id=user_id).all()]
    groups_data    = []
    for g in ChatGroup.query.all():
        if g.id in user_group_ids:
            continue
        pending = GroupJoinRequest.query.filter_by(group_id=g.id, user_id=user_id, status='pending').first()
        groups_data.append({
            'id': g.id, 'name': g.name, 'description': g.description,
            'is_private': g.is_private, 'created_by': g.created_by,
            'creator_name':  g.creator.name,
            'member_count':  ChatGroupMember.query.filter_by(group_id=g.id).count(),
            'message_count': GroupMessage.query.filter_by(group_id=g.id).count(),
            'has_pending_request': pending is not None,
            'created_at': g.created_at.isoformat()
        })
    return jsonify({'success': True, 'groups': groups_data})


@app.route('/api/search-groups')
def search_groups():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    q       = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'error': 'Search query must be at least 2 characters'}), 400
    user_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(user_id=user_id).all()]
    groups_data    = []
    for g in ChatGroup.query.all():
        if g.id in user_group_ids:
            continue
        if q.lower() not in g.name.lower() and q.lower() not in (g.description or '').lower():
            continue
        pending = GroupJoinRequest.query.filter_by(group_id=g.id, user_id=user_id, status='pending').first()
        groups_data.append({
            'id': g.id, 'name': g.name, 'description': g.description,
            'is_private': g.is_private, 'created_by': g.created_by,
            'creator_name':  g.creator.name,
            'member_count':  ChatGroupMember.query.filter_by(group_id=g.id).count(),
            'message_count': GroupMessage.query.filter_by(group_id=g.id).count(),
            'has_pending_request': pending is not None,
            'created_at': g.created_at.isoformat()
        })
    return jsonify({'success': True, 'groups': groups_data})


@app.route('/api/add-member-to-group', methods=['POST'])
def add_member_to_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id        = session['user_id']
    data           = request.json
    group_id       = data.get('group_id')
    target_user_id = data.get('user_id', user_id)
    password       = data.get('password')
    if not group_id:
        return jsonify({'error': 'Group ID required'}), 400
    group = ChatGroup.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    current_member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if user_id == target_user_id:
        if current_member:
            return jsonify({'error': 'Already a member of this group'}), 400
        if group.is_private:
            if not password or not group.check_password(password):
                return jsonify({'error': 'Invalid group password'}), 401
            if GroupJoinRequest.query.filter_by(group_id=group_id, user_id=user_id).first():
                return jsonify({'error': 'Join request already pending'}), 400
            db.session.add(GroupJoinRequest(group_id=group_id, user_id=user_id))
            db.session.commit()
            return jsonify({'success': True, 'message': 'Join request sent!'})
        else:
            db.session.add(ChatGroupMember(group_id=group_id, user_id=user_id, role='member'))
            db.session.commit()
            return jsonify({'success': True, 'message': 'Joined group successfully!'})
    else:
        if not current_member or current_member.role != 'admin':
            return jsonify({'error': 'Only admins can add members'}), 403
        if ChatGroupMember.query.filter_by(group_id=group_id, user_id=target_user_id).first():
            return jsonify({'error': 'User already in group'}), 400
        is_connected = Connection.query.filter(
            ((Connection.user_id == user_id) & (Connection.connected_user_id == target_user_id)) |
            ((Connection.user_id == target_user_id) & (Connection.connected_user_id == user_id))
        ).first()
        if not is_connected:
            return jsonify({'error': 'You can only add connected users'}), 403
        db.session.add(ChatGroupMember(group_id=group_id, user_id=target_user_id, role='member'))
        db.session.commit()
        return jsonify({'success': True, 'message': 'Member added successfully!'})


@app.route('/api/remove-member-from-group', methods=['POST'])
def remove_member_from_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id        = session['user_id']
    data           = request.json
    group_id       = data.get('group_id')
    target_user_id = data.get('user_id')
    group          = ChatGroup.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    admin_member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not admin_member or admin_member.role != 'admin':
        return jsonify({'error': 'Only admins can remove members'}), 403
    if target_user_id == group.created_by:
        return jsonify({'error': 'Cannot remove group creator'}), 403
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=target_user_id).first()
    if not member:
        return jsonify({'error': 'Member not found'}), 404
    db.session.delete(member)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Member removed successfully!'})


@app.route('/api/get-group-members/<int:group_id>')
def get_group_members(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    members = ChatGroupMember.query.filter_by(group_id=group_id).all()
    return jsonify({'success': True, 'members': [{
        'id': m.user.id, 'name': m.user.name, 'username': m.user.username,
        'profile_pic': m.user.get_profile_pic_url(), 'role': m.role,
        'joined_at': m.joined_at.isoformat()
    } for m in members]})


@app.route('/api/send-group-message', methods=['POST'])
def send_group_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    if request.content_type and 'multipart/form-data' in request.content_type:
        group_id     = request.form.get('group_id')
        content      = request.form.get('content', '').strip()
        message_type = request.form.get('message_type', 'text')
    else:
        data         = request.json or {}
        group_id     = data.get('group_id')
        content      = data.get('content', '').strip()
        message_type = data.get('message_type', 'text')
    if not group_id:
        return jsonify({'error': 'Group ID required'}), 400
    if not content and message_type == 'text':
        return jsonify({'error': 'Content required for text messages'}), 400
    if len(content) > 5000:
        return jsonify({'error': 'Message too long'}), 400
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403
    try:
        image_path, pdf_path = None, None
        if message_type == 'image' and 'image' in request.files:
            f = request.files['image']
            if f and f.filename and allowed_file(f.filename):
                os.makedirs('uploads/group_chat', exist_ok=True)
                ts  = datetime.utcnow().timestamp()
                ext = f.filename.rsplit('.', 1)[1].lower()
                image_path = f"group_{group_id}_{user_id}_{ts}.{ext}"
                f.save(os.path.join('uploads/group_chat', image_path))
        if message_type == 'pdf' and 'pdf' in request.files:
            f = request.files['pdf']
            if f and f.filename and allowed_file(f.filename):
                os.makedirs('uploads/group_chat', exist_ok=True)
                ts  = datetime.utcnow().timestamp()
                ext = f.filename.rsplit('.', 1)[1].lower()
                pdf_path = f"group_{group_id}_{user_id}_{ts}.{ext}"
                f.save(os.path.join('uploads/group_chat', pdf_path))
        msg = GroupMessage(
            group_id=group_id, sender_id=user_id, content=content,
            message_type=message_type, image_path=image_path, pdf_path=pdf_path
        )
        db.session.add(msg)
        db.session.commit()
        return jsonify({'success': True, 'message': {
            'id': msg.id, 'sender_id': user_id, 'sender_name': msg.sender.name,
            'content': content, 'message_type': message_type,
            'image_url': f'/uploads/group_chat/{image_path}' if image_path else None,
            'pdf_url':   f'/uploads/group_chat/{pdf_path}'   if pdf_path   else None,
            'created_at': msg.created_at.isoformat()
        }})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-group-messages/<int:group_id>')
def get_group_messages(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    member  = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403
    messages      = GroupMessage.query.filter_by(group_id=group_id).order_by(GroupMessage.created_at.asc()).all()
    messages_data = []
    for msg in messages:
        msg_data = {
            'id': msg.id, 'sender_id': msg.sender_id, 'sender_name': msg.sender.name,
            'sender_pic': msg.sender.get_profile_pic_url(), 'content': msg.content,
            'message_type': msg.message_type,
            'image_url': f'/uploads/group_chat/{msg.image_path}' if msg.image_path else None,
            'pdf_url':   f'/uploads/group_chat/{msg.pdf_path}'   if msg.pdf_path   else None,
            'created_at': msg.created_at.isoformat(), 'is_sent': msg.sender_id == user_id
        }
        if msg.message_type == 'poll' and msg.poll_id:
            poll = Poll.query.get(msg.poll_id)
            if poll:
                options_data = []
                for opt in poll.options:
                    options_data.append({
                        'id': opt.id, 'text': opt.option_text, 'votes': len(opt.votes),
                        'has_voted': PollVote.query.filter_by(option_id=opt.id, user_id=user_id).first() is not None
                    })
                msg_data['poll'] = {
                    'id': poll.id, 'question': poll.question, 'is_active': poll.is_active,
                    'options': options_data, 'total_votes': sum(o['votes'] for o in options_data)
                }
        messages_data.append(msg_data)
    return jsonify({'success': True, 'messages': messages_data, 'current_user_id': user_id})


@app.route('/api/create-poll', methods=['POST'])
def create_poll():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id  = session['user_id']
    data     = request.json
    group_id = data.get('group_id')
    question = data.get('question', '').strip()
    options  = data.get('options', [])
    if not group_id or not question:
        return jsonify({'error': 'Group ID and question required'}), 400
    if len(options) < 2 or len(options) > 6:
        return jsonify({'error': 'Poll must have 2-6 options'}), 400
    if not ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first():
        return jsonify({'error': 'You are not a member of this group'}), 403
    try:
        poll = Poll(group_id=group_id, creator_id=user_id, question=question)
        db.session.add(poll)
        db.session.flush()
        for opt_text in options:
            if opt_text.strip():
                db.session.add(PollOption(poll_id=poll.id, option_text=opt_text.strip()))
        poll_msg = GroupMessage(group_id=group_id, sender_id=user_id, content=f"📊 Poll: {question}", message_type='poll', poll_id=poll.id)
        db.session.add(poll_msg)
        db.session.commit()
        return jsonify({'success': True, 'poll_id': poll.id, 'message_id': poll_msg.id, 'message': 'Poll created successfully!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-polls/<int:group_id>')
def get_polls(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    if not ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first():
        return jsonify({'error': 'You are not a member of this group'}), 403
    polls      = Poll.query.filter_by(group_id=group_id, is_active=True).order_by(Poll.created_at.desc()).all()
    polls_data = []
    for poll in polls:
        options_data = [{
            'id': opt.id, 'text': opt.option_text, 'votes': len(opt.votes),
            'has_voted': PollVote.query.filter_by(option_id=opt.id, user_id=user_id).first() is not None
        } for opt in poll.options]
        polls_data.append({
            'id': poll.id, 'question': poll.question, 'creator_name': poll.creator.name,
            'options': options_data, 'total_votes': sum(o['votes'] for o in options_data),
            'created_at': poll.created_at.isoformat()
        })
    return jsonify({'success': True, 'polls': polls_data})


@app.route('/api/vote-poll', methods=['POST'])
def vote_poll():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id   = session['user_id']
    data      = request.json
    poll_id   = data.get('poll_id')
    option_id = data.get('option_id')
    if not poll_id or not option_id:
        return jsonify({'error': 'Poll ID and option ID required'}), 400
    poll = Poll.query.get(poll_id)
    if not poll or not poll.is_active:
        return jsonify({'error': 'Poll not found or inactive'}), 404
    if not ChatGroupMember.query.filter_by(group_id=poll.group_id, user_id=user_id).first():
        return jsonify({'error': 'You are not a member of this group'}), 403
    existing_vote = PollVote.query.filter_by(poll_id=poll_id, user_id=user_id).first()
    if existing_vote:
        existing_vote.option_id = option_id
        db.session.commit()
        return jsonify({'success': True, 'message': 'Vote updated!'})
    try:
        db.session.add(PollVote(poll_id=poll_id, option_id=option_id, user_id=user_id))
        db.session.commit()
        return jsonify({'success': True, 'message': 'Vote cast!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/ask-ai-group', methods=['POST'])
def ask_ai_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if request.content_type and 'multipart/form-data' in request.content_type:
        question = request.form.get('question', '')
        group_id = request.form.get('group_id')
        context  = request.form.get('context', '')
    else:
        data     = request.json or {}
        question = data.get('question', '')
        group_id = data.get('group_id')
        context  = data.get('context', '')
    if not question:
        return jsonify({'error': 'Please provide a question'}), 400
    if group_id:
        if not ChatGroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first():
            return jsonify({'error': 'You are not a member of this group'}), 403
    try:
        prompt = f"""You are an AI Brainstorm Helper for a study group.
Group Context: {context or 'General study group discussion'}
User Question: {question}
Provide a helpful, clear, and actionable response. Keep it concise but thorough."""
        if 'image' in request.files:
            f = request.files['image']
            if f and f.filename and allowed_file(f.filename):
                try:
                    from PIL import Image as PILImage
                    image_data = f.read()
                    image      = PILImage.open(io.BytesIO(image_data))
                    response   = vision_model.generate_content([prompt, image])
                except Exception as img_error:
                    logger.warning(f'Image processing failed: {img_error}')
                    response   = model.generate_content(prompt)
            else:
                response = model.generate_content(prompt)
        else:
            response = model.generate_content(prompt)
        return jsonify({'success': True, 'explanation': response.text})
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500


@app.route('/api/schedule-brainstorm', methods=['POST'])
def schedule_brainstorm():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id        = session['user_id']
    data           = request.json
    group_id       = data.get('group_id')
    title          = data.get('title', '').strip()
    description    = data.get('description', '').strip()
    scheduled_time = data.get('scheduled_time')
    if not group_id or not title or not scheduled_time:
        return jsonify({'error': 'Group ID, title, and scheduled time required'}), 400
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member or member.role != 'admin':
        return jsonify({'error': 'Only group admins can schedule sessions'}), 403
    try:
        sess_obj = BrainstormSession(
            group_id=group_id, title=title, description=description,
            scheduled_time=datetime.fromisoformat(scheduled_time)
        )
        db.session.add(sess_obj)
        db.session.flush()
        group_members = ChatGroupMember.query.filter_by(group_id=group_id).all()
        for mr in group_members:
            if mr.user_id != user_id:
                db.session.add(GroupMessage(
                    group_id=group_id, sender_id=user_id,
                    content=f"📅 Brainstorm Session Scheduled!\n\n{title}\n\n{description}\n\nTime: {scheduled_time}\n\nJoin us!"
                ))
        db.session.commit()
        return jsonify({'success': True, 'session_id': sess_obj.id, 'message': f'Session scheduled! Notified {len(group_members)} members'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-group-sessions/<int:group_id>')
def get_group_sessions(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    sessions = BrainstormSession.query.filter_by(group_id=group_id).order_by(BrainstormSession.scheduled_time.desc()).all()
    return jsonify({'success': True, 'sessions': [{
        'id': s.id, 'title': s.title, 'description': s.description,
        'scheduled_time': s.scheduled_time.isoformat(), 'status': s.status,
        'note_count': len(s.notes), 'created_at': s.created_at.isoformat()
    } for s in sessions]})


@app.route('/api/add-brainstorm-note', methods=['POST'])
def add_brainstorm_note():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id    = session['user_id']
    data       = request.json
    session_id = data.get('session_id')
    content    = data.get('content', '').strip()
    mentions   = data.get('mentions', [])
    tags       = data.get('tags', [])
    if not session_id or not content:
        return jsonify({'error': 'Session ID and content required'}), 400
    sess_obj = BrainstormSession.query.get(session_id)
    if not sess_obj:
        return jsonify({'error': 'Session not found'}), 404
    if not ChatGroupMember.query.filter_by(group_id=sess_obj.group_id, user_id=user_id).first():
        return jsonify({'error': 'You are not a member of this group'}), 403
    try:
        note = BrainstormNote(
            session_id=session_id, user_id=user_id, content=content,
            mentions=json.dumps(mentions) if mentions else None,
            tags=json.dumps(tags) if tags else None
        )
        db.session.add(note)
        db.session.commit()
        return jsonify({'success': True, 'note': {
            'id': note.id, 'user_name': note.user.name, 'content': content,
            'mentions': mentions, 'tags': tags, 'created_at': note.created_at.isoformat()
        }})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-brainstorm-notes/<int:session_id>')
def get_brainstorm_notes(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    notes = BrainstormNote.query.filter_by(session_id=session_id).order_by(BrainstormNote.created_at.asc()).all()
    return jsonify({'success': True, 'notes': [{
        'id': n.id, 'user_id': n.user_id, 'user_name': n.user.name,
        'user_pic': n.user.get_profile_pic_url(), 'content': n.content,
        'mentions':  json.loads(n.mentions)  if n.mentions  else [],
        'tags':      json.loads(n.tags)      if n.tags      else [],
        'mention_ai': n.mention_ai, 'has_media': n.has_media,
        'image_url':    f'/uploads/{n.image_path}'    if n.image_path    else None,
        'textbook_url': f'/uploads/{n.textbook_path}' if n.textbook_path else None,
        'solved_problem': n.solved_problem, 'created_at': n.created_at.isoformat()
    } for n in notes]})


@app.route('/api/accept-join-request', methods=['POST'])
def accept_join_request():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id      = session['user_id']
    request_id   = request.json.get('request_id')
    join_request = GroupJoinRequest.query.get(request_id)
    if not join_request:
        return jsonify({'error': 'Request not found'}), 404
    admin_member = ChatGroupMember.query.filter_by(group_id=join_request.group_id, user_id=user_id).first()
    if not admin_member or admin_member.role != 'admin':
        return jsonify({'error': 'Only admins can accept requests'}), 403
    try:
        db.session.add(ChatGroupMember(group_id=join_request.group_id, user_id=join_request.user_id, role='member'))
        join_request.status       = 'approved'
        join_request.responded_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Request accepted!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/reject-join-request', methods=['POST'])
def reject_join_request():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id      = session['user_id']
    request_id   = request.json.get('request_id')
    join_request = GroupJoinRequest.query.get(request_id)
    if not join_request:
        return jsonify({'error': 'Request not found'}), 404
    admin_member = ChatGroupMember.query.filter_by(group_id=join_request.group_id, user_id=user_id).first()
    if not admin_member or admin_member.role != 'admin':
        return jsonify({'error': 'Only admins can reject requests'}), 403
    try:
        join_request.status       = 'rejected'
        join_request.responded_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Request rejected!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-pending-join-requests')
def get_pending_join_requests():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id         = session['user_id']
    admin_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(user_id=user_id, role='admin').all()]
    pending = GroupJoinRequest.query.filter(
        GroupJoinRequest.group_id.in_(admin_group_ids),
        GroupJoinRequest.status == 'pending'
    ).order_by(GroupJoinRequest.created_at.desc()).all()
    return jsonify({'success': True, 'requests': [{
        'id': r.id, 'group_id': r.group_id, 'group_name': r.group.name,
        'user_id': r.user_id, 'user_name': r.user.name, 'user_username': r.user.username,
        'user_pic': r.user.get_profile_pic_url(), 'created_at': r.created_at.isoformat()
    } for r in pending]})


@app.route('/api/get-my-join-requests')
def get_my_join_requests():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    reqs = GroupJoinRequest.query.filter_by(user_id=session['user_id']).order_by(GroupJoinRequest.created_at.desc()).all()
    return jsonify({'success': True, 'requests': [{
        'id': r.id, 'group_id': r.group_id, 'group_name': r.group.name,
        'status': r.status, 'created_at': r.created_at.isoformat(),
        'responded_at': r.responded_at.isoformat() if r.responded_at else None
    } for r in reqs]})


@app.route('/api/get-unread-notifications')
def get_unread_notifications():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id               = session['user_id']
    unread_messages_count = Message.query.filter_by(receiver_id=user_id, is_read=False).count()
    unread_messages       = Message.query.filter_by(receiver_id=user_id, is_read=False).order_by(Message.created_at.desc()).limit(5).all()
    messages_data = [{
        'id': m.id, 'type': 'message', 'sender_id': m.sender_id,
        'sender_name': m.sender.name, 'sender_pic': m.sender.get_profile_pic_url(),
        'content': (m.content[:50] + '...') if len(m.content) > 50 else m.content,
        'created_at': m.created_at.isoformat()
    } for m in unread_messages]
    admin_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(user_id=user_id, role='admin').all()]
    pending_reqs = GroupJoinRequest.query.filter(
        GroupJoinRequest.group_id.in_(admin_group_ids),
        GroupJoinRequest.status == 'pending'
    ).order_by(GroupJoinRequest.created_at.desc()).limit(5).all()
    requests_data = [{
        'id': r.id, 'type': 'join_request', 'request_id': r.id,
        'group_id': r.group_id, 'group_name': r.group.name,
        'user_id': r.user_id, 'user_name': r.user.name,
        'user_pic': r.user.get_profile_pic_url(), 'created_at': r.created_at.isoformat()
    } for r in pending_reqs]
    all_notifications = sorted(messages_data + requests_data, key=lambda x: x['created_at'], reverse=True)
    return jsonify({
        'success': True,
        'unread_messages_count':  unread_messages_count,
        'pending_requests_count': len(pending_reqs),
        'total_notifications':    unread_messages_count + len(pending_reqs),
        'notifications':          all_notifications
    })


@app.route('/api/save-quiz-result', methods=['POST'])
def save_quiz_result():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        data       = request.json
        score      = data.get('score')
        time_taken = data.get('time_taken')
        answers    = data.get('answers', {})
        if score is None:
            return jsonify({'success': False, 'error': 'Score is required'}), 400
        user_id = session['user_id']
        user    = User.query.get(user_id)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        quiz = Quiz.query.first()
        if not quiz:
            quiz = Quiz(
                title='Generated Quiz', description='Quiz generated from uploaded PDF',
                subject='General', difficulty='medium', question_count=10, time_limit=300
            )
            db.session.add(quiz)
            db.session.flush()
        result = QuizResult(
            user_id=user_id, quiz_id=quiz.id, score=score,
            answers=json.dumps(answers), time_taken=time_taken,
            completed_at=datetime.utcnow()
        )
        db.session.add(result)
        db.session.commit()
        if 'quiz_questions' in session:
            del session['quiz_questions']
            session.modified = True
        return jsonify({'success': True, 'message': 'Quiz result saved successfully!', 'result_id': result.id}), 200
    except Exception as e:
        logger.error(f'Error saving quiz result: {str(e)}', exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


@app.route('/debug/db-status')
def debug_db_status():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        user_id      = session['user_id']
        user         = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        quiz_results = QuizResult.query.filter_by(user_id=user_id).all()
        return jsonify({
            'success': True, 'user_id': user_id, 'user_name': user.name,
            'total_quiz_results_in_db': QuizResult.query.count(),
            'user_quiz_results_count':  len(quiz_results),
            'user_quiz_results': [{
                'id': r.id, 'score': r.score, 'time_taken': r.time_taken,
                'completed_at': r.completed_at.isoformat() if r.completed_at else None
            } for r in quiz_results],
            'user_stats': {
                'total_quizzes':    user.get_total_quizzes(),
                'average_score':    user.get_average_score(),
                'connection_count': user.get_connection_count()
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    return send_from_directory(uploads_dir, filename)
@app.route('/legal')
def legal():
    return render_template('legal.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

# ══════════════════════════════════════════════════════════════════════════════
#  SETTINGS PAGE
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/settings')
def settings():
    if 'user_id' not in session:
        flash('Please login first')
        return redirect(url_for('index'))
    user  = User.query.get(session['user_id'])
    theme = session.get('theme', 'dark')
    return render_template('settings.html', user=user, theme=theme)


@app.route('/api/update-settings', methods=['POST'])
def update_settings():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data    = request.get_json(silent=True) or {}
    stype   = data.get('type')
    user_id = session['user_id']
    user    = User.query.get(user_id)

    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    # ── Profile ──────────────────────────────────────────────────
    if stype == 'profile':
        import re
        name       = data.get('name', '').strip()
        username   = data.get('username', '').strip()
        profession = data.get('profession', '').strip()

        if not name:
            return jsonify({'success': False, 'error': 'Full name is required'})
        if not username:
            return jsonify({'success': False, 'error': 'Username is required'})
        if not re.match(r'^[a-zA-Z0-9_]{3,50}$', username):
            return jsonify({'success': False, 'error': 'Invalid username format'})

        # Check username uniqueness (excluding current user)
        existing = User.query.filter(
            db.func.lower(User.username) == username.lower(),
            User.id != user_id
        ).first()
        if existing:
            return jsonify({'success': False, 'error': 'That username is already taken'})

        try:
            user.name       = name
            user.username   = username
            user.profession = profession if profession else None
            db.session.commit()
            session['username'] = username
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            logger.error(f"Profile update error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': 'Database error. Please try again.'})

    # ── Password ──────────────────────────────────────────────────
    elif stype == 'password':
        import re
        current_pw = data.get('current_password', '')
        new_pw     = data.get('new_password', '')

        if not current_pw or not new_pw:
            return jsonify({'success': False, 'error': 'Both current and new passwords are required'})
        if not user.check_password(current_pw):
            return jsonify({'success': False, 'error': 'Current password is incorrect'})
        if len(new_pw) < 8:
            return jsonify({'success': False, 'error': 'New password must be at least 8 characters'})
        if not re.search(r'[A-Z]', new_pw):
            return jsonify({'success': False, 'error': 'New password needs at least one uppercase letter'})
        if not re.search(r'\d', new_pw):
            return jsonify({'success': False, 'error': 'New password needs at least one number'})
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', new_pw):
            return jsonify({'success': False, 'error': 'New password needs at least one special character'})

        try:
            user.set_password(new_pw)
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            logger.error(f"Password update error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': 'Database error. Please try again.'})

    # ── Academic ──────────────────────────────────────────────────
    elif stype == 'academic':
        school      = data.get('school', '').strip()
        study_level = data.get('study_level', '').strip()
        country     = data.get('country', '').strip()

        if not school:
            return jsonify({'success': False, 'error': 'School name is required'})
        if not study_level:
            return jsonify({'success': False, 'error': 'Study level is required'})
        if not country:
            return jsonify({'success': False, 'error': 'Country is required'})

        valid_levels = ['High School', 'Undergraduate', 'Postgraduate', 'PhD', 'Professional', 'Self-learner']
        if study_level not in valid_levels:
            return jsonify({'success': False, 'error': 'Invalid study level'})

        try:
            user.school      = school
            user.study_level = study_level
            user.country     = country
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            logger.error(f"Academic update error: {e}", exc_info=True)
            return jsonify({'success': False, 'error': 'Database error. Please try again.'})

    # ── Email (blocked) ───────────────────────────────────────────
    elif stype == 'email':
        return jsonify({'success': False, 'error': 'Email changes are not permitted. Please contact support.'})

    else:
        return jsonify({'success': False, 'error': 'Unknown settings type'}), 400


@app.route('/api/update-profile-pic', methods=['POST'])
def update_profile_pic():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    if 'profile_pic' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'})

    file = request.files['profile_pic']
    if not file or not file.filename:
        return jsonify({'success': False, 'error': 'No file selected'})

    if not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'Invalid file type. Use JPG, PNG, WEBP, or GIF.'})

    if file.content_length and file.content_length > 5 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'File too large. Max 5MB.'})

    # Read and check actual size
    file_bytes = file.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'File too large. Max 5MB.'})

    try:
        user_id  = session['user_id']
        user     = User.query.get(user_id)
        ext      = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{user_id}.{ext}"
        save_dir = app.config['PROFILE_UPLOAD_FOLDER']
        os.makedirs(save_dir, exist_ok=True)

        # Remove old profile pic if it has a different extension
        if user.profile_pic and user.profile_pic != filename:
            old_path = os.path.join(save_dir, user.profile_pic)
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    pass

        filepath = os.path.join(save_dir, filename)
        with open(filepath, 'wb') as f_out:
            f_out.write(file_bytes)

        user.profile_pic = filename
        db.session.commit()
        return jsonify({'success': True, 'url': user.get_profile_pic_url()})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Profile pic update error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Upload failed. Please try again.'})


@app.route('/api/delete-account', methods=['POST'])
def delete_account():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    user_id = session['user_id']
    user    = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    try:
        # Delete profile pic file
        if user.profile_pic:
            pic_path = os.path.join(app.config['PROFILE_UPLOAD_FOLDER'], user.profile_pic)
            if os.path.exists(pic_path):
                try:
                    os.remove(pic_path)
                except Exception:
                    pass

        # SQLAlchemy cascade should handle related rows if your models have
        # cascade="all, delete-orphan". If not, delete manually:
        Message.query.filter(
            (Message.sender_id == user_id) | (Message.receiver_id == user_id)
        ).delete(synchronize_session=False)

        Connection.query.filter(
            (Connection.user_id == user_id) | (Connection.connected_user_id == user_id)
        ).delete(synchronize_session=False)

        QuizResult.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        UserTag.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        GeneratedQuestion.query.filter_by(user_id=user_id).delete(synchronize_session=False)

        db.session.delete(user)
        db.session.commit()
        session.clear()
        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Account deletion error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Deletion failed. Please try again.'})
    
    
@app.route('/api/youtube-search')
def youtube_search():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'success': False, 'error': 'No query provided'}), 400
    
    api_key = os.getenv('YOUTUBE_API_KEY')
    if not api_key:
        return jsonify({'success': False, 'error': 'YouTube API not configured'}), 500
    
    try:
        response = req.get(
            'https://www.googleapis.com/youtube/v3/search',
            params={
                'part': 'snippet',
                'q': query,
                'type': 'video',
                'maxResults': 4,
                'relevanceLanguage': 'en',
                'safeSearch': 'strict',
                'key': api_key
            },
            timeout=10
        )
        data = response.json()
        
        if 'error' in data:
            return jsonify({'success': False, 'error': data['error'].get('message', 'YouTube API error')}), 500
        
        videos = []
        for item in data.get('items', []):
            video_id = item['id']['videoId']
            snippet  = item['snippet']
            videos.append({
                'video_id':    video_id,
                'title':       snippet['title'],
                'channel':     snippet['channelTitle'],
                'thumbnail':   snippet['thumbnails']['medium']['url'],
                'description': snippet['description'][:120] + '…' if len(snippet['description']) > 120 else snippet['description'],
                'url':         f'https://www.youtube.com/watch?v={video_id}'
            })
        
        return jsonify({'success': True, 'videos': videos, 'query': query})
    
    except Exception as e:
        logger.error(f"YouTube search error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to fetch videos'}), 500
    
@app.route('/similar-questions')
def similar_questions_page():
    if 'user_id' not in session:
        flash('Please login first')
        return redirect(url_for('index'))
    user  = User.query.get(session['user_id'])
    theme = session.get('theme', 'dark')
    return render_template('similar_questions.html', user=user, theme=theme)


@app.route('/api/generate-similar-questions', methods=['POST'])
def generate_similar_questions():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    uploaded_pdfs = []
    for key in request.files:
        if key.startswith('pdf'):
            f = request.files[key]
            if f and f.filename and allowed_file(f.filename):
                uploaded_pdfs.append(f)

    if not uploaded_pdfs:
        return jsonify({'success': False, 'error': 'Please upload at least 1 PDF (up to 4).'}), 400
    uploaded_pdfs = uploaded_pdfs[:4]

    try:
        question_count = int(request.form.get('question_count', 20))
        question_count = max(5, min(60, question_count))
    except ValueError:
        question_count = 20

    hardness      = request.form.get('hardness', 'same')
    subject_title = request.form.get('subject_title', '').strip() or 'Generated Exam'
    include_ans   = request.form.get('include_answers', 'true').lower() == 'true'

    all_texts, file_names = [], []
    for f in uploaded_pdfs:
        try:
            text = extract_pdf_text(f)
            if text and len(text.strip()) > 30:
                all_texts.append(text.strip())
                file_names.append(f.filename)
        except Exception as e:
            logger.warning(f"Could not read PDF {f.filename}: {e}")

    if not all_texts:
        return jsonify({'success': False, 'error': 'No readable text found. Use text-based PDFs, not scanned images.'}), 400

    combined_text = '\n\n---NEXT PDF---\n\n'.join(
        f"[PDF {i+1}: {file_names[i]}]\n{t[:4000]}" for i, t in enumerate(all_texts)
    )

    # Step 1: Analyse structure fingerprint
    analysis_prompt = f"""Analyse these exam papers and extract their structural pattern.

PAPERS:
{combined_text[:6000]}

Return ONLY valid JSON (no markdown):
{{
  "detected_format": "objective|theory|mixed",
  "option_style": "A B C D",
  "avg_question_length": "short|medium|long",
  "difficulty_level": "easy|medium|hard",
  "main_topics": ["topic1", "topic2", "topic3"],
  "question_styles": ["definition", "application", "calculation", "comparison", "scenario"],
  "subject_domain": "string"
}}"""

    try:
        ar = model.generate_content(analysis_prompt).text.strip()
        s, e = ar.find('{'), ar.rfind('}') + 1
        fingerprint = json.loads(ar[s:e] if s != -1 and e > s else '{}')
    except Exception:
        fingerprint = {
            'detected_format': 'objective', 'option_style': 'A B C D',
            'avg_question_length': 'medium', 'difficulty_level': 'medium',
            'main_topics': ['General'], 'question_styles': ['definition', 'application'],
            'subject_domain': subject_title
        }

    effective_difficulty = fingerprint.get('difficulty_level', 'medium') if hardness == 'same' else hardness
    detected_format = fingerprint.get('detected_format', 'objective')
    topics_str      = ', '.join(fingerprint.get('main_topics', ['General Content']))
    q_styles_str    = ', '.join(fingerprint.get('question_styles', ['definition']))

    # Step 2: Generate questions
    if detected_format == 'theory':
        gen_prompt = f"""Generate a NEW theory exam that mirrors the style of the analysed papers.

Subject: {fingerprint.get('subject_domain', subject_title)}
Topics: {topics_str}
Styles: {q_styles_str}
Difficulty: {effective_difficulty}
Count: {question_count}

Return ONLY valid JSON:
{{
  "paper_title": "string",
  "subject": "string",
  "difficulty": "{effective_difficulty}",
  "format": "theory",
  "instructions": "string",
  "questions": [
    {{"number": 1, "question": "...", "marks": 5, "type": "short_answer|essay|calculation", "model_answer": "...", "topic": "..."}}
  ]
}}"""
    else:
        gen_prompt = f"""Generate a NEW multiple-choice exam that mirrors the style of the analysed papers.

Subject: {fingerprint.get('subject_domain', subject_title)}
Topics: {topics_str}
Styles: {q_styles_str}
Difficulty: {effective_difficulty}
Count: {question_count}

Return ONLY valid JSON:
{{
  "paper_title": "string",
  "subject": "string",
  "difficulty": "{effective_difficulty}",
  "format": "objective",
  "instructions": "Answer ALL questions. Choose the BEST option.",
  "questions": [
    {{"number": 1, "question": "...", "options": ["A. text", "B. text", "C. text", "D. text"], "answer": "A", "explanation": "...", "topic": "...", "marks": 1}}
  ]
}}"""

    try:
        gr = model.generate_content(gen_prompt).text.strip()
        s2, e2 = gr.find('{'), gr.rfind('}') + 1
        paper_data = json.loads(gr[s2:e2] if s2 != -1 and e2 > s2 else gr)
    except Exception as ex:
        logger.error(f"Question generation failed: {ex}", exc_info=True)
        return jsonify({'success': False, 'error': f'AI generation failed: {str(ex)[:200]}'}), 500

    questions = paper_data.get('questions', [])
    if not questions:
        return jsonify({'success': False, 'error': 'AI returned no questions. Please try again.'}), 500

    return jsonify({
        'success': True, 'paper': paper_data,
        'source_files': file_names, 'fingerprint': fingerprint,
        'question_count': len(questions), 'include_answers': include_ans
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=app.debug)