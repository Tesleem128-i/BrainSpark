from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from models import (db, User, Quiz, QuizResult, Connection, UserTag, Message,
                    ChatGroup, ChatGroupMember, GroupMessage, BrainstormSession,
                    BrainstormNote, GroupJoinRequest, Poll, PollOption, PollVote,
                    GeneratedQuestion, TopicMastery, WrongAnswer, AppNotification,
                    HandRaise, TokenTransaction, TokenUsageLog)
import hashlib
import base64

def save_file_to_db(file_storage, file_type):
    """Save file to database as base64. Returns filename key."""
    try:
        file_bytes = file_storage.read()
        file_storage.seek(0)
        b64_data = base64.b64encode(file_bytes).decode('utf-8')
        ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else 'bin'
        ts = int(datetime.utcnow().timestamp() * 1000)
        filename = f"{file_type}_{ts}.{ext}"
        mime_map = {
            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
            'gif': 'image/gif', 'webp': 'image/webp', 'pdf': 'application/pdf',
            'webm': 'audio/webm', 'ogg': 'audio/ogg', 'mp3': 'audio/mp3',
            'wav': 'audio/wav'
        }
        mime_type = mime_map.get(ext, 'application/octet-stream')
        from models import GroupFile
        gf = GroupFile(filename=filename, file_data=b64_data, mime_type=mime_type, file_type=file_type)
        db.session.add(gf)
        db.session.flush()
        return filename
    except Exception as e:
        logger.error(f"save_file_to_db error: {e}")
        return None
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

from pywebpush import webpush, WebPushException


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
app.config['MAX_CONTENT_LENGTH']    = 20 * 1024 * 1024  # 20 MB

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
vision_model = genai.GenerativeModel('gemini-2.5-flash')

# ── Folders ───────────────────────────────────────────────────────────────────
os.makedirs('uploads/profiles',     exist_ok=True)
os.makedirs('uploads',              exist_ok=True)
os.makedirs('uploads/group_chat',   exist_ok=True)
os.makedirs('uploads/voice_notes',  exist_ok=True)

# ── VAPID keys (generate once; store in env) ──────────────────────────────────
VAPID_PUBLIC_KEY  = os.getenv('VAPID_PUBLIC_KEY',  '')
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
VAPID_CLAIMS      = {'sub': f"mailto:{os.getenv('MAIL_USERNAME', 'admin@brainspark.app')}"}


# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═════════════════════════════════════════════════════════════════════════════

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
    if not filename or filename == '':
        return False
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {
        'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'mp3', 'ogg', 'webm', 'wav'
    }


def extract_pdf_text(file_storage):
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
    pdf_bytes = file_storage.read()
    file_storage.seek(0)
    return pdf_bytes


def extract_pdf_text_simple(filepath):
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


def _create_notification(user_id, notif_type, title, body='', link_type=None, link_id=None):
    """Create an in-app notification record."""
    try:
        n = AppNotification(
            user_id=user_id, notif_type=notif_type,
            title=title, body=body,
            link_type=link_type, link_id=link_id
        )
        db.session.add(n)
        db.session.flush()
        # Try web-push (non-fatal)
        _send_push(user_id, title, body)
    except Exception as e:
        logger.warning(f"Notification creation error: {e}")


def _send_push(user_id, title, body):
    """Send a Web Push notification if subscription exists."""
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        return
    try:
        from pywebpush import webpush, WebPushException
        user = User.query.get(user_id)
        if not user or not user.push_subscription:
            return
        sub = json.loads(user.push_subscription)
        webpush(
            subscription_info=sub,
            data=json.dumps({'title': title, 'body': body}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
    except Exception as e:
        logger.debug(f"Push notification failed (non-fatal): {e}")


def get_time_ago(dt):
    seconds = (datetime.utcnow() - dt).total_seconds()
    if seconds < 60:     return f"{int(seconds)} seconds ago"
    if seconds < 3600:   return f"{int(seconds/60)} minutes ago"
    if seconds < 86400:  return f"{int(seconds/3600)} hours ago"
    if seconds < 604800: return f"{int(seconds/86400)} days ago"
    return dt.strftime('%Y-%m-%d')


# ═════════════════════════════════════════════════════════════════════════════
#  CORE ROUTES
# ═════════════════════════════════════════════════════════════════════════════

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
            return jsonify({'success': False, 'error': 'That username is already taken.'})
        if User.query.filter(db.func.lower(User.email) == email).first():
            return jsonify({'success': False, 'error': 'An account with this email already exists.'})
        if len(password) < 8:
            return jsonify({'success': False, 'error': 'Password must be at least 8 characters.'})
        if not re.search(r'[A-Z]', password):
            return jsonify({'success': False, 'error': 'Password must contain at least one uppercase letter.'})
        if not re.search(r'\d', password):
            return jsonify({'success': False, 'error': 'Password must contain at least one number.'})
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            return jsonify({'success': False, 'error': 'Password must contain at least one special character.'})

        user = User(name=name, username=username, email=email,
                    school=school, profession=profession,
                    study_level=study_level, country=country)
        user.set_password(password)
        code = ''.join(random.choices('0123456789', k=6))
        user.verification_code = code

        body = (
            f"Hi {name},\n\nYour Brainspark verification code is:\n\n    {code}\n\n"
            f"Enter this code on the signup page to activate your account.\n"
            f"The code expires in 15 minutes.\n\n— The Brainspark Team"
        )
        try:
            send_email_brevo(email, name, 'Brainspark — Verify Your Email', body)
        except Exception as mail_err:
            logger.error(f"Email send failed for {email}: {mail_err}", exc_info=True)
            return jsonify({'success': False, 'error': f'Could not send verification email: {str(mail_err)[:200]}.'})

        db.session.add(user)
        db.session.flush()

        profile_file = request.files.get('profile_pic')
        if profile_file and profile_file.filename and allowed_file(profile_file.filename):
            try:
                ext      = profile_file.filename.rsplit('.', 1)[1].lower()
                save_dir = app.config['PROFILE_UPLOAD_FOLDER']
                os.makedirs(save_dir, exist_ok=True)
                final_name = f"{user.id}.{ext}"
                final_path = os.path.join(save_dir, final_name)
                temp_path  = os.path.join(save_dir, f"tmp_{user.id}.{ext}")
                profile_file.save(temp_path)
                # Remove any old pic for this user with a different extension
                for existing_file in os.listdir(save_dir):
                    if existing_file.startswith(f"{user.id}.") and existing_file != final_name:
                        try:
                            os.remove(os.path.join(save_dir, existing_file))
                        except Exception:
                            pass
                os.replace(temp_path, final_path)
                user.profile_pic = final_name
            except Exception as pic_err:
                logger.warning(f"Profile pic save failed (non-fatal): {pic_err}")
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass

        db.session.commit()
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
            return jsonify({'success': False, 'error': 'Incorrect code. Please check your email and try again.'})
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
        body = (
            f"Hi {user.name},\n\nYour new Brainspark verification code is:\n\n    {code}\n\n"
            f"This code expires in 15 minutes.\n\n— The Brainspark Team"
        )
        send_email_brevo(email, user.name, 'Brainspark — New Verification Code', body)
        return jsonify({'success': True, 'message': 'A new code has been sent to your email.'})
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
        data = request.json or {}
        name    = data.get('name', '').strip()
        email   = data.get('email', '').strip()
        message = data.get('message', '').strip()

        if not name or not email or not message:
            return jsonify({'message': 'Please fill in all fields.'}), 400

        is_support = message.startswith('[Token Support]')
        subject = f"{'🔴 Token Support Request' if is_support else 'Brainspark Contact'}: {name}"

        body = (
            f"Name:    {name}\n"
            f"Email:   {email}\n"
            f"{'─'*40}\n"
            f"{message}\n\n"
            f"{'─'*40}\n"
            f"Sent from Brainspark {'token support form' if is_support else 'contact form'}\n"
            f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )

        # Send to admin
        send_email_brevo(
            os.getenv('MAIL_USERNAME'), 'Brainspark Admin',
            subject, body
        )

        # Send confirmation to user
        confirmation_body = (
            f"Hi {name},\n\n"
            f"We received your message and will get back to you within 24 hours.\n\n"
            f"Your message:\n{message}\n\n"
            f"— The Brainspark Team"
        )
        try:
            send_email_brevo(
                email, name,
                'Brainspark — We received your message',
                confirmation_body
            )
        except Exception:
            pass  # confirmation to user is non-fatal

        return jsonify({'message': 'Message sent successfully!'})
    except Exception as e:
        logger.error(f"send_email error: {e}", exc_info=True)
        return jsonify({'message': f'Error: {str(e)[:200]}'}), 500


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
    if not file.filename or not allowed_file(file.filename):
        return jsonify({'success': False, 'error': 'Only PDF files are allowed'}), 400

    filepath = None
    try:
        filename  = secure_filename(file.filename)
        filepath  = os.path.join(app.config['UPLOAD_FOLDER'], f"temp_{int(datetime.now().timestamp())}_{filename}")
        try:
            file.save(filepath)
        except Exception as save_err:
            logger.error(f"File save error: {save_err}")
            return jsonify({'success': False, 'error': f'Could not save file: {str(save_err)[:100]}'}), 500

        text = extract_pdf_text_simple(filepath)

        if not text or len(text.strip()) < 50:
            return jsonify({'success': False, 'error': 'PDF has no readable text. Make sure it is not a scanned image PDF.'}), 400

        # Limit text to avoid AI timeout on Render free tier
        text = text[:6000]

        question_type  = request.form.get('type', 'objective')
        hardness       = request.form.get('hardness', 'medium')
        question_count = int(request.form.get('question_count', 10))
        question_count = max(5, min(100, question_count))

        # Skip AI topic detection — go straight to session to avoid timeout
        # Topics will be detected during question generation instead
        topics = ["General Content"]
        try:
            topics_prompt = (
                f"List 3-7 main topics from this text as JSON only.\n"
                f"Text: {text[:1500]}\n"
                f'Return ONLY: {{"topics": ["Topic 1", "Topic 2"]}}'
            )
            response    = model.generate_content(topics_prompt)
            topics_text = response.text.strip()
            start = topics_text.find('{')
            end   = topics_text.rfind('}') + 1
            if start != -1 and end > start:
                topics_data = json.loads(topics_text[start:end])
                topics = topics_data.get('topics', ["General Content"]) or ["General Content"]
        except Exception as topic_err:
            logger.warning(f"Topic detection failed (non-fatal): {topic_err}")
            topics = ["General Content"]

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
    finally:
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass


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
    user  = User.query.get(session['user_id'])
    theme = session.get('theme', 'light')
    return render_template('study-buddies.html', user=user, theme=theme)


@app.route('/api/get-quiz-questions')
def get_quiz_questions():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    quiz_questions_raw = session.get('quiz_questions')
    if not quiz_questions_raw:
        return jsonify({'success': False, 'error': 'No quiz data found.'}), 400
    try:
        if isinstance(quiz_questions_raw, str):
            quiz_questions = json.loads(quiz_questions_raw)
        else:
            quiz_questions = quiz_questions_raw
        questions = quiz_questions.get('questions', []) if isinstance(quiz_questions, dict) else quiz_questions
        return jsonify({'success': True, 'questions': questions})
    except Exception as e:
        return jsonify({'success': False, 'error': 'Invalid quiz data in session.'}), 500


@app.route('/api/get-quiz-topics')
def get_quiz_topics():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    topics_json = session.get('pdf_topics')
    questions   = session.get('quiz_questions')
    if questions:
        return jsonify({'success': True, 'topics': [], 'already_generated': True})
    if not topics_json:
        return jsonify({'success': False, 'error': 'No topics found.'}), 400
    try:
        topics = json.loads(topics_json)
        return jsonify({'success': True, 'topics': topics, 'already_generated': False})
    except json.JSONDecodeError:
        return jsonify({'success': False, 'error': 'Invalid topics data'}), 500


@app.route('/api/generate-questions', methods=['POST'])
def generate_questions():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    token_check = require_tokens('quiz_generation', 1)
    if token_check:
        return jsonify({'error': token_check['error']}), token_check['code']

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
        return jsonify({'error': 'No PDF text found.'}), 400

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
            prompt = f"""Generate UNIQUE questions for this text:
{pdf_text[:3000]}
**OUTPUT ONLY VALID JSON**:
{{"questions": [{{"question": "...", "options": ["A. option1", "B. option2", "C. option3", "D. option4"], "answer": "A", "explanation": "..."}}]}}
Rules: Generate EXACTLY {question_count} questions. Exactly 4 options labelled A, B, C, D. "answer" must be exactly one letter: A, B, C, or D. Difficulty: {hardness}. All unique."""
        else:
            topics_str = ', '.join(selected_topics) if isinstance(selected_topics, list) else str(selected_topics)
            prompt = f"""Generate UNIQUE questions from this text, focusing ONLY on: {topics_str}
Text: {pdf_text[:3000]}
**OUTPUT ONLY VALID JSON**:
{{"questions": [{{"question": "...", "options": ["A. option1", "B. option2", "C. option3", "D. option4"], "answer": "A", "explanation": "..."}}]}}
Rules: Generate EXACTLY {question_count} questions. Focus ONLY on: {topics_str}. Difficulty: {hardness}. All unique."""

        response       = model.generate_content(prompt)
        questions_text = response.text.strip()

        try:
            start          = questions_text.find('{')
            end            = questions_text.rfind('}') + 1
            json_str       = questions_text[start:end] if start != -1 and end > start else '{}'
            questions_data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return jsonify({'success': False, 'error': 'Failed to parse AI response. Try again.'}), 500

        questions = questions_data.get('questions', [])
        if not questions:
            return jsonify({'success': False, 'error': 'No questions generated.'}), 400

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

        unique_new = unique_new[:question_count]

        if len(unique_new) < 5:
            return jsonify({'success': False, 'error': f'Could only generate {len(unique_new)} unique questions.'}), 400

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
        connection_count = Connection.query.filter(
            (Connection.user_id == user_id) | (Connection.connected_user_id == user_id)
        ).filter(Connection.user_id == user_id).count()

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
            'user_id': user_id,
            'stats': {'total_quizzes': total_quizzes, 'average_score': average_score, 'connection_count': connection_count},
            'recent_activity':  recent_activity,
            'performance_data': daily_scores
        })
    except Exception as e:
        logger.error(f'Error fetching dashboard stats: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully', 'redirect': '/'})


# ═════════════════════════════════════════════════════════════════════════════
#  STUDY BUDDIES
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/find-study-buddies')
def find_study_buddies():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        user_id = session['user_id']
        user    = User.query.get(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        search_query = request.args.get('search', '').lower().strip()
        filter_type  = request.args.get('filter', '').strip()

        def _shared_count(buddy):
            count = 0
            try:
                if user.profession and buddy.profession and user.profession.strip().lower() == buddy.profession.strip().lower():
                    count += 1
                if user.study_level and buddy.study_level and user.study_level == buddy.study_level:
                    count += 1
                if user.school and buddy.school and user.school.strip().lower() == buddy.school.strip().lower():
                    count += 1
            except Exception:
                pass
            return count

        query = User.query.filter(User.id != user_id)

        if filter_type == 'country' and user.country:
            query = query.filter(User.country == user.country)
        elif filter_type == 'school' and user.school:
            query = query.filter(User.school.ilike(f'%{user.school}%'))
        elif filter_type == 'profession' and user.profession:
            query = query.filter(User.profession.ilike(f'%{user.profession}%'))
        elif filter_type == 'level' and user.study_level:
            query = query.filter(User.study_level == user.study_level)

        if search_query:
            query = query.filter(
                (User.name.ilike(f'%{search_query}%')) |
                (User.username.ilike(f'%{search_query}%'))
            )

        buddies = query.limit(200).all()

        # Bulk fetch connections
        try:
            connections = Connection.query.filter(
                (Connection.user_id == user_id) | (Connection.connected_user_id == user_id)
            ).all()
            connected_ids = set()
            for c in connections:
                other = c.connected_user_id if c.user_id == user_id else c.user_id
                connected_ids.add(other)
        except Exception:
            connected_ids = set()

        # Bulk fetch pending requests (sent by me)
        try:
            sent_notifs = AppNotification.query.filter_by(
                notif_type='connection_request',
                link_id=user_id,
                is_read=False
            ).all()
            pending_ids = set(n.user_id for n in sent_notifs)
        except Exception:
            pending_ids = set()

        

        buddies_data = []
        for buddy in buddies:
            try:
                shared   = _shared_count(buddy)
                priority = shared * 50
                if user.country and buddy.country and buddy.country == user.country:
                    priority += 10

                try:
                    total_quizzes = buddy.get_total_quizzes()
                except Exception:
                    total_quizzes = 0

                try:
                    average_score = buddy.get_average_score()
                except Exception:
                    average_score = 0

                try:
                    profile_pic = buddy.get_profile_pic_url()
                except Exception:
                    profile_pic = '/static/image/KNOWITNOW.png'

                try:
                    tags = [t.tag for t in buddy.tags]
                except Exception:
                    tags = []

                buddies_data.append({
                    'id':           buddy.id,
                    'name':         buddy.name or '',
                    'username':     buddy.username or '',
                    'profile_pic':  profile_pic,
                    'school':       buddy.school or '',
                    'study_level':  buddy.study_level or '',
                    'country':      buddy.country or '',
                    'profession':   buddy.profession or '',
                    'bio':          getattr(buddy, 'bio', '') or '',
                    'tags':         tags,
                    'total_quizzes':        total_quizzes,
                    'average_score':        average_score,
                    'is_connected':         buddy.id in connected_ids,
                    'has_pending_request':  buddy.id in pending_ids,
                    'shared_count':         shared,
                    'priority':             priority,
                    'match_country':    bool(user.country    and buddy.country    and user.country    == buddy.country),
                    'match_school':     bool(user.school     and buddy.school     and user.school.strip().lower()     == buddy.school.strip().lower()),
                    'match_profession': bool(user.profession and buddy.profession and user.profession.strip().lower() == buddy.profession.strip().lower()),
                    'match_level':      bool(user.study_level and buddy.study_level and user.study_level == buddy.study_level),
                })
            except Exception as buddy_err:
                logger.warning(f"Skipping buddy {getattr(buddy, 'id', '?')}: {buddy_err}")
                continue

        buddies_data.sort(key=lambda x: (-x['priority'], x['name']))
        for b in buddies_data:
            del b['priority']

        return jsonify({'success': True, 'buddies': buddies_data})

    except Exception as e:
        logger.error(f'find_study_buddies error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

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

@app.route('/api/accept-connection-request', methods=['POST'])
def accept_connection_request():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    requester_id = data.get('requester_id')
    action = data.get('action', 'accept')  # 'accept' or 'reject'
    if not requester_id:
        return jsonify({'error': 'requester_id required'}), 400
    user_id = session['user_id']
    # Mark the notification as read
    AppNotification.query.filter_by(
        user_id=user_id, notif_type='connection_request',
        link_id=requester_id
    ).update({'is_read': True})
    if action == 'reject':
        db.session.commit()
        return jsonify({'success': True, 'message': 'Request declined.'})
    # Check not already connected
    existing = Connection.query.filter(
        ((Connection.user_id == user_id) & (Connection.connected_user_id == requester_id)) |
        ((Connection.user_id == requester_id) & (Connection.connected_user_id == user_id))
    ).first()
    if existing:
        db.session.commit()
        return jsonify({'success': True, 'message': 'Already connected.'})
    try:
        db.session.add(Connection(user_id=user_id, connected_user_id=requester_id))
        db.session.add(Connection(user_id=requester_id, connected_user_id=user_id))
        db.session.flush()
        me = User.query.get(user_id)
        _create_notification(requester_id, 'connection_accepted',
                             f'{me.name} accepted your request!',
                             f'You are now connected with {me.name}.',
                             'dm', user_id)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Connected!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
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
    # Check if request already sent
    pending = AppNotification.query.filter_by(
        notif_type='connection_request', link_id=user_id, user_id=connected_user_id
    ).first()
    if pending:
        return jsonify({'success': True, 'pending': True, 'message': 'Request already sent!'})
    try:
        me = User.query.get(user_id)
        _create_notification(connected_user_id, 'connection_request',
                             f'{me.name} wants to connect!',
                             f'Tap to accept or decline.',
                             'dm', user_id)
        db.session.commit()
        return jsonify({'success': True, 'pending': True, 'message': 'Connection request sent!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/send-message', methods=['POST'])
def send_message_api():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    is_mp = request.content_type and 'multipart/form-data' in request.content_type
    if is_mp:
        data = request.form
        receiver_id = data.get('receiver_id')
        content     = data.get('content', '').strip()
    else:
        data        = request.json or {}
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
        image_path = None
        if 'image' in request.files:
            f = request.files['image']
            if f and f.filename and allowed_file(f.filename):
                image_path = save_file_to_db(f, 'image')

        message = Message(sender_id=sender_id, receiver_id=receiver_id, content=content, image_path=image_path)
        db.session.add(message)
        db.session.flush()
        # Notify receiver
        sender = User.query.get(sender_id)
        _create_notification(receiver_id, 'message', f'Message from {sender.name}',
                             content[:80], 'dm', sender_id)
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
    return jsonify({'success': True, 'current_user_id': user_id, 'messages': [{
        'id': m.id, 'sender_id': m.sender_id, 'sender_name': m.sender.name,
        'receiver_id': m.receiver_id, 'content': m.content,
        'image_url': f'/api/file/{m.image_path}' if getattr(m, 'image_path', None) else None,
        'is_read': m.is_read, 'created_at': m.created_at.isoformat()
    } for m in messages]})


@app.route('/api/get-connections')
def get_connections():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        user_id   = session['user_id']
        # Get all user IDs that are connected to me (either direction)
        initiated_ids = [c.connected_user_id for c in Connection.query.filter_by(user_id=user_id).all()]
        received_ids  = [c.user_id for c in Connection.query.filter_by(connected_user_id=user_id).all()]
        all_buddy_ids = list(set(initiated_ids + received_ids))
        buddies       = User.query.filter(User.id.in_(all_buddy_ids)).all()
        connections_data, seen_ids = [], set()

        def _append(buddy):
            try:
                unread = Message.query.filter(
                    (Message.sender_id == buddy.id) &
                    (Message.receiver_id == user_id) &
                    (Message.is_read == False)
                ).count()
            except Exception:
                unread = 0

            try:
                avg = buddy.get_average_score()
            except Exception:
                avg = 0

            try:
                pic = buddy.get_profile_pic_url()
            except Exception:
                pic = '/static/image/KNOWITNOW.png'

            try:
                tags = [t.tag for t in buddy.tags]
            except Exception:
                tags = []

            connections_data.append({
                'id':            buddy.id,
                'name':          buddy.name or '',
                'username':      buddy.username or '',
                'profile_pic':   pic,
                'study_level':   buddy.study_level or '',
                'average_score': avg,
                'tags':          tags,
                'bio':           getattr(buddy, 'bio', '') or '',
                'unread_count':  unread,
            })

        for buddy in buddies:
            try:
                if buddy.id not in seen_ids:
                    seen_ids.add(buddy.id)
                    _append(buddy)
            except Exception as e:
                logger.warning(f"Connection error: {e}")
                continue
            except Exception as e:
                logger.warning(f"Connection error: {e}")
                continue

        return jsonify({'success': True, 'connections': connections_data})

    except Exception as e:
        logger.error(f'get_connections error: {e}', exc_info=True)
        return jsonify({'success': False, 'error': str(e), 'connections': []}), 500


# ═════════════════════════════════════════════════════════════════════════════
#  AI CHAT
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/ask-ai', methods=['POST'])
def ask_ai():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Token check
    token_check = require_tokens('ai_chat', 1)
    if token_check:
        return jsonify({'error': token_check['error']}), token_check['code']

    is_multipart = request.content_type and 'multipart/form-data' in request.content_type

    if is_multipart:
        question      = request.form.get('question', '').strip()
        reset_conv    = request.form.get('reset', '').lower() in ('true', '1')
        response_mode = request.form.get('response_mode', 'text')
    else:
        data          = request.get_json(silent=True) or {}
        question      = data.get('question', '').strip()
        reset_conv    = data.get('reset', False)
        response_mode = data.get('response_mode', 'text')

    if not question and not is_multipart:
        return jsonify({'error': 'Please provide a question'}), 400

    try:
        if reset_conv or 'ai_conversation' not in session:
            conversation_history       = []
            session['ai_conversation'] = []
        else:
            conversation_history = session.get('ai_conversation', [])

        context = ""
        if conversation_history:
            context = "Previous conversation:\n"
            for i, ex in enumerate(conversation_history[-6:], 1):
                context += f"\nQ{i}: {ex['question']}\nA{i}: {ex['answer']}\n"
            context += "\n---\n\n"

        system_prompt = (
            "You are Brainspark AI, an expert study tutor. "
            "Explain concepts clearly with examples. Use **bold** for key terms. "
            "If the user says they don't understand, rephrase with a simpler analogy. "
            "Be warm, encouraging, and concise."
        )

        content_parts = []
        saved_image_url = None
        full_text_prompt = f"{system_prompt}\n\n{context}"
        if question:
            full_text_prompt += f"Student's question: {question}"
        else:
            full_text_prompt += "Please analyze the attached file(s) and explain the key concepts."
        content_parts.append(full_text_prompt)

        has_pdf = has_image = False

        if is_multipart and 'pdf' in request.files:
            pdf_file = request.files['pdf']
            if pdf_file and pdf_file.filename:
                pdf_text = extract_pdf_text(pdf_file)
                if pdf_text:
                    has_pdf = True
                    content_parts.append(f"\n\n[PDF CONTENT]\n{pdf_text[:5000]}")

        saved_image_url = None
        if is_multipart and 'image' in request.files:
            img_file = request.files['image']
            if img_file and img_file.filename:
                try:
                    img_bytes = img_file.read()
                    ext       = img_file.filename.rsplit('.', 1)[-1].lower()
                    mime_map  = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                                 'gif': 'image/gif', 'webp': 'image/webp'}
                    mime_type = mime_map.get(ext, 'image/jpeg')
                    content_parts.append({'mime_type': mime_type, 'data': img_bytes})
                    has_image = True
                    try:
                        ai_img_dir = os.path.join('uploads', 'ai_chat')
                        os.makedirs(ai_img_dir, exist_ok=True)
                        ts = int(datetime.utcnow().timestamp() * 1000)
                        img_filename = f"ai_{session['user_id']}_{ts}.{ext}"
                        with open(os.path.join(ai_img_dir, img_filename), 'wb') as f_out:
                            f_out.write(img_bytes)
                        saved_image_url = f'/uploads/ai_chat/{img_filename}'
                    except Exception as save_err:
                        logger.warning(f"Image save failed (non-fatal): {save_err}")
                except Exception as img_err:
                    logger.warning(f"Image processing error: {img_err}")
                    saved_image_url = None

        if has_image:
            response = vision_model.generate_content(content_parts)
        else:
            joined_prompt = "\n".join(str(p) for p in content_parts if isinstance(p, str))
            response      = model.generate_content(joined_prompt)

        explanation = response.text.strip()

        youtube_query = None
        if response_mode == 'youtube':
            q = question.strip() if question.strip() else explanation[:60]
            youtube_query = q[:80]

        conversation_history.append({
            'question': question or '[file attachment]',
            'answer': explanation[:500],
            'image_url': saved_image_url,
            'has_pdf': has_pdf,
            'timestamp': datetime.utcnow().isoformat()
        })
        session['ai_conversation'] = conversation_history[-20:]
        session.modified = True

        deduct_token(session['user_id'], 'ai_chat', 1)
        return jsonify({
            'success':            True,
            'explanation':        explanation,
            'youtube_query':      youtube_query,
            'has_pdf':            has_pdf,
            'has_image':          has_image,
            'saved_image_url':    saved_image_url,
            'conversation_count': len(conversation_history)
        })

    except Exception as e:
        logger.error(f'Error in ask-ai: {str(e)}', exc_info=True)
        return jsonify({'error': f'AI error: {str(e)[:200]}'}), 500


# ═════════════════════════════════════════════════════════════════════════════
#  GROUPS
# ═════════════════════════════════════════════════════════════════════════════

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
            'message_count': GroupMessage.query.filter_by(group_id=g.id, is_deleted=False).count(),
            'your_role': m.role, 'is_muted': m.is_muted,
            'created_at': g.created_at.isoformat()
        })
    return jsonify({'success': True, 'groups': groups_data})


@app.route('/api/discover-groups')
def discover_groups():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id        = session['user_id']
    user           = User.query.get(user_id)
    user_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(user_id=user_id).all()]

    # Helper: count shared profile fields between current user and a target user
    def _shared_with_user(other):
        if not other:
            return 0
        count = 0
        if user.profession and other.profession and user.profession.strip().lower() == other.profession.strip().lower():
            count += 1
        if user.study_level and other.study_level and user.study_level == other.study_level:
            count += 1
        if user.school and other.school and user.school.strip().lower() == other.school.strip().lower():
            count += 1
        return count

    groups_data = []
    for g in ChatGroup.query.all():
        if g.id in user_group_ids:
            continue

        # Check shared profile fields with the group admin/creator
        creator = User.query.get(g.created_by)
        shared  = _shared_with_user(creator)

        

        pending = GroupJoinRequest.query.filter_by(group_id=g.id, user_id=user_id, status='pending').first()
        groups_data.append({
            'id': g.id, 'name': g.name, 'description': g.description,
            'is_private': g.is_private, 'created_by': g.created_by,
            'creator_name':  g.creator.name,
            'member_count':  ChatGroupMember.query.filter_by(group_id=g.id).count(),
            'message_count': GroupMessage.query.filter_by(group_id=g.id, is_deleted=False).count(),
            'has_pending_request': pending is not None,
            'shared_with_admin': shared,
            'created_at': g.created_at.isoformat()
        })

    # Sort: most shared fields first
    groups_data.sort(key=lambda x: -x['shared_with_admin'])
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
            'message_count': GroupMessage.query.filter_by(group_id=g.id, is_deleted=False).count(),
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
            db.session.flush()
            # Notify admins
            admins = ChatGroupMember.query.filter_by(group_id=group_id, role='admin').all()
            requester = User.query.get(user_id)
            for adm in admins:
                _create_notification(adm.user_id, 'join_request',
                                     f'Join Request for {group.name}',
                                     f'{requester.name} wants to join your group.',
                                     'group', group_id)
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
        db.session.flush()
        _create_notification(target_user_id, 'join_approved',
                             f'Added to {group.name}',
                             f'You were added to the group "{group.name}".',
                             'group', group_id)
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
        'bio': getattr(m.user, 'bio', '') or '',
        'joined_at': m.joined_at.isoformat()
    } for m in members]})


@app.route('/api/mute-group', methods=['POST'])
def mute_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json
    m = ChatGroupMember.query.filter_by(group_id=data.get('group_id'), user_id=session['user_id']).first()
    if not m:
        return jsonify({'error': 'Not a member'}), 403
    m.is_muted = not m.is_muted
    db.session.commit()
    return jsonify({'success': True, 'is_muted': m.is_muted})


# ═════════════════════════════════════════════════════════════════════════════
#  GROUP MESSAGES  (enhanced)
# ═════════════════════════════════════════════════════════════════════════════

def _serialize_group_message(msg, current_user_id):
    """Convert a GroupMessage to a dict with full enrichment."""
    reactions = {}
    if msg.reactions:
        try:
            reactions = json.loads(msg.reactions)
        except Exception:
            pass

    mentions = []
    if msg.mentions:
        try:
            mentions = json.loads(msg.mentions)
        except Exception:
            pass

    reply_data = None
    if msg.reply_to_id and msg.reply_to and not msg.reply_to.is_deleted:
        reply_data = {
            'id':           msg.reply_to.id,
            'sender_name':  msg.reply_to.sender.name,
            'content':      msg.reply_to.content[:120],
            'message_type': msg.reply_to.message_type
        }

    poll_data = None
    if msg.message_type == 'poll' and msg.poll_id:
        poll = Poll.query.get(msg.poll_id)
        if poll:
            opts = []
            for opt in poll.options:
                opts.append({
                    'id': opt.id, 'text': opt.option_text, 'votes': len(opt.votes),
                    'has_voted': PollVote.query.filter_by(option_id=opt.id, user_id=current_user_id).first() is not None
                })
            poll_data = {
                'id': poll.id, 'question': poll.question, 'is_active': poll.is_active,
                'options': opts, 'total_votes': sum(o['votes'] for o in opts)
            }

    # Parse brainstorm session card data and refresh status from DB
    session_data = None
    if msg.message_type == 'brainstorm_session' and msg.content:
        try:
            session_data = json.loads(msg.content)
            # Always refresh the live status from the database so the card stays current
            if session_data.get('id'):
                live_session = BrainstormSession.query.get(session_data['id'])
                if live_session:
                    session_data['status'] = live_session.status
                    session_data['teacher_id'] = live_session.teacher_id
        except Exception:
            session_data = None

    return {
        'id':             msg.id,
        'sender_id':      msg.sender_id,
        'sender_name':    msg.sender.name,
        'sender_username': msg.sender.username,
        'sender_pic':     msg.sender.get_profile_pic_url(),
        'content':        '' if msg.is_deleted else msg.content,
        'message_type':   msg.message_type,
        'is_deleted':     msg.is_deleted,
        'is_edited':      msg.is_edited,
        'is_sent':        msg.sender_id == current_user_id,
        'image_url':      f'/api/file/{msg.image_path}' if msg.image_path else None,
        'pdf_url':        f'/api/file/{msg.pdf_path}'   if msg.pdf_path   else None,
        'voice_url':      f'/api/file/{msg.voice_path}' if msg.voice_path else None,
        'reply_to':       reply_data,
        'mentions':       mentions,
        'reactions':      reactions,
        'poll':           poll_data,
        'session_data':   session_data,
        'created_at':     msg.created_at.isoformat(),
        'edited_at':      msg.edited_at.isoformat() if msg.edited_at else None,
    }


@app.route('/api/send-group-message', methods=['POST'])
def send_group_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']

    if request.content_type and 'multipart/form-data' in request.content_type:
        group_id     = request.form.get('group_id')
        content      = request.form.get('content', '').strip()
        message_type = request.form.get('message_type', 'text')
        reply_to_id  = request.form.get('reply_to_id')
        mentions_raw = request.form.get('mentions', '[]')
    else:
        data         = request.json or {}
        group_id     = data.get('group_id')
        content      = data.get('content', '').strip()
        message_type = data.get('message_type', 'text')
        reply_to_id  = data.get('reply_to_id')
        mentions_raw = json.dumps(data.get('mentions', []))

    if not group_id:
        return jsonify({'error': 'Group ID required'}), 400
    if not content and message_type == 'text':
        return jsonify({'error': 'Content required for text messages'}), 400
    if len(content) > 5000:
        return jsonify({'error': 'Message too long'}), 400

    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403

    # Parse mentions
    try:
        mentions_list = json.loads(mentions_raw) if mentions_raw else []
    except Exception:
        mentions_list = []

    # Check if @BrainAI is mentioned → auto-invoke AI
    brain_ai_mentioned = 'brainai' in [str(m).lower() for m in mentions_list]

    try:
        image_path = voice_path = pdf_path = None

        if message_type == 'image' and 'image' in request.files:
            f = request.files['image']
            if f and f.filename and allowed_file(f.filename):
                ts  = int(datetime.utcnow().timestamp() * 1000)
                ext = f.filename.rsplit('.', 1)[1].lower()
                image_path = save_file_to_db(f, 'image')

        if message_type == 'pdf' and 'pdf' in request.files:
            f = request.files['pdf']
            if f and f.filename and allowed_file(f.filename):
                ts  = int(datetime.utcnow().timestamp() * 1000)
                ext = f.filename.rsplit('.', 1)[1].lower()
                pdf_path = save_file_to_db(f, 'pdf')

        if message_type == 'voice' and 'voice' in request.files:
            f = request.files['voice']
            if f and f.filename:
                ts  = int(datetime.utcnow().timestamp() * 1000)
                ext = f.filename.rsplit('.', 1)[-1].lower() or 'webm'
                voice_path = save_file_to_db(f, 'voice')
                message_type = 'voice'

        msg = GroupMessage(
            group_id=group_id, sender_id=user_id, content=content or '',
            message_type=message_type, image_path=image_path,
            pdf_path=pdf_path, voice_path=voice_path,
            reply_to_id=int(reply_to_id) if reply_to_id else None,
            mentions=json.dumps(mentions_list) if mentions_list else None
        )
        db.session.add(msg)
        db.session.flush()

        sender = User.query.get(user_id)
        group  = ChatGroup.query.get(group_id)

        # ── Notify group members ────────────────────────────────────────────
        members = ChatGroupMember.query.filter_by(group_id=group_id).all()
        for m in members:
            if m.user_id == user_id:
                continue
            if m.is_muted:
                continue
            notif_type = 'group_message'
            title      = f'{sender.name} in {group.name}'
            body       = content[:80] if content else f'Shared a {message_type}'
            # check if this member is mentioned
            if str(m.user_id) in [str(x) for x in mentions_list]:
                notif_type = 'mention'
                title      = f'{sender.name} mentioned you in {group.name}'
            _create_notification(m.user_id, notif_type, title, body, 'group', group_id)

        db.session.commit()

        msg_data = _serialize_group_message(msg, user_id)

        # ── BrainAI auto-reply ──────────────────────────────────────────────
        if brain_ai_mentioned and content:
            try:
                recent_msgs = GroupMessage.query.filter_by(group_id=group_id, is_deleted=False)\
                    .order_by(GroupMessage.created_at.desc()).limit(10).all()
                context_lines = []
                for rm in reversed(recent_msgs[1:]):  # exclude the just-sent msg
                    context_lines.append(f"{rm.sender.name}: {rm.content[:150]}")
                context_str = "\n".join(context_lines)

                ai_prompt = (
                    f"You are BrainAI, an expert AI study assistant embedded in the Brainspark group chat. "
                    f"Be helpful, concise, encouraging, and educational. "
                    f"Group context:\n{context_str}\n\n"
                    f"Someone asked you (tagged @BrainAI): {content}\n\n"
                    f"Respond directly and helpfully."
                )
                ai_content_parts = [ai_prompt]
                if image_path:
                    try:
                        img_full_path = os.path.join('uploads/group_chat', image_path)
                        with open(img_full_path, 'rb') as img_f:
                            img_bytes = img_f.read()
                        ext = image_path.rsplit('.', 1)[-1].lower()
                        mime_map = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif'}
                        ai_content_parts.append({'mime_type': mime_map.get(ext,'image/jpeg'), 'data': img_bytes})
                        ai_response = vision_model.generate_content(ai_content_parts)
                    except Exception:
                        ai_response = model.generate_content(ai_prompt)
                elif pdf_path:
                    try:
                        pdf_full_path = os.path.join('uploads/group_chat', pdf_path)
                        pdf_text_for_ai = extract_pdf_text_simple(pdf_full_path)
                        if pdf_text_for_ai:
                            ai_content_parts[0] += f"\n\n[Attached PDF Content]\n{pdf_text_for_ai[:4000]}"
                        ai_response = model.generate_content(ai_content_parts[0])
                    except Exception:
                        ai_response = model.generate_content(ai_prompt)
                else:
                    ai_response = model.generate_content(ai_prompt)
                ai_text     = ai_response.text.strip()

                # Find a bot user or create a system message attributed to BrainAI
                # We use the group creator as sender but mark message_type='ai'
                ai_msg = GroupMessage(
                    group_id=group_id, sender_id=group.created_by,
                    content=f"🤖 **BrainAI:** {ai_text}",
                    message_type='ai'
                )
                db.session.add(ai_msg)
                # Notify members about BrainAI reply
                for m in members:
                    if m.is_muted:
                        continue
                    _create_notification(m.user_id, 'brainai_mention',
                                         f'🤖 BrainAI replied in {group.name}',
                                         ai_text[:80], 'group', group_id)
                db.session.commit()

                return jsonify({'success': True, 'message': msg_data,
                                'brain_ai_reply': _serialize_group_message(ai_msg, user_id)})
            except Exception as ai_err:
                logger.warning(f"BrainAI auto-reply failed: {ai_err}")

        return jsonify({'success': True, 'message': msg_data})

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

    since = request.args.get('since')
    query = GroupMessage.query.filter_by(group_id=group_id)
    if since:
        try:
            since_clean = since[:19]
            since_dt    = datetime.fromisoformat(since_clean)
            query       = query.filter(GroupMessage.created_at > since_dt)
        except Exception:
            pass

    messages = query.order_by(GroupMessage.created_at.asc()).all()
    return jsonify({
        'success': True,
        'messages': [_serialize_group_message(m, user_id) for m in messages],
        'current_user_id': user_id
    })


@app.route('/api/edit-group-message', methods=['POST'])
def edit_group_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data    = request.json or {}
    msg_id  = data.get('message_id')
    content = data.get('content', '').strip()
    if not msg_id or not content:
        return jsonify({'error': 'message_id and content required'}), 400
    msg = GroupMessage.query.get(msg_id)
    if not msg or msg.sender_id != session['user_id']:
        return jsonify({'error': 'Cannot edit this message'}), 403
    msg.content   = content
    msg.is_edited = True
    msg.edited_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True, 'message': _serialize_group_message(msg, session['user_id'])})


@app.route('/api/delete-group-message', methods=['POST'])
def delete_group_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data   = request.json or {}
    msg_id = data.get('message_id')
    msg    = GroupMessage.query.get(msg_id)
    if not msg:
        return jsonify({'error': 'Message not found'}), 404
    user_id  = session['user_id']
    member   = ChatGroupMember.query.filter_by(group_id=msg.group_id, user_id=user_id).first()
    is_admin = member and member.role == 'admin'
    if msg.sender_id != user_id and not is_admin:
        return jsonify({'error': 'Cannot delete this message'}), 403
    msg.is_deleted = True
    msg.content    = 'This message was deleted'
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/react-group-message', methods=['POST'])
def react_group_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data    = request.json or {}
    msg_id  = data.get('message_id')
    emoji   = data.get('emoji', '')
    user_id = session['user_id']
    msg     = GroupMessage.query.get(msg_id)
    if not msg:
        return jsonify({'error': 'Message not found'}), 404
    member = ChatGroupMember.query.filter_by(group_id=msg.group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'Not a member'}), 403
    try:
        reactions = json.loads(msg.reactions) if msg.reactions else {}
    except Exception:
        reactions = {}
    if emoji not in reactions:
        reactions[emoji] = []
    if user_id in reactions[emoji]:
        reactions[emoji].remove(user_id)
        if not reactions[emoji]:
            del reactions[emoji]
    else:
        reactions[emoji].append(user_id)
        # Notify message sender
        if msg.sender_id != user_id:
            me = User.query.get(user_id)
            _create_notification(msg.sender_id, 'reaction',
                                 f'{me.name} reacted {emoji} to your message',
                                 msg.content[:60], 'group', msg.group_id)
    msg.reactions = json.dumps(reactions)
    db.session.commit()
    return jsonify({'success': True, 'reactions': reactions})


@app.route('/api/ask-ai-group', methods=['POST'])
def ask_ai_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    token_check = require_tokens('ai_chat', 1)
    if token_check:
        return jsonify({'error': token_check['error']}), token_check['code']
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
        prompt = (
            f"You are BrainAI, an expert AI study assistant in a Brainspark group chat. "
            f"Group context: {context or 'General study group discussion'}\n"
            f"User question: {question}\n"
            f"Be helpful, clear, educational and encouraging. Format your response clearly."
        )
        if 'image' in request.files:
            f = request.files['image']
            if f and f.filename and allowed_file(f.filename):
                try:
                    img_bytes = f.read()
                    ext       = f.filename.rsplit('.', 1)[-1].lower()
                    mime_map  = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif'}
                    mime_type = mime_map.get(ext, 'image/jpeg')
                    response  = vision_model.generate_content([prompt, {'mime_type': mime_type, 'data': img_bytes}])
                except Exception:
                    response = model.generate_content(prompt)
            else:
                response = model.generate_content(prompt)
        else:
            response = model.generate_content(prompt)
        deduct_token(session['user_id'], 'ai_chat', 1)
        return jsonify({'success': True, 'explanation': response.text})
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500


# ═════════════════════════════════════════════════════════════════════════════
#  POLLS
# ═════════════════════════════════════════════════════════════════════════════

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
        poll_msg = GroupMessage(group_id=group_id, sender_id=user_id,
                                content=f"📊 Poll: {question}", message_type='poll', poll_id=poll.id)
        db.session.add(poll_msg)
        db.session.flush()
        # Notify members
        group = ChatGroup.query.get(group_id)
        creator = User.query.get(user_id)
        for m in ChatGroupMember.query.filter_by(group_id=group_id).all():
            if m.user_id == user_id or m.is_muted:
                continue
            _create_notification(m.user_id, 'group_message',
                                 f'📊 New Poll in {group.name}',
                                 f'{creator.name}: {question}', 'group', group_id)
        db.session.commit()
        return jsonify({'success': True, 'poll_id': poll.id, 'message_id': poll_msg.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


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


# ═════════════════════════════════════════════════════════════════════════════
#  BRAINSTORM  (enhanced)
# ═════════════════════════════════════════════════════════════════════════════

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
        sched_dt = datetime.fromisoformat(scheduled_time)
        teacher_id = data.get('teacher_id') or user_id  # default teacher is the scheduling admin
        sess_obj = BrainstormSession(
            group_id=group_id, title=title,
            description=description, scheduled_time=sched_dt,
            teacher_id=teacher_id
        )
        db.session.add(sess_obj)
        db.session.flush()

        group        = ChatGroup.query.get(group_id)
        scheduler    = User.query.get(user_id)
        group_members = ChatGroupMember.query.filter_by(group_id=group_id).all()

        for mr in group_members:
            if mr.user_id == user_id:
                continue
            # In-app notification
            _create_notification(
                mr.user_id, 'brainstorm_scheduled',
                f'📅 Brainstorm Scheduled: {title}',
                f'{scheduler.name} scheduled a session in {group.name} for {sched_dt.strftime("%b %d %H:%M")}.',
                'brainstorm', sess_obj.id
            )
            # Drop a brainstorm session card message in chat
            import json as _json
            session_card_data = _json.dumps({
                'id': sess_obj.id,
                'title': title,
                'description': description or 'Join us for a collaborative study session!',
                'scheduled_time': sched_dt.isoformat(),
                'status': 'scheduled',
                'teacher_id': teacher_id,
                'group_id': group_id
            })
            sys_msg = GroupMessage(
                group_id=group_id, sender_id=user_id,
                content=session_card_data,
                message_type='brainstorm_session'
            )
            db.session.add(sys_msg)
            break  # Only one system message per group

        db.session.commit()
        return jsonify({
            'success': True, 'session_id': sess_obj.id,
            'message': f'Session scheduled! Notified {len(group_members)-1} members'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-group-sessions/<int:group_id>')
def get_group_sessions(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'Not a member'}), 403
    is_admin = member.role == 'admin'
    group = ChatGroup.query.get(group_id)
    sessions = BrainstormSession.query.filter_by(group_id=group_id).order_by(BrainstormSession.scheduled_time.desc()).all()
    return jsonify({'success': True, 'sessions': [{
        'id': s.id, 'title': s.title, 'description': s.description,
        'scheduled_time': s.scheduled_time.isoformat(), 'status': s.status,
        'note_count': len(s.notes), 'created_at': s.created_at.isoformat(),
        'teacher_id': s.teacher_id,
        'teacher_name': s.teacher.name if s.teacher else None,
        'group_id': s.group_id,
        'group_name': group.name if group else '',
        'is_admin': is_admin
    } for s in sessions]})


@app.route('/api/get-session-details/<int:session_id>')
def get_session_details(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    s = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    member = ChatGroupMember.query.filter_by(group_id=s.group_id, user_id=session['user_id']).first()
    if not member:
        return jsonify({'error': 'Not a member'}), 403
    return jsonify({'success': True, 'session': {
        'id': s.id, 'title': s.title, 'description': s.description,
        'scheduled_time': s.scheduled_time.isoformat(), 'status': s.status,
        'whiteboard_data': s.whiteboard_data, 'shared_doc': s.shared_doc,
        'note_count': len(s.notes), 'group_id': s.group_id
    }})


@app.route('/api/update-session-doc', methods=['POST'])
def update_session_doc():
    """Save the shared brainstorm document."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data       = request.json or {}
    session_id = data.get('session_id')
    doc        = data.get('shared_doc', '')
    s          = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    if not ChatGroupMember.query.filter_by(group_id=s.group_id, user_id=session['user_id']).first():
        return jsonify({'error': 'Not a member'}), 403
    s.shared_doc = doc
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/update-session-status', methods=['POST'])
def update_session_status():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data       = request.json or {}
    session_id = data.get('session_id')
    status     = data.get('status')
    if status not in ('scheduled', 'ongoing', 'completed'):
        return jsonify({'error': 'Invalid status'}), 400
    s = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    member = ChatGroupMember.query.filter_by(group_id=s.group_id, user_id=session['user_id']).first()
    if not member or member.role != 'admin':
        return jsonify({'error': 'Only admins can update session status'}), 403
    s.status = status
    db.session.flush()
    if status == 'ongoing':
        # Notify all members
        group_members = ChatGroupMember.query.filter_by(group_id=s.group_id).all()
        for m in group_members:
            if m.user_id == session['user_id']:
                continue
            _create_notification(
                m.user_id, 'brainstorm_starting',
                f'🧠 Brainstorm Starting: {s.title}',
                f'Your session in {s.group.name} is starting now! Join in.',
                'brainstorm', s.id
            )
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/add-brainstorm-note', methods=['POST'])
def add_brainstorm_note():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id    = session['user_id']
    data       = request.json
    session_id = data.get('session_id')
    content    = data.get('content', '').strip()
    note_type  = data.get('note_type', 'text')
    color      = data.get('color', '#ff4f30')
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
            note_type=note_type, color=color,
            mentions=json.dumps(mentions) if mentions else None,
            tags=json.dumps(tags) if tags else None
        )
        db.session.add(note)
        db.session.flush()
        # Notify mentioned members
        group_members = ChatGroupMember.query.filter_by(group_id=sess_obj.group_id).all()
        author = User.query.get(user_id)
        for uid in mentions:
            if int(uid) != user_id:
                _create_notification(int(uid), 'mention',
                                     f'{author.name} mentioned you in brainstorm',
                                     content[:80], 'brainstorm', session_id)
        db.session.commit()
        return jsonify({'success': True, 'note': {
            'id': note.id, 'user_id': note.user_id, 'user_name': note.user.name,
            'user_pic': note.user.get_profile_pic_url(),
            'content': content, 'note_type': note_type, 'color': color,
            'mentions': mentions, 'tags': tags,
            'upvotes': 0, 'created_at': note.created_at.isoformat()
        }})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/upvote-brainstorm-note', methods=['POST'])
def upvote_brainstorm_note():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data    = request.json or {}
    note_id = data.get('note_id')
    note    = BrainstormNote.query.get(note_id)
    if not note:
        return jsonify({'error': 'Note not found'}), 404
    note.upvotes = (note.upvotes or 0) + 1
    db.session.commit()
    return jsonify({'success': True, 'upvotes': note.upvotes})


@app.route('/api/get-brainstorm-notes/<int:session_id>')
def get_brainstorm_notes(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    notes = BrainstormNote.query.filter_by(session_id=session_id).order_by(BrainstormNote.created_at.asc()).all()
    return jsonify({'success': True, 'notes': [{
        'id': n.id, 'user_id': n.user_id, 'user_name': n.user.name,
        'user_pic': n.user.get_profile_pic_url(), 'content': n.content,
        'note_type': n.note_type, 'color': n.color,
        'mentions':  json.loads(n.mentions)  if n.mentions  else [],
        'tags':      json.loads(n.tags)      if n.tags      else [],
        'upvotes': n.upvotes or 0,
        'mention_ai': n.mention_ai, 'has_media': n.has_media,
        'image_url':    f'/uploads/{n.image_path}'    if n.image_path    else None,
        'textbook_url': f'/uploads/{n.textbook_path}' if n.textbook_path else None,
        'solved_problem': n.solved_problem, 'created_at': n.created_at.isoformat()
    } for n in notes]})


# ═════════════════════════════════════════════════════════════════════════════
#  JOIN REQUESTS
# ═════════════════════════════════════════════════════════════════════════════

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
        db.session.flush()
        group = ChatGroup.query.get(join_request.group_id)
        _create_notification(join_request.user_id, 'join_approved',
                             f'Joined {group.name}!',
                             f'Your request to join "{group.name}" was approved.',
                             'group', join_request.group_id)
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
        db.session.flush()
        group = ChatGroup.query.get(join_request.group_id)
        _create_notification(join_request.user_id, 'join_rejected',
                             f'Request to join {group.name}',
                             f'Your join request was not accepted this time.',
                             'group', join_request.group_id)
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


# ═════════════════════════════════════════════════════════════════════════════
#  UNIFIED NOTIFICATIONS
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/get-unread-notifications')
def get_unread_notifications():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']

    # In-app notifications (new system)
    app_notifs = AppNotification.query.filter_by(user_id=user_id, is_read=False)\
        .order_by(AppNotification.created_at.desc()).limit(30).all()

    notifs_data = [{
        'id':         n.id,
        'type':       n.notif_type,
        'title':      n.title,
        'body':       n.body,
        'link_type':  n.link_type,
        'link_id':    n.link_id,
        'created_at': n.created_at.isoformat()
    } for n in app_notifs]

    # Legacy: direct messages unread count
    unread_dms = Message.query.filter_by(receiver_id=user_id, is_read=False).count()

    # Legacy: pending join requests for admin groups
    admin_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(user_id=user_id, role='admin').all()]
    pending_count   = GroupJoinRequest.query.filter(
        GroupJoinRequest.group_id.in_(admin_group_ids),
        GroupJoinRequest.status == 'pending'
    ).count() if admin_group_ids else 0

    total = len(app_notifs)

    return jsonify({
        'success': True,
        'unread_messages_count':  unread_dms,
        'pending_requests_count': pending_count,
        'total_notifications':    total,
        'notifications':          notifs_data
    })


@app.route('/api/mark-notifications-read', methods=['POST'])
def mark_notifications_read():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data    = request.json or {}
    ids     = data.get('ids', [])
    mark_all = data.get('all', False)
    user_id = session['user_id']
    if mark_all:
        AppNotification.query.filter_by(user_id=user_id, is_read=False).update({'is_read': True})
    elif ids:
        AppNotification.query.filter(
            AppNotification.id.in_(ids), AppNotification.user_id == user_id
        ).update({'is_read': True}, synchronize_session=False)
    db.session.commit()
    return jsonify({'success': True})


# ═════════════════════════════════════════════════════════════════════════════
#  PUSH SUBSCRIPTION
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/push-subscribe', methods=['POST'])
def push_subscribe():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    sub  = data.get('subscription')
    if not sub:
        return jsonify({'error': 'No subscription data'}), 400
    user = User.query.get(session['user_id'])
    if user:
        user.push_subscription = json.dumps(sub)
        db.session.commit()
    return jsonify({'success': True})


@app.route('/api/push-vapid-public-key')
def push_vapid_public_key():
    return jsonify({'public_key': VAPID_PUBLIC_KEY})


# ═════════════════════════════════════════════════════════════════════════════
#  QUIZ RESULTS & MASTERY
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/save-quiz-result', methods=['POST'])
def save_quiz_result():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        data       = request.json
        score      = data.get('score')
        time_taken = data.get('time_taken')
        answers    = data.get('answers', {})
        questions  = data.get('questions', [])

        if score is None:
            return jsonify({'success': False, 'error': 'Score is required'}), 400

        user_id = session['user_id']
        user    = User.query.get(user_id)
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        quiz = Quiz.query.first()
        if not quiz:
            quiz = Quiz(
                title='Generated Quiz',
                description='Quiz generated from uploaded PDF',
                subject='General', difficulty='medium',
                question_count=10, time_limit=300
            )
            db.session.add(quiz)
            db.session.flush()

        result = QuizResult(
            user_id=user_id, quiz_id=quiz.id, score=score,
            answers=json.dumps(answers), time_taken=time_taken,
            completed_at=datetime.utcnow()
        )
        db.session.add(result)

        if answers and isinstance(answers, dict) and questions:
            for q in questions:
                topic       = q.get('topic', 'General')
                q_text      = q.get('question', '')
                user_ans    = str(answers.get(q_text, '')).strip().upper()
                correct_ans = str(q.get('answer', '')).strip().upper()
                is_correct  = (user_ans == correct_ans)

                mastery = TopicMastery.query.filter_by(
                    user_id=user_id, topic=topic
                ).first()
                if not mastery:
                    mastery = TopicMastery(user_id=user_id, topic=topic)
                    db.session.add(mastery)

                mastery.total_questions  += 1
                mastery.attempts          = (mastery.attempts or 0) + 1
                mastery.updated_at        = datetime.utcnow()
                if is_correct:
                    mastery.correct_answers += 1
                else:
                    db.session.add(WrongAnswer(
                        user_id=user_id, topic=topic,
                        question_text=q_text,
                        correct_answer=correct_ans,
                        user_answer=user_ans
                    ))

        db.session.commit()
        session.pop('quiz_questions', None)
        session.modified = True

        return jsonify({
            'success': True,
            'message': 'Quiz result saved successfully!',
            'result_id': result.id
        }), 200

    except Exception as e:
        logger.error(f'Error saving quiz result: {str(e)}', exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


@app.route('/api/mastery-map')
def mastery_map():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    topics  = TopicMastery.query.filter_by(user_id=user_id).order_by(TopicMastery.updated_at.desc()).all()
    return jsonify({'success': True, 'topics': [{
        'topic':            t.topic,
        'mastery_score':    t.mastery_score,
        'level':            t.level,
        'total_questions':  t.total_questions,
        'correct_answers':  t.correct_answers,
        'attempts':         t.attempts or 0
    } for t in topics]})


# ═════════════════════════════════════════════════════════════════════════════
#  YOUTUBE
# ═════════════════════════════════════════════════════════════════════════════

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
            params={'part': 'snippet', 'q': query, 'type': 'video', 'maxResults': 4,
                    'relevanceLanguage': 'en', 'safeSearch': 'strict', 'key': api_key},
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
                'video_id': video_id, 'title': snippet['title'],
                'channel': snippet['channelTitle'],
                'thumbnail': snippet['thumbnails']['medium']['url'],
                'description': (snippet['description'][:120] + '…') if len(snippet['description']) > 120 else snippet['description'],
                'url': f'https://www.youtube.com/watch?v={video_id}'
            })
        return jsonify({'success': True, 'videos': videos, 'query': query})
    except Exception as e:
        logger.error(f"YouTube search error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to fetch videos'}), 500


# ═════════════════════════════════════════════════════════════════════════════
#  SETTINGS
# ═════════════════════════════════════════════════════════════════════════════

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
        existing = User.query.filter(db.func.lower(User.username) == username.lower(), User.id != user_id).first()
        if existing:
            return jsonify({'success': False, 'error': 'That username is already taken'})
        try:
            user.name       = name
            user.username   = username
            user.profession = profession if profession else None
            user.bio        = data.get('bio', '').strip()[:160] or None
            db.session.commit()
            session['username'] = username
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': 'Database error.'})

    elif stype == 'password':
        import re
        current_pw = data.get('current_password', '')
        new_pw     = data.get('new_password', '')
        if not current_pw or not new_pw:
            return jsonify({'success': False, 'error': 'Both passwords are required'})
        if not user.check_password(current_pw):
            return jsonify({'success': False, 'error': 'Current password is incorrect'})
        if len(new_pw) < 8:
            return jsonify({'success': False, 'error': 'New password must be at least 8 characters'})
        if not re.search(r'[A-Z]', new_pw):
            return jsonify({'success': False, 'error': 'Needs at least one uppercase letter'})
        if not re.search(r'\d', new_pw):
            return jsonify({'success': False, 'error': 'Needs at least one number'})
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', new_pw):
            return jsonify({'success': False, 'error': 'Needs at least one special character'})
        try:
            user.set_password(new_pw)
            db.session.commit()
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': 'Database error.'})

    elif stype == 'academic':
        school      = data.get('school', '').strip()
        study_level = data.get('study_level', '').strip()
        country     = data.get('country', '').strip()
        if not school or not study_level or not country:
            return jsonify({'success': False, 'error': 'All academic fields are required'})
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
            return jsonify({'success': False, 'error': 'Database error.'})

    elif stype == 'email':
        return jsonify({'success': False, 'error': 'Email changes are not permitted.'})

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
        return jsonify({'success': False, 'error': 'Invalid file type.'})
    file_bytes = file.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        return jsonify({'success': False, 'error': 'File too large. Max 5MB.'})

    temp_path = None
    try:
        user_id  = session['user_id']
        user     = User.query.get(user_id)
        ext      = file.filename.rsplit('.', 1)[1].lower()
        save_dir = app.config['PROFILE_UPLOAD_FOLDER']
        os.makedirs(save_dir, exist_ok=True)

        final_name = f"{user_id}.{ext}"
        final_path = os.path.join(save_dir, final_name)
        temp_path  = os.path.join(save_dir, f"tmp_{user_id}.{ext}")

        # Write to temp file first — never touch the live file until save succeeds
        with open(temp_path, 'wb') as f_out:
            f_out.write(file_bytes)

        # Delete any old pic with a DIFFERENT extension (e.g. old was .jpeg, new is .png)
        for existing_file in os.listdir(save_dir):
            if existing_file.startswith(f"{user_id}.") and existing_file != final_name:
                try:
                    os.remove(os.path.join(save_dir, existing_file))
                except Exception:
                    pass

        # Atomic rename — temp becomes the real file
        os.replace(temp_path, final_path)

        user.profile_pic = final_name
        db.session.commit()
        return jsonify({'success': True, 'url': f'/uploads/profiles/{final_name}'})

    except Exception as e:
        db.session.rollback()
        if temp_path:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
        logger.error(f"Profile pic upload failed: {e}", exc_info=True)
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
        if user.profile_pic:
            pic_path = os.path.join(app.config['PROFILE_UPLOAD_FOLDER'], user.profile_pic)
            if os.path.exists(pic_path):
                try: os.remove(pic_path)
                except Exception: pass
        Message.query.filter(
            (Message.sender_id == user_id) | (Message.receiver_id == user_id)
        ).delete(synchronize_session=False)
        Connection.query.filter(
            (Connection.user_id == user_id) | (Connection.connected_user_id == user_id)
        ).delete(synchronize_session=False)
        QuizResult.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        UserTag.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        GeneratedQuestion.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        AppNotification.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        WrongAnswer.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        TopicMastery.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        db.session.delete(user)
        db.session.commit()
        session.clear()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': 'Deletion failed.'})


# ═════════════════════════════════════════════════════════════════════════════
#  SIMILAR QUESTIONS
# ═════════════════════════════════════════════════════════════════════════════

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
    token_check = require_tokens('similar_questions', 1)
    if token_check:
        return jsonify({'success': False, 'error': token_check['error']}), token_check['code']

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
                all_texts.append(text.strip()[:3000])
                file_names.append(f.filename)
            else:
                # try the simple extractor as fallback
                f.seek(0)
                import tempfile as _tf
                suffix = '.' + (f.filename.rsplit('.', 1)[-1] if '.' in f.filename else 'pdf')
                with _tf.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp_path = tmp.name
                    f.save(tmp_path)
                fallback_text = extract_pdf_text_simple(tmp_path)
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
                if fallback_text and len(fallback_text.strip()) > 30:
                    all_texts.append(fallback_text.strip()[:3000])
                    file_names.append(f.filename)
                else:
                    logger.warning(f"PDF {f.filename} yielded no text from either extractor")
        except Exception as e:
            logger.warning(f"Could not read PDF {f.filename}: {e}")

    if not all_texts:
        return jsonify({'success': False, 'error': 'No readable text found in your PDFs. Make sure they are not scanned image PDFs — use text-based PDFs only.'}), 400

    combined_text = '\n\n---NEXT PDF---\n\n'.join(
        f"[PDF {i+1}: {file_names[i]}]\n{t[:2000]}" for i, t in enumerate(all_texts)
    )

    # Step 1: Fingerprint analysis with safe fallback
    fingerprint = {
        'detected_format': 'objective',
        'option_style': 'A B C D',
        'avg_question_length': 'medium',
        'difficulty_level': 'medium',
        'main_topics': ['General'],
        'question_styles': ['definition'],
        'subject_domain': subject_title or 'General'
    }
    try:
        analysis_prompt = (
            f"Analyse these exam papers and extract their structural pattern.\n"
            f"PAPERS:\n{combined_text[:5000]}\n"
            f"Return ONLY valid JSON, no markdown, no extra text:\n"
            f'{{"detected_format":"objective","option_style":"A B C D","avg_question_length":"medium","difficulty_level":"medium","main_topics":["Topic1","Topic2"],"question_styles":["MCQ"],"subject_domain":"Subject Name"}}'
        )
        ar = model.generate_content(analysis_prompt).text.strip()
        # strip markdown code fences if present
        ar = ar.replace('```json', '').replace('```', '').strip()
        s_idx = ar.find('{')
        e_idx = ar.rfind('}') + 1
        if s_idx != -1 and e_idx > s_idx:
            parsed = json.loads(ar[s_idx:e_idx])
            # only overwrite keys that are present and non-empty
            for key in ('detected_format', 'option_style', 'avg_question_length',
                        'difficulty_level', 'main_topics', 'question_styles', 'subject_domain'):
                val = parsed.get(key)
                if val and (not isinstance(val, list) or len(val) > 0):
                    fingerprint[key] = val
    except Exception as fp_err:
        logger.warning(f"Fingerprint analysis failed (using defaults): {fp_err}")

    effective_difficulty = fingerprint.get('difficulty_level', 'medium') if hardness == 'same' else hardness
    detected_format = fingerprint.get('detected_format', 'objective')
    # flatten lists safely
    raw_topics = fingerprint.get('main_topics', ['General Content'])
    topics_str = ', '.join(raw_topics) if isinstance(raw_topics, list) else str(raw_topics)
    raw_styles = fingerprint.get('question_styles', ['MCQ'])
    q_styles_str = ', '.join(raw_styles) if isinstance(raw_styles, list) else str(raw_styles)
    domain = fingerprint.get('subject_domain', subject_title or 'General')

    # Step 2: Generate paper
    if detected_format == 'theory':
        gen_prompt = (
            f"You are an expert exam setter. Generate a NEW theory exam paper.\n"
            f"Subject: {domain}\n"
            f"Topics: {topics_str}\n"
            f"Question styles: {q_styles_str}\n"
            f"Difficulty: {effective_difficulty}\n"
            f"Number of questions: {question_count}\n\n"
            f"Return ONLY valid JSON with NO markdown fences and NO extra text:\n"
            f'{{"paper_title":"Exam Title","subject":"{domain}","difficulty":"{effective_difficulty}","format":"theory","instructions":"Answer all questions.","questions":[{{"number":1,"question":"Question text here","marks":5,"type":"short_answer","model_answer":"Model answer here","topic":"Topic name"}}]}}'
        )
    else:
        gen_prompt = (
            f"You are an expert exam setter. Generate a NEW multiple choice exam paper.\n"
            f"Subject: {domain}\n"
            f"Topics: {topics_str}\n"
            f"Question styles: {q_styles_str}\n"
            f"Difficulty: {effective_difficulty}\n"
            f"Number of questions: {question_count}\n\n"
            f"Rules:\n"
            f"- Every question must have exactly 4 options labelled A, B, C, D\n"
            f"- The 'answer' field must be exactly one letter: A, B, C, or D\n"
            f"- All questions must be unique\n\n"
            f"Return ONLY valid JSON with NO markdown fences and NO extra text:\n"
            f'{{"paper_title":"Exam Title","subject":"{domain}","difficulty":"{effective_difficulty}","format":"objective","instructions":"Answer ALL questions. Each question carries equal marks.","questions":[{{"number":1,"question":"Question text","options":["A. option one","B. option two","C. option three","D. option four"],"answer":"A","explanation":"Why A is correct","topic":"Topic name","marks":1}}]}}'
        )

    paper_data = None
    last_error = 'AI did not return valid JSON.'
    for attempt in range(2):  # try twice before giving up
        try:
            gr = model.generate_content(gen_prompt).text.strip()
            gr = gr.replace('```json', '').replace('```', '').strip()
            s2 = gr.find('{')
            e2 = gr.rfind('}') + 1
            if s2 == -1 or e2 <= s2:
                last_error = 'AI response contained no JSON object.'
                logger.warning(f"Attempt {attempt+1}: no JSON braces found. Raw: {gr[:300]}")
                continue
            paper_data = json.loads(gr[s2:e2])
            if paper_data.get('questions'):
                break
            else:
                last_error = 'AI returned JSON but questions list was empty.'
                paper_data = None
        except json.JSONDecodeError as jde:
            last_error = f'JSON parse error: {str(jde)[:120]}'
            logger.warning(f"Attempt {attempt+1} JSON decode error: {jde}. Raw snippet: {gr[:300] if 'gr' in dir() else 'N/A'}")
        except Exception as gen_err:
            last_error = f'AI call failed: {str(gen_err)[:150]}'
            logger.error(f"Attempt {attempt+1} generation error: {gen_err}", exc_info=True)
            break

    if not paper_data or not paper_data.get('questions'):
        return jsonify({'success': False, 'error': f'Could not generate paper. {last_error} Please try again.'}), 500

    questions = paper_data['questions']

    # Sanitise questions — ensure required fields exist
    clean_questions = []
    for i, q in enumerate(questions):
        if not q.get('question'):
            continue
        q.setdefault('number', i + 1)
        q.setdefault('marks', 1)
        q.setdefault('topic', 'General')
        if detected_format != 'theory':
            q.setdefault('options', ['A. Option A', 'B. Option B', 'C. Option C', 'D. Option D'])
            q.setdefault('answer', 'A')
            q.setdefault('explanation', '')
        else:
            q.setdefault('model_answer', '')
            q.setdefault('type', 'short_answer')
        clean_questions.append(q)

    if not clean_questions:
        return jsonify({'success': False, 'error': 'AI generated questions were all malformed. Please try again.'}), 500

    paper_data['questions'] = clean_questions

    return jsonify({
        'success': True,
        'paper': paper_data,
        'source_files': file_names,
        'fingerprint': fingerprint,
        'question_count': len(clean_questions),
        'include_answers': include_ans
    })


# ═════════════════════════════════════════════════════════════════════════════
#  STATIC / MISC
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    os.makedirs(os.path.join(uploads_dir, 'group_chat'), exist_ok=True)
    os.makedirs(os.path.join(uploads_dir, 'voice_notes'), exist_ok=True)
    os.makedirs(os.path.join(uploads_dir, 'profiles'), exist_ok=True)
    os.makedirs(os.path.join(uploads_dir, 'ai_chat'), exist_ok=True)
    return send_from_directory(uploads_dir, filename)
@app.route('/api/file/<path:filename>')
def serve_db_file(filename):
    try:
        from models import GroupFile
        gf = GroupFile.query.filter_by(filename=filename).first()
        if not gf:
            return jsonify({'error': 'File not found'}), 404
        file_bytes = base64.b64decode(gf.file_data)
        from flask import Response
        return Response(file_bytes, mimetype=gf.mime_type)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/run-migration-xyz123')
def run_migration():
    results = []
    migrations = [
        "ALTER TABLE token_transaction ADD COLUMN IF NOT EXISTS reference_code VARCHAR(20)",
        "ALTER TABLE message ADD COLUMN IF NOT EXISTS image_path VARCHAR(500)",
        "ALTER TABLE token_transaction ADD COLUMN IF NOT EXISTS verified_by INTEGER",
        "ALTER TABLE token_transaction ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP",
    ]
    try:
        with db.engine.connect() as conn:
            for sql in migrations:
                try:
                    conn.execute(db.text(sql))
                    results.append(f'OK: {sql[:60]}')
                except Exception as e:
                    results.append(f'SKIP: {str(e)[:80]}')
            conn.commit()
        return '<br>'.join(results) + '<br><strong>Done!</strong>'
    except Exception as e:
        return f'Error: {str(e)}'
@app.route('/legal')
def legal():
    return render_template('legal.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

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
        })
    except Exception as e:
        logger.error(f"DB status error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
    
@app.route('/api/join-brainstorm-session', methods=['POST'])
def join_brainstorm_session():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    session_id = data.get('session_id')
    s = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    # Any group member can join
    if not ChatGroupMember.query.filter_by(group_id=s.group_id, user_id=session['user_id']).first():
        return jsonify({'error': 'Not a group member'}), 403
    return jsonify({'success': True, 'session': {
        'id': s.id, 'title': s.title, 'shared_doc': s.shared_doc,
        'whiteboard_data': s.whiteboard_data, 'status': s.status,
        'group_id': s.group_id, 'teacher_id': s.teacher_id
    }})


@app.route('/api/leave-brainstorm-session', methods=['POST'])
def leave_brainstorm_session():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    # Leaving is always allowed — just a client-side state reset
    return jsonify({'success': True})

@app.route('/api/leave-group', methods=['POST'])
def leave_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    data = request.json or {}
    group_id = data.get('group_id')
    group = ChatGroup.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    if group.created_by == user_id:
        return jsonify({'error': 'Group creator cannot leave. Delete the group instead.'}), 403
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'Not a member'}), 404
    try:
        db.session.delete(member)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/delete-group', methods=['POST'])
def delete_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    data = request.json or {}
    group_id = data.get('group_id')
    group = ChatGroup.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    if group.created_by != user_id:
        return jsonify({'error': 'Only the group creator can delete this group'}), 403
    try:
        # Delete related records that may not cascade automatically
        GroupJoinRequest.query.filter_by(group_id=group_id).delete(synchronize_session=False)
        AppNotification.query.filter_by(link_type='group', link_id=group_id).delete(synchronize_session=False)
        # Delete brainstorm sessions and their notes/handraises
        sessions = BrainstormSession.query.filter_by(group_id=group_id).all()
        for s in sessions:
            HandRaise.query.filter_by(session_id=s.id).delete(synchronize_session=False)
            BrainstormNote.query.filter_by(session_id=s.id).delete(synchronize_session=False)
        BrainstormSession.query.filter_by(group_id=group_id).delete(synchronize_session=False)
        # Null out poll_id on messages before deleting polls (avoids FK constraint)
        GroupMessage.query.filter_by(group_id=group_id).update({'poll_id': None}, synchronize_session=False)
        db.session.flush()
        # Delete polls and their options/votes
        polls = Poll.query.filter_by(group_id=group_id).all()
        for p in polls:
            PollVote.query.filter_by(poll_id=p.id).delete(synchronize_session=False)
            PollOption.query.filter_by(poll_id=p.id).delete(synchronize_session=False)
        Poll.query.filter_by(group_id=group_id).delete(synchronize_session=False)
        # Delete messages and members
        GroupMessage.query.filter_by(group_id=group_id).delete(synchronize_session=False)
        ChatGroupMember.query.filter_by(group_id=group_id).delete(synchronize_session=False)
        db.session.delete(group)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/rename-group', methods=['POST'])
def rename_group():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    data = request.json or {}
    group_id = data.get('group_id')
    new_name = data.get('name', '').strip()
    new_desc = data.get('description', '').strip()
    if not new_name or len(new_name) < 3:
        return jsonify({'error': 'Group name must be at least 3 characters'}), 400
    group = ChatGroup.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id, role='admin').first()
    if not member:
        return jsonify({'error': 'Only admins can rename the group'}), 403
    try:
        group.name = new_name
        group.description = new_desc
        db.session.commit()
        return jsonify({'success': True, 'message': 'Group renamed!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/set-session-teacher', methods=['POST'])
def set_session_teacher():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    data = request.json or {}
    session_id = data.get('session_id')
    teacher_user_id = data.get('teacher_id')
    s = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    # Only admins can assign teacher
    member = ChatGroupMember.query.filter_by(group_id=s.group_id, user_id=user_id, role='admin').first()
    if not member:
        return jsonify({'error': 'Only admins can assign a teacher'}), 403
    # Teacher must be a group member
    if not ChatGroupMember.query.filter_by(group_id=s.group_id, user_id=teacher_user_id).first():
        return jsonify({'error': 'Teacher must be a group member'}), 400
    s.teacher_id = teacher_user_id
    db.session.commit()
    teacher = User.query.get(teacher_user_id)
    # Notify the assigned teacher
    _create_notification(teacher_user_id, 'brainstorm_scheduled',
                         f'You are the teacher for "{s.title}"',
                         'You can now broadcast voice during the session.',
                         'brainstorm', session_id)
    return jsonify({'success': True, 'teacher_name': teacher.name})

# ═════════════════════════════════════════════════════════════════════════════
#  WHITEBOARD SYNC
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/save-whiteboard', methods=['POST'])
def save_whiteboard():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    session_id = data.get('session_id')
    image_data = data.get('image_data', '')  # base64 PNG
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400
    s = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    # Only teacher can save whiteboard
    if s.teacher_id and s.teacher_id != session['user_id']:
        return jsonify({'error': 'Only the teacher can draw on the whiteboard'}), 403
    s.whiteboard_data = image_data
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/get-whiteboard/<int:session_id>')
def get_whiteboard(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    s = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    if not ChatGroupMember.query.filter_by(group_id=s.group_id, user_id=session['user_id']).first():
        return jsonify({'error': 'Not a member'}), 403
    return jsonify({'success': True, 'image_data': s.whiteboard_data or '', 'teacher_id': s.teacher_id})


# ═════════════════════════════════════════════════════════════════════════════
#  HAND RAISE SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

@app.route('/api/raise-hand', methods=['POST'])
def raise_hand():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    session_id = data.get('session_id')
    s = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    if not ChatGroupMember.query.filter_by(group_id=s.group_id, user_id=session['user_id']).first():
        return jsonify({'error': 'Not a member'}), 403
    # Check if already raised
    existing = HandRaise.query.filter_by(session_id=session_id, user_id=session['user_id'], status='raised').first()
    if existing:
        return jsonify({'success': True, 'already_raised': True, 'raise_id': existing.id})
    raise_obj = HandRaise(session_id=session_id, user_id=session['user_id'])
    db.session.add(raise_obj)
    db.session.flush()
    # Notify teacher
    if s.teacher_id:
        me = User.query.get(session['user_id'])
        _create_notification(s.teacher_id, 'mention', f'✋ {me.name} raised their hand',
                             'Click to acknowledge in the brainstorm session.', 'brainstorm', session_id)
    db.session.commit()
    return jsonify({'success': True, 'raise_id': raise_obj.id})


@app.route('/api/lower-hand', methods=['POST'])
def lower_hand():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    session_id = data.get('session_id')
    HandRaise.query.filter_by(session_id=session_id, user_id=session['user_id'], status='raised').update({'status': 'dismissed'})
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/get-raised-hands/<int:session_id>')
def get_raised_hands(session_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    s = BrainstormSession.query.get(session_id)
    if not s:
        return jsonify({'error': 'Session not found'}), 404
    hands = HandRaise.query.filter_by(session_id=session_id).filter(
        HandRaise.status.in_(['raised', 'acknowledged'])
    ).order_by(HandRaise.created_at.asc()).all()
    return jsonify({'success': True, 'hands': [{
        'id': h.id, 'user_id': h.user_id, 'user_name': h.user.name,
        'user_pic': h.user.get_profile_pic_url(),
        'status': h.status, 'question_text': h.question_text,
        'created_at': h.created_at.isoformat()
    } for h in hands]})


@app.route('/api/acknowledge-hand', methods=['POST'])
def acknowledge_hand():
    """Teacher acknowledges a raised hand — member now gets a text box."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    raise_id = data.get('raise_id')
    h = HandRaise.query.get(raise_id)
    if not h:
        return jsonify({'error': 'Not found'}), 404
    s = BrainstormSession.query.get(h.session_id)
    if not s or s.teacher_id != session['user_id']:
        return jsonify({'error': 'Only the teacher can acknowledge hands'}), 403
    h.status = 'acknowledged'
    db.session.flush()
    # Notify the student
    _create_notification(h.user_id, 'mention', '✅ Teacher acknowledged your hand!',
                         'You can now type your question.', 'brainstorm', h.session_id)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/submit-question', methods=['POST'])
def submit_question():
    """Member submits their typed question after hand is acknowledged."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    raise_id = data.get('raise_id')
    question_text = data.get('question', '').strip()
    if not question_text:
        return jsonify({'error': 'Question text required'}), 400
    h = HandRaise.query.get(raise_id)
    if not h or h.user_id != session['user_id']:
        return jsonify({'error': 'Not found or not yours'}), 404
    h.question_text = question_text
    h.status = 'raised'  # back to raised so teacher sees the question
    db.session.flush()
    s = BrainstormSession.query.get(h.session_id)
    if s and s.teacher_id:
        me = User.query.get(session['user_id'])
        _create_notification(s.teacher_id, 'mention',
                             f'❓ {me.name} asks: {question_text[:60]}',
                             question_text[:120], 'brainstorm', h.session_id)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/dismiss-hand', methods=['POST'])
def dismiss_hand():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.json or {}
    raise_id = data.get('raise_id')
    h = HandRaise.query.get(raise_id)
    if not h:
        return jsonify({'error': 'Not found'}), 404
    h.status = 'answered'
    h.answered_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/check-my-hand/<int:session_id>')
def check_my_hand(session_id):
    """Members poll this to check if their hand was acknowledged."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    h = HandRaise.query.filter_by(
        session_id=session_id, user_id=session['user_id']
    ).filter(HandRaise.status.in_(['raised', 'acknowledged'])).order_by(HandRaise.created_at.desc()).first()
    if not h:
        return jsonify({'success': True, 'status': None, 'raise_id': None})
    return jsonify({'success': True, 'status': h.status, 'raise_id': h.id, 'question_text': h.question_text})



# ═════════════════════════════════════════════════════════════════════════════
#  SPARK TOKEN SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

BANK_NAME     = "Moniepoint MFB"
ACCOUNT_NAME  = "Brainspark Technologies"
ACCOUNT_NUMBER = "1234567890"  # replace with your real account number
PLATFORM_FEE  = 500
MIN_PAYMENT   = 1500
MAX_PAYMENT   = 6000
ADMIN_USERNAME = "Peace1"

os.makedirs('uploads/receipts', exist_ok=True)


def deduct_token(user_id, feature='ai', tokens=1):
    """Deduct spark tokens and log usage. Returns True if successful."""
    user = User.query.get(user_id)
    if not user:
        return False
    tokens_available = getattr(user, 'spark_tokens', 0) or 0
    if tokens_available < tokens:
        return False
    user.spark_tokens = tokens_available - tokens
    log = TokenUsageLog(user_id=user_id, feature=feature, tokens_used=tokens)
    db.session.add(log)
    db.session.commit()
    return True


def require_tokens(feature='ai', tokens=1):
    """Decorator/check — returns error dict if user has no tokens."""
    if 'user_id' not in session:
        return {'error': 'Unauthorized', 'code': 401}
    user = User.query.get(session['user_id'])
    if not user:
        return {'error': 'User not found', 'code': 404}
    available = getattr(user, 'spark_tokens', 0) or 0
    if available < tokens:
        return {'error': 'NO_TOKENS', 'code': 402}
    return None


@app.route('/tokens')
def tokens_page():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    user  = User.query.get(session['user_id'])
    theme = session.get('theme', 'dark')
    return render_template('tokens.html', user=user, theme=theme,
                           bank_name=BANK_NAME, account_name=ACCOUNT_NAME,
                           account_number=ACCOUNT_NUMBER,
                           min_payment=MIN_PAYMENT, max_payment=MAX_PAYMENT,
                           platform_fee=PLATFORM_FEE)


@app.route('/api/submit-payment', methods=['POST'])
def submit_payment():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    # Verify password first
    password = request.form.get('password', '')
    user = User.query.get(session['user_id'])
    if not user.check_password(password):
        return jsonify({'success': False, 'error': 'Incorrect password.'}), 403

    amount_str = request.form.get('amount', '')
    try:
        amount = float(amount_str)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid amount.'}), 400

    if amount < MIN_PAYMENT or amount > MAX_PAYMENT:
        return jsonify({'success': False,
                        'error': f'Amount must be between ₦{MIN_PAYMENT:,} and ₦{MAX_PAYMENT:,}.'}), 400

    receipt = request.files.get('receipt')
    if not receipt or not receipt.filename:
        return jsonify({'success': False, 'error': 'Receipt is required.'}), 400

    if not allowed_file(receipt.filename):
        return jsonify({'success': False, 'error': 'Receipt must be a PDF or image.'}), 400

    try:
        ext = receipt.filename.rsplit('.', 1)[1].lower()
        ts  = int(datetime.utcnow().timestamp() * 1000)
        filename = f"receipt_{session['user_id']}_{ts}.{ext}"
        receipt.save(os.path.join('uploads/receipts', filename))

        tokens_added = int(amount - PLATFORM_FEE)

        txn = TokenTransaction(
            user_id=session['user_id'],
            amount_paid=amount,
            platform_fee=PLATFORM_FEE,
            tokens_added=tokens_added,
            receipt_path=filename,
            status='pending'
        )
        db.session.add(txn)
        db.session.commit()

        # Notify admin
        admin = User.query.filter_by(username=ADMIN_USERNAME).first()
        if admin:
            _create_notification(admin.id, 'payment',
                                 f'New payment from {user.name}',
                                 f'₦{amount:,.0f} — {tokens_added} tokens pending verification.',
                                 'admin', txn.id)

        return jsonify({'success': True,
                        'message': 'Payment submitted! We will verify within a few minutes.',
                        'transaction_id': txn.id})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Payment submission error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)[:200]}), 500
@app.route('/api/generate-payment-ref', methods=['POST'])
def generate_payment_ref():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    import random, string
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    amount = data.get('amount', 0)
    user = User.query.get(session['user_id'])
    if not user or not user.check_password(password):
        return jsonify({'success': False, 'error': 'Incorrect password.'}), 403
    try:
        amount = float(amount)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid amount.'}), 400
    if amount < MIN_PAYMENT or amount > MAX_PAYMENT:
        return jsonify({'success': False, 'error': f'Amount must be ₦{MIN_PAYMENT:,}–₦{MAX_PAYMENT:,}.'}), 400
    ref = 'BSP-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    tokens_added = int(amount - PLATFORM_FEE)
    txn = TokenTransaction(
        user_id=session['user_id'],
        amount_paid=amount,
        platform_fee=PLATFORM_FEE,
        tokens_added=tokens_added,
        receipt_path='',
        status='pending',
        reference_code=ref
    )
    db.session.add(txn)
    db.session.commit()
    return jsonify({'success': True, 'reference': ref, 'transaction_id': txn.id})

@app.route('/api/expire-payment-ref', methods=['POST'])
def expire_payment_ref():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    txn_id = data.get('transaction_id')
    if txn_id:
        txn = TokenTransaction.query.filter_by(id=txn_id, user_id=session['user_id'], status='pending').first()
        if txn:
            txn.status = 'rejected'
            db.session.commit()
    return jsonify({'success': True})

@app.route('/api/verify-payment-receipt', methods=['POST'])
def verify_payment_receipt():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    txn_id = request.form.get('transaction_id')
    receipt = request.files.get('receipt')
    if not txn_id or not receipt:
        return jsonify({'success': False, 'error': 'Missing data.'}), 400
    txn = TokenTransaction.query.filter_by(id=txn_id, user_id=session['user_id'], status='pending').first()
    if not txn:
        return jsonify({'success': False, 'error': 'Transaction not found or already processed.'}), 404
    # Check 5-minute window
    elapsed = (datetime.utcnow() - txn.created_at).total_seconds()
    if elapsed > 360:  # 6 min grace (5 min + 1 min buffer)
        txn.status = 'rejected'
        db.session.commit()
        return jsonify({'success': False, 'error': 'Payment window expired. Please start a new transaction.'}), 400
    if not allowed_file(receipt.filename):
        return jsonify({'success': False, 'error': 'Invalid file type.'}), 400
    try:
        ext = receipt.filename.rsplit('.', 1)[1].lower()
        ts = int(datetime.utcnow().timestamp() * 1000)
        filename = f"receipt_{session['user_id']}_{ts}.{ext}"
        receipt_path = os.path.join('uploads', 'receipts', filename)
        os.makedirs(os.path.join('uploads', 'receipts'), exist_ok=True)
        receipt_bytes = receipt.read()
        with open(receipt_path, 'wb') as f:
            f.write(receipt_bytes)
        txn.receipt_path = filename
        db.session.flush()
        # AI verification using Gemini vision
        user = User.query.get(session['user_id'])
        verification_prompt = (
            f"You are a payment verification system. Analyse this bank transfer receipt image carefully.\n\n"
            f"Expected details to verify:\n"
            f"- Account Number: {ACCOUNT_NUMBER}\n"
            f"- Account Name: {ACCOUNT_NAME}\n"
            f"- Bank: {BANK_NAME}\n"
            f"- Amount: ₦{txn.amount_paid:,.0f}\n"
            f"- Reference/Narration must contain: {txn.reference_code}\n"
            f"- Transfer must be dated today or yesterday (current UTC date: {datetime.utcnow().strftime('%Y-%m-%d')})\n\n"
            f"Check the receipt image and respond ONLY with valid JSON:\n"
            f'{{"verified": true/false, "reason": "brief explanation", '
            f'"found_amount": "amount seen", "found_reference": "reference seen", '
            f'"found_account": "account number seen", "found_date": "date seen"}}'
        )
        try:
            mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                        'gif': 'image/gif', 'webp': 'image/webp', 'pdf': 'application/pdf'}
            mime_type = mime_map.get(ext, 'image/jpeg')
            if ext == 'pdf':
                # For PDFs extract text and verify textually
                pdf_text = extract_pdf_text_simple(receipt_path)
                text_prompt = (
                    f"You are a payment verification system. Analyse this bank transfer receipt text.\n\n"
                    f"Receipt text:\n{pdf_text[:3000]}\n\n"
                    f"Expected details:\n"
                    f"- Account Number: {ACCOUNT_NUMBER}\n"
                    f"- Account Name: {ACCOUNT_NAME}\n"
                    f"- Amount: ₦{txn.amount_paid:,.0f}\n"
                    f"- Reference/Narration must contain: {txn.reference_code}\n"
                    f"- Transfer must be dated today or yesterday (current UTC date: {datetime.utcnow().strftime('%Y-%m-%d')})\n\n"
                    f"Respond ONLY with valid JSON:\n"
                    f'{{"verified": true, "reason": "brief explanation", '
                    f'"found_amount": "amount seen", "found_reference": "reference seen", '
                    f'"found_account": "account seen", "found_date": "date seen"}}'
                )
                ai_resp = model.generate_content(text_prompt)
            else:
                with open(receipt_path, 'rb') as img_f:
                    img_bytes = img_f.read()
                ai_resp = vision_model.generate_content([
                    verification_prompt,
                    {'mime_type': mime_type, 'data': img_bytes}
                ])
            resp_text = ai_resp.text.strip()
            resp_text = resp_text.replace('```json', '').replace('```', '').strip()
            s_idx = resp_text.find('{')
            e_idx = resp_text.rfind('}') + 1
            if s_idx == -1 or e_idx <= s_idx:
                raise ValueError('No JSON in AI response')
            result = json.loads(resp_text[s_idx:e_idx])
            verified = result.get('verified', False)
        except Exception as ai_err:
            logger.error(f"AI verification error: {ai_err}", exc_info=True)
            # Fall back to manual review if AI fails
            verified = False
            result = {'reason': 'AI verification unavailable — your payment has been queued for manual review.'}
        if verified:
            txn.status = 'approved'
            txn.verified_by = 0  # 0 = auto-verified
            txn.verified_at = datetime.utcnow()
            user.spark_tokens = (getattr(user, 'spark_tokens', 0) or 0) + txn.tokens_added
            user.total_tokens_purchased = (getattr(user, 'total_tokens_purchased', 0) or 0) + txn.tokens_added
            user.total_spent = (getattr(user, 'total_spent', 0) or 0) + txn.amount_paid
            db.session.commit()
            _create_notification(session['user_id'], 'payment',
                                 f'✅ {txn.tokens_added:,} Spark Tokens added!',
                                 f'Your payment of ₦{txn.amount_paid:,.0f} was verified automatically.',
                                 'tokens', txn.id)
            # Also notify admin
            admin = User.query.filter_by(username=ADMIN_USERNAME).first()
            if admin:
                _create_notification(admin.id, 'payment',
                                     f'Auto-verified: {user.name}',
                                     f'₦{txn.amount_paid:,.0f} — {txn.tokens_added} tokens added.',
                                     'admin', txn.id)
            return jsonify({
                'success': True,
                'tokens_added': txn.tokens_added,
                'message': f'{txn.tokens_added:,} Spark Tokens added to your account!'
            })
        else:
            # Keep as pending for manual admin review
            db.session.commit()
            reason = result.get('reason', 'Could not verify payment details.')
            # Notify admin for manual check
            admin = User.query.filter_by(username=ADMIN_USERNAME).first()
            if admin:
                _create_notification(admin.id, 'payment',
                                     f'Manual review needed: {user.name}',
                                     f'₦{txn.amount_paid:,.0f} — AI could not verify. Reason: {reason[:80]}',
                                     'admin', txn.id)
            return jsonify({
                'success': False,
                'error': f'Verification failed: {reason}\n\nYour payment has been queued for manual review. You will be notified within 30 minutes.',
                'queued': True
            })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Receipt verification error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'Verification error: {str(e)[:200]}'}), 500

@app.route('/api/my-token-stats')
def my_token_stats():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    user    = User.query.get(user_id)

    logs = TokenUsageLog.query.filter_by(user_id=user_id)\
        .order_by(TokenUsageLog.created_at.desc()).limit(100).all()

    # Daily usage last 7 days
    daily = []
    for i in range(7):
        day = datetime.utcnow() - timedelta(days=6-i)
        day_start = day.replace(hour=0,minute=0,second=0,microsecond=0)
        day_end   = day.replace(hour=23,minute=59,second=59,microsecond=999999)
        used = db.session.query(db.func.sum(TokenUsageLog.tokens_used))\
            .filter(TokenUsageLog.user_id==user_id,
                    TokenUsageLog.created_at>=day_start,
                    TokenUsageLog.created_at<=day_end).scalar() or 0
        daily.append({'day': day.strftime('%a'), 'tokens': used})

    transactions = TokenTransaction.query.filter_by(user_id=user_id)\
        .order_by(TokenTransaction.created_at.desc()).limit(20).all()

    return jsonify({
        'success': True,
        'spark_tokens': getattr(user, 'spark_tokens', 0) or 0,
        'total_purchased': getattr(user, 'total_tokens_purchased', 0) or 0,
        'total_spent': getattr(user, 'total_spent', 0) or 0,
        'daily_usage': daily,
        'recent_logs': [{'feature': l.feature, 'tokens': l.tokens_used,
                          'date': l.created_at.isoformat()} for l in logs],
        'transactions': [{'id': t.id, 'amount': t.amount_paid,
                           'tokens': t.tokens_added, 'status': t.status,
                           'date': t.created_at.isoformat()} for t in transactions]
    })


# ── ADMIN ROUTES ─────────────────────────────────────────────────────────────

@app.route('/admin')
def admin_panel():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    if not user or user.username != ADMIN_USERNAME:
        return redirect(url_for('dashboard'))
    theme = session.get('theme', 'dark')
    return render_template('admin.html', user=user, theme=theme)


@app.route('/api/admin/stats')
def admin_stats():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user = User.query.get(session['user_id'])
    if not user or user.username != ADMIN_USERNAME:
        return jsonify({'error': 'Forbidden'}), 403

    total_revenue = db.session.query(
        db.func.sum(TokenTransaction.amount_paid)
    ).filter_by(status='approved').scalar() or 0

    total_profit = db.session.query(
        db.func.sum(TokenTransaction.platform_fee)
    ).filter_by(status='approved').scalar() or 0

    total_api_cost = total_revenue - total_profit

    total_tokens_issued = db.session.query(
        db.func.sum(TokenTransaction.tokens_added)
    ).filter_by(status='approved').scalar() or 0

    pending_txns = TokenTransaction.query.filter_by(status='pending')\
        .order_by(TokenTransaction.created_at.desc()).all()

    all_users = User.query.all()
    user_stats = []
    for u in all_users:
        tokens = getattr(u, 'spark_tokens', 0) or 0
        spent  = getattr(u, 'total_spent', 0) or 0
        if tokens > 0 or spent > 0:
            user_stats.append({
                'id': u.id, 'name': u.name, 'username': u.username,
                'spark_tokens': tokens, 'total_spent': spent,
                'total_purchased': getattr(u, 'total_tokens_purchased', 0) or 0
            })

    return jsonify({
        'success': True,
        'total_revenue': total_revenue,
        'total_profit': total_profit,
        'total_api_cost': total_api_cost,
        'total_tokens_issued': total_tokens_issued,
        'pending_count': len(pending_txns),
        'pending_transactions': [{
            'id': t.id, 'user_id': t.user_id,
            'user_name': t.user.name, 'user_email': t.user.email,
            'amount_paid': t.amount_paid, 'tokens_added': t.tokens_added,
            'receipt_path': t.receipt_path,
            'created_at': t.created_at.isoformat()
        } for t in pending_txns],
        'user_stats': user_stats
    })


@app.route('/api/admin/verify-payment', methods=['POST'])
def admin_verify_payment():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    admin = User.query.get(session['user_id'])
    if not admin or admin.username != ADMIN_USERNAME:
        return jsonify({'error': 'Forbidden'}), 403

    data   = request.json or {}
    txn_id = data.get('transaction_id')
    action = data.get('action', 'approve')  # approve or reject

    txn = TokenTransaction.query.get(txn_id)
    if not txn:
        return jsonify({'error': 'Transaction not found'}), 404

    try:
        txn.status      = 'approved' if action == 'approve' else 'rejected'
        txn.verified_by = session['user_id']
        txn.verified_at = datetime.utcnow()

        if action == 'approve':
            user = User.query.get(txn.user_id)
            user.spark_tokens = (getattr(user, 'spark_tokens', 0) or 0) + txn.tokens_added
            user.total_tokens_purchased = (getattr(user, 'total_tokens_purchased', 0) or 0) + txn.tokens_added
            user.total_spent = (getattr(user, 'total_spent', 0) or 0) + txn.amount_paid
            _create_notification(txn.user_id, 'payment',
                                 f'✅ {txn.tokens_added:,} Spark Tokens added!',
                                 f'Your payment of ₦{txn.amount_paid:,.0f} has been verified.',
                                 'tokens', txn.id)
        else:
            _create_notification(txn.user_id, 'payment',
                                 '❌ Payment could not be verified',
                                 'Please contact support if you believe this is an error.',
                                 'tokens', txn.id)

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/uploads/receipts/<path:filename>')
def receipt_file(filename):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user = User.query.get(session['user_id'])
    if not user or user.username != ADMIN_USERNAME:
        return jsonify({'error': 'Forbidden'}), 403
    return send_from_directory(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads', 'receipts'), filename)



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=app.debug)