from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from models import db, User, Quiz, QuizResult, Connection, UserTag, Message, ChatGroup, ChatGroupMember, GroupMessage, BrainstormSession, BrainstormNote, GroupJoinRequest, Poll, PollOption, PollVote, GeneratedQuestion, ConvertedFile
import hashlib
import os
import subprocess
from dotenv import load_dotenv
import google.generativeai as genai
from flask_mail import Mail, Message as MailMessage
import random
import PyPDF2
import io
import json
import textwrap
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

# ── Database ──────────────────────────────────────────────────────────────────
if os.getenv('RENDER') and os.getenv('DATABASE_URL'):
    db_url = os.getenv('DATABASE_URL')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    if '?' in db_url:
        db_url = db_url.split('?')[0]
    db_url += '?sslmode=require'
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    try:
        if '@' in db_url:
            host = db_url.split('@')[1].split('/')[0]
            print(f"Using PostgreSQL: {host}")
        else:
            print("Using PostgreSQL (URL format)")
    except (IndexError, AttributeError):
        print("Using PostgreSQL")
else:
    base_dir      = os.path.dirname(os.path.abspath(__file__))
    instance_path = os.path.join(base_dir, 'instance')
    os.makedirs(instance_path, exist_ok=True)
    db_path = os.path.join(instance_path, 'knowitnow.db').replace('\\', '/')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    print(f"Using SQLite at {db_path}")

app.secret_key = os.getenv('SECRET_KEY', 'knowitnow_super_secret_key_change_in_production')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER']         = 'uploads'
# FIX: unified profile upload path (was 'uploads/profiles' in config but makedirs used 'static/uploads/profiles')
app.config['PROFILE_UPLOAD_FOLDER'] = 'uploads/profiles'
app.config['MAX_CONTENT_LENGTH']    = 10 * 1024 * 1024  # 10 MB

# ── Email ─────────────────────────────────────────────────────────────────────
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587      # ← CHANGED: 587 (TLS)
app.config['MAIL_USE_TLS'] = True  # ← ADDED: TLS
app.config['MAIL_USE_SSL'] = False # ← CHANGED: False
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')

db.init_app(app)
mail = Mail(app)

with app.app_context():
    db.create_all()

# ── Google AI ─────────────────────────────────────────────────────────────────
genai.configure(api_key=os.getenv('GOOGLE_AI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

# ── Folders ───────────────────────────────────────────────────────────────────
# FIX: makedirs now matches PROFILE_UPLOAD_FOLDER config above
os.makedirs('uploads/profiles', exist_ok=True)
os.makedirs('uploads', exist_ok=True)
os.makedirs('uploads/converted_audio', exist_ok=True)
os.makedirs('uploads/converted_video', exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'png', 'jpg', 'jpeg', 'gif'}


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
    except ImportError:
        return "PDF library not available"
    except Exception as e:
        logger.warning(f"PDF extraction failed: {str(e)}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
#  AUDIO / VIDEO HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _find_font(size=28):
    """Return a PIL ImageFont, falling back to the built-in default."""
    from PIL import ImageFont
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_frame(mouth_open, bg_color, text_color, chunk_text, width=1280, height=720):
    import numpy as np
    from PIL import Image, ImageDraw

    img  = Image.new("RGB", (width, height), color=bg_color)
    draw = ImageDraw.Draw(img)

    hx, hy, hr = width // 4, height // 2, 90

    draw.ellipse(
        [hx - 50, hy + hr - 10, hx + 50, hy + hr + 120],
        fill=(200, 160, 120), outline=text_color, width=2
    )
    draw.ellipse(
        [hx - hr, hy - hr, hx + hr, hy + hr],
        fill=(255, 210, 160), outline=text_color, width=3
    )
    draw.arc([hx - hr, hy - hr, hx + hr, hy], start=180, end=0, fill=(80, 50, 30), width=8)
    for ex in [hx - 32, hx + 32]:
        draw.ellipse([ex - 10, hy - 40, ex + 10, hy - 20], fill="white")
        draw.ellipse([ex -  6, hy - 37, ex +  6, hy - 24], fill=(60, 60, 80))
        draw.ellipse([ex -  3, hy - 35, ex +  3, hy - 29], fill="white")
    for ex in [hx - 32, hx + 32]:
        draw.line([(ex - 10, hy - 47), (ex + 10, hy - 45)], fill=(80, 50, 30), width=3)
    draw.polygon([(hx, hy - 8), (hx - 6, hy + 8), (hx + 6, hy + 8)], fill=(240, 180, 140))
    my = hy + 48
    if mouth_open:
        draw.ellipse([hx - 22, my - 8, hx + 22, my + 18], fill=(140, 60, 60))
        draw.rectangle([hx - 18, my - 5, hx + 18, my + 2], fill="white")
    else:
        draw.arc([hx - 22, my - 8, hx + 22, my + 10], start=0, end=180, fill=(140, 60, 60), width=3)

    font_large = _find_font(32)
    font_small = _find_font(24)

    text_x = width // 2 + 20
    lines  = textwrap.wrap(chunk_text, width=38)[:7]
    text_y = height // 2 - (len(lines) * 42) // 2

    for line in lines:
        try:
            bbox   = draw.textbbox((0, 0), line, font=font_large)
            text_w = bbox[2] - bbox[0]
        except Exception:
            text_w = len(line) * 18
        draw.text((text_x, text_y), line, fill=text_color, font=font_large)
        text_y += 44

    draw.rectangle([0, height - 44, width, height], fill=(0, 0, 0))
    footer = "Brainspark AI · Learning Made Visual"
    try:
        fb = draw.textbbox((0, 0), footer, font=font_small)
        fw = fb[2] - fb[0]
    except Exception:
        fw = len(footer) * 13
    draw.text(((width - fw) // 2, height - 32), footer, fill=(180, 180, 180), font=font_small)

    return np.array(img)


def _tts_chunk(text, wav_path):
    script = (
        "import pyttsx3\n"
        "engine = pyttsx3.init()\n"
        "engine.setProperty('rate', 150)\n"
        f"engine.save_to_file({repr(text)}, {repr(wav_path)})\n"
        "engine.runAndWait()\n"
        "engine.stop()\n"
    )
    try:
        result = subprocess.run(
            ["python3", "-c", script],
            timeout=60,
            capture_output=True
        )
        return (
            result.returncode == 0
            and os.path.exists(wav_path)
            and os.path.getsize(wav_path) > 0
        )
    except Exception as e:
        logger.warning(f"_tts_chunk subprocess error: {e}")
        return False


def create_audio_from_text(text_content, audio_path, language, speed):
    try:
        import pyttsx3
    except ImportError:
        raise RuntimeError("pyttsx3 is not installed. Run: pip install pyttsx3")

    try:
        speed_multiplier = float(speed) if speed else 1.0
        engine = pyttsx3.init()
        engine.setProperty('rate', int(150 * speed_multiplier))

        limited_text = ' '.join(text_content.split()[:1000])
        wav_path = audio_path.replace('.mp3', '.wav')

        engine.save_to_file(limited_text, wav_path)
        engine.runAndWait()
        engine.stop()

        try:
            from pydub import AudioSegment
            AudioSegment.from_wav(wav_path).export(audio_path, format='mp3')
            if os.path.exists(wav_path):
                os.remove(wav_path)
        except Exception:
            if os.path.exists(wav_path):
                os.rename(wav_path, audio_path)

        logger.info(f"Audio created: {audio_path}")
        return True

    except Exception as e:
        logger.error(f"create_audio_from_text error: {e}", exc_info=True)
        raise


def create_video_from_text(text_content, video_path, style, duration_setting):
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        raise RuntimeError("Pillow and numpy are required. Run: pip install Pillow numpy")

    try:
        from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips
    except ImportError:
        raise RuntimeError("moviepy is required. Run: pip install moviepy")

    style_map = {
        'slides':     ((10,  30,  100), (255, 255, 255)),
        'animated':   ((20,  20,   40), (255, 255, 255)),
        'whiteboard': ((245, 245, 240), (30,  30,   30)),
    }
    bg_color, text_color = style_map.get(style, style_map['slides'])

    chunk_limits  = {'short': 3, 'medium': 6, 'long': 10}
    fallback_secs = {'short': 4, 'medium': 5, 'long':  7}
    max_chunks    = chunk_limits.get(duration_setting, 6)
    fallback_dur  = fallback_secs.get(duration_setting, 5)

    raw_sentences = [s.strip() for s in text_content[:4000].replace('\n', ' ').split('.') if s.strip()]
    chunks, current = [], ""
    for sentence in raw_sentences:
        if len(current) + len(sentence) < 280:
            current += sentence + ". "
        else:
            if current.strip():
                chunks.append(current.strip())
            current = sentence + ". "
    if current.strip():
        chunks.append(current.strip())
    chunks = chunks[:max_chunks] or [text_content[:400]]

    audio_dir = 'uploads/converted_audio'
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(video_path)), exist_ok=True)

    clips            = []
    temp_audio_paths = []

    for idx, chunk in enumerate(chunks):
        logger.info(f"Video chunk {idx + 1}/{len(chunks)}: {chunk[:60]}…")

        clip_duration = fallback_dur
        audio_clip    = None
        wav_path      = os.path.join(audio_dir, f"chunk_{idx}_{os.getpid()}.wav")

        try:
            if _tts_chunk(chunk, wav_path):
                audio_clip    = AudioFileClip(wav_path)
                clip_duration = max(audio_clip.duration, 2.0)
                temp_audio_paths.append((wav_path, audio_clip))
            else:
                logger.warning(f"TTS silent for chunk {idx}, using fallback duration")
        except Exception as tts_err:
            logger.warning(f"TTS error chunk {idx}: {tts_err}")

        fps       = 2
        n_frames  = max(2, int(clip_duration * fps))
        frame_dur = clip_duration / n_frames

        frame_clips = []
        for fi in range(n_frames):
            mouth_open = (fi % 2 == 0) if audio_clip else False
            frame_arr  = _draw_frame(mouth_open, bg_color, text_color, chunk)
            frame_clips.append(ImageClip(frame_arr).set_duration(frame_dur))

        chunk_video = concatenate_videoclips(frame_clips, method="chain")

        if audio_clip is not None:
            if audio_clip.duration > chunk_video.duration:
                audio_clip = audio_clip.subclip(0, chunk_video.duration)
            chunk_video = chunk_video.set_audio(audio_clip)

        clips.append(chunk_video)

    if not clips:
        blank = _draw_frame(False, bg_color, text_color, text_content[:300])
        clips = [ImageClip(blank).set_duration(fallback_dur)]

    final = concatenate_videoclips(clips, method="chain")
    final.write_videofile(
        video_path,
        fps=24,
        codec='libx264',
        audio_codec='aac',
        temp_audiofile=video_path + ".temp_audio.m4a",
        remove_temp=True,
        verbose=False,
        logger=None,
        preset='ultrafast',
    )
    final.close()

    for path, ac in temp_audio_paths:
        try:
            ac.close()
        except Exception:
            pass
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    logger.info(f"Video created: {video_path}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    theme = session.get('theme', 'light')
    return render_template('index.html', theme=theme)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        username    = request.form.get('username', '').strip()
        email       = request.form.get('email', '').strip().lower()
        school      = request.form.get('school', '')
        profession  = request.form.get('profession', '')
        study_level = request.form.get('study_level', '')
        country     = request.form.get('country', '')
        password    = request.form.get('password', '')

        # ── Validate required fields ──────────────────────────────────────────
        if not all([name, username, email, study_level, country, password]):
            return jsonify({'success': False, 'error': 'All required fields must be filled in.'})

        if User.query.filter_by(username=username).first():
            return jsonify({'success': False, 'error': 'Username already taken.'})
        if User.query.filter_by(email=email).first():
            return jsonify({'success': False, 'error': 'Email already registered.'})

        # ── Create user but don't commit yet ──────────────────────────────────
        user = User(
            name=name, username=username, email=email,
            school=school, profession=profession,
            study_level=study_level, country=country
        )
        user.set_password(password)

        # Generate verification code before any DB write
        code = ''.join(random.choices('0123456789', k=6))
        user.verification_code = code

        # ── Try sending the email BEFORE committing the user ──────────────────
        # This ensures we don't save a user we can't email
        msg = MailMessage(
            'Brainspark - Verify Your Email',
            recipients=[email]
        )
        msg.body = (
            f"Your Brainspark verification code is:\n\n"
            f"{code}\n\n"
            f"Enter this code on the signup page to verify your account. "
            f"Code expires in 15 minutes.\n\n"
            f"Best,\nBrainspark Team"
        )
        try:
            mail.send(msg)
        except Exception as e:
            logger.error(f"Email send failed: {str(e)}", exc_info=True)
            return jsonify({'success': False, 'error': f'Could not send verification email. Please check your email address and try again. ({str(e)})'})

        # ── Email sent OK — now save the user ─────────────────────────────────
        try:
            db.session.add(user)
            db.session.flush()  # get user.id for profile pic filename

            # Handle optional profile picture
            if 'profile_pic' in request.files and request.files['profile_pic'].filename:
                file = request.files['profile_pic']
                if file and allowed_file(file.filename):
                    ext      = file.filename.rsplit('.', 1)[1].lower()
                    filename = f"{user.id}.{ext}"
                    filepath = os.path.join(app.config['PROFILE_UPLOAD_FOLDER'], filename)
                    try:
                        file.save(filepath)
                        user.profile_pic = filename
                    except Exception as pic_err:
                        logger.warning(f"Profile pic save failed (non-fatal): {pic_err}")

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"User save failed after email sent: {str(e)}", exc_info=True)
            return jsonify({'success': False, 'error': 'Account creation failed. Please try again.'})

        logger.info(f"New user created and verification email sent: {email}")
        return jsonify({'success': True, 'email': email, 'user_id': user.id})

    theme = session.get('theme', 'light')
    return render_template('signup.html', theme=theme)


@app.route('/verify', methods=['POST'])
def verify():
    data  = request.json
    code  = data.get('code', '').strip()
    email = data.get('email', '').strip().lower()

    if not code or not email:
        return jsonify({'success': False, 'error': 'Code and email are required.'}), 400

    user = User.query.filter(
        db.func.lower(User.email) == email,
        User.verification_code == code,
        User.is_verified == False
    ).first()

    if not user:
        existing_verified = User.query.filter(
            db.func.lower(User.email) == email, User.is_verified == True
        ).first()
        if existing_verified:
            return jsonify({'success': False, 'error': 'This email is already verified. Please log in.'})
        existing_user = User.query.filter(
            db.func.lower(User.email) == email, User.is_verified == False
        ).first()
        if existing_user:
            return jsonify({'success': False, 'error': 'Invalid code. Please check your email and try again, or request a new code.'})
        return jsonify({'success': False, 'error': 'Invalid or expired code. Please request a new one.'})

    user.is_verified       = True
    user.verification_code = None
    db.session.commit()
    return jsonify({'success': True, 'message': 'Account verified! Redirecting to login...', 'redirect': '/login'})


@app.route('/verify-email')
def verify_email_page():
    email = request.args.get('email', '')
    theme = session.get('theme', 'light')
    return render_template('verify_email.html', email=email, theme=theme)


@app.route('/resend-verification', methods=['POST'])
def resend_verification():
    data  = request.json
    email = data.get('email', '').strip().lower()
    if not email:
        return jsonify({'success': False, 'error': 'Email required'}), 400
    user = User.query.filter(
        db.func.lower(User.email) == email,
        User.is_verified == False
    ).first()
    if not user:
        return jsonify({'success': False, 'error': 'User not found or already verified'}), 404
    code = ''.join(random.choices('0123456789', k=6))
    user.verification_code = code
    db.session.commit()
    msg      = MailMessage('Brainspark - Verify Your Email', recipients=[email])
    msg.body = (
        f"Your new Brainspark verification code is:\n\n"
        f"{code}\n\n"
        f"Code expires in 15 minutes.\n\n"
        f"Best,\nBrainspark Team"
    )
    try:
        mail.send(msg)
        return jsonify({'success': True, 'message': 'Code resent successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Email send failed: {str(e)}'}), 500


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user     = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            if not user.is_verified:
                return jsonify({'success': False, 'error': 'Please verify your email first'})
            session['user_id']  = user.id
            session['username'] = user.username
            return jsonify({'success': True, 'message': 'Login successful!', 'redirect': '/dashboard'})
        else:
            return jsonify({'success': False, 'error': 'Invalid username or password'})

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
        msg  = MailMessage(
            subject=f"Brainspark Contact: {data.get('name', 'No Name')}",
            recipients=[os.getenv('MAIL_USERNAME')],
            body=f"Name: {data.get('name')}\nEmail: {data.get('email')}\nMessage: {data.get('message')}",
            sender=os.getenv('MAIL_USERNAME')
        )
        mail.send(msg)
        return jsonify({'message': 'Message sent successfully!'})
    except Exception as e:
        return jsonify({'message': f'Error: {str(e)}'}), 500


# ── Upload Notes (PDF → Topics) ───────────────────────────────────────────────
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
        filepath = os.path.join(
            app.config['UPLOAD_FOLDER'],
            f"temp_{int(datetime.now().timestamp())}_{filename}"
        )
        file.save(filepath)
        text = extract_pdf_text_simple(filepath)
        if os.path.exists(filepath):
            os.remove(filepath)

        if not text or len(text.strip()) < 50:
            return jsonify({
                'success': False,
                'error': 'No readable text found in PDF. Try a text-based PDF (not scanned images).'
            }), 400

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

        logger.info(f"Stored PDF text and {len(topics)} topics in session")
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

        existing_rows = GeneratedQuestion.query.filter_by(
            user_id=session['user_id'], source_hash=pdf_source_hash
        ).all()
        existing_question_texts = {str(r.question_text).strip().lower() for r in existing_rows if r.question_text}

        if selected_topics == 'all' or not selected_topics:
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
            topics_str = (', '.join(selected_topics) if isinstance(selected_topics, list)
                          else str(selected_topics)) if selected_topics != 'all' else 'all topics'
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
            return jsonify({
                'success': False,
                'error': f'Could only generate {len(unique_new)} unique questions. Try generating again or upload a longer PDF.'
            }), 400

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

        logger.info(f"Generated {len(unique_new)} unique questions")
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

        recent_results = QuizResult.query.filter_by(user_id=user_id).order_by(
            QuizResult.completed_at.desc()
        ).limit(5).all()

        recent_activity = [{
            'quiz_title':   r.quiz.title,
            'score':        r.score,
            'completed_at': r.completed_at.strftime('%Y-%m-%d %H:%M:%S'),
            'time_ago':     get_time_ago(r.completed_at)
        } for r in recent_results]

        daily_scores = {}
        for i in range(7):
            day       = datetime.utcnow() - timedelta(days=6 - i)
            day_start = day.replace(hour=0,  minute=0,  second=0,  microsecond=0)
            day_end   = day.replace(hour=23, minute=59, second=59, microsecond=999999)
            day_results = QuizResult.query.filter(
                QuizResult.user_id      == user_id,
                QuizResult.completed_at >= day_start,
                QuizResult.completed_at <= day_end
            ).all()
            daily_scores[day.strftime('%a')] = (
                round(sum(r.score for r in day_results) / len(day_results)) if day_results else 0
            )

        return jsonify({
            'success': True,
            'stats': {
                'total_quizzes':    total_quizzes,
                'average_score':    average_score,
                'connection_count': connection_count
            },
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


# ══════════════════════════════════════════════════════════════════════════════
#  STUDY BUDDIES & CONNECTIONS
# ══════════════════════════════════════════════════════════════════════════════

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
        query = query.filter(
            (User.name.ilike(f'%{search_query}%')) |
            (User.username.ilike(f'%{search_query}%'))
        )

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
            'id':            buddy.id,
            'name':          buddy.name,
            'username':      buddy.username,
            'profile_pic':   buddy.get_profile_pic_url(),
            'school':        buddy.school,
            'study_level':   buddy.study_level,
            'country':       buddy.country,
            'tags':          [t.tag for t in buddy.tags],
            'total_quizzes': buddy.get_total_quizzes(),
            'average_score': buddy.get_average_score(),
            'is_connected':  is_connected,
            'priority':      priority
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
        (Message.sender_id == buddy_id) &
        (Message.receiver_id == user_id) &
        (Message.is_read == False)
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
            (Message.sender_id == buddy.id) &
            (Message.receiver_id == user_id) &
            (Message.is_read == False)
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
        {'id': 1, 'title': 'Understanding Calculus Derivatives',
         'description': "Let's discuss how derivatives work", 'members': 12, 'messages': 45},
        {'id': 2, 'title': 'Physics Mechanics Help',
         'description': "Need help with Newton's laws", 'members': 8, 'messages': 23},
        {'id': 3, 'title': 'Chemistry Reactions',
         'description': 'Balancing equations and understanding reactions', 'members': 15, 'messages': 67}
    ]
    return jsonify({'success': True, 'discussions': discussions})


@app.route('/api/ask-ai', methods=['POST'])
def ask_ai():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if request.content_type and 'multipart/form-data' in request.content_type:
        question           = request.form.get('question', '')
        reset_conversation = request.form.get('reset', False)
    else:
        data               = request.json or {}
        question           = data.get('question', '')
        reset_conversation = data.get('reset', False)
    if not question:
        return jsonify({'error': 'Please provide a question'}), 400
    try:
        if reset_conversation or 'ai_conversation' not in session:
            conversation_history       = []
            session['ai_conversation'] = []
        else:
            conversation_history = session.get('ai_conversation', [])

        context = ""
        if conversation_history:
            context = "Previous conversation:\n"
            for i, ex in enumerate(conversation_history, 1):
                context += f"\nQ{i}: {ex['question']}\nA{i}: {ex['answer']}\n"
            context += "\n---\n\n"

        pdf_text = None
        if 'pdf' in request.files:
            pdf_file = request.files['pdf']
            if pdf_file and pdf_file.filename and allowed_file(pdf_file.filename):
                pdf_text = extract_pdf_text(pdf_file)

        if pdf_text:
            prompt = f"""{context}PDF CONTENT:\n{pdf_text[:4000]}\n\nAnswer concisely:\n{question}"""
        else:
            prompt = f"""{context}Answer concisely:\n{question}"""

        response    = model.generate_content(prompt)
        explanation = response.text

        conversation_history.append({'question': question, 'answer': explanation})
        session['ai_conversation'] = conversation_history
        session.modified = True

        return jsonify({'success': True, 'explanation': explanation,
                        'conversation_count': len(conversation_history),
                        'pdf_processed': pdf_text is not None})
    except Exception as e:
        logger.error(f'Error in ask-ai: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error: {str(e)}'}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  GROUPS
# ══════════════════════════════════════════════════════════════════════════════

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
        poll_msg = GroupMessage(
            group_id=group_id, sender_id=user_id,
            content=f"📊 Poll: {question}", message_type='poll', poll_id=poll.id
        )
        db.session.add(poll_msg)
        db.session.commit()
        return jsonify({'success': True, 'poll_id': poll.id, 'message_id': poll_msg.id,
                        'message': 'Poll created successfully!'})
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
                    response   = model.generate_content([prompt, image])
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
        return jsonify({'success': True, 'session_id': sess_obj.id,
                        'message': f'Session scheduled! Notified {len(group_members)} members'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-group-sessions/<int:group_id>')
def get_group_sessions(group_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    sessions = BrainstormSession.query.filter_by(group_id=group_id).order_by(
        BrainstormSession.scheduled_time.desc()).all()
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


@app.route('/api/upload-brainstorm-image', methods=['POST'])
def upload_brainstorm_image():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if 'image' not in request.files:
        return jsonify({'error': 'No image file'}), 400
    f = request.files['image']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400
    if f and allowed_file(f.filename):
        try:
            os.makedirs('uploads/brainstorm', exist_ok=True)
            ts       = datetime.utcnow().timestamp()
            ext      = f.filename.rsplit('.', 1)[1].lower()
            filename = f"brainstorm_{session['user_id']}_{ts}.{ext}"
            f.save(os.path.join('uploads/brainstorm', filename))
            return jsonify({'success': True, 'filename': filename,
                            'url': f'/uploads/brainstorm/{filename}'})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'Invalid file type'}), 400


@app.route('/api/add-brainstorm-note-rich', methods=['POST'])
def add_brainstorm_note_rich():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id      = session['user_id']
    data         = request.json
    session_id   = data.get('session_id')
    content      = data.get('content', '').strip()
    mentions     = data.get('mentions', [])
    tags         = data.get('tags', [])
    mention_ai   = data.get('mention_ai', False)
    image_path   = data.get('image_path')
    solved_prob  = data.get('solved_problem')
    textbook_ref = data.get('textbook_ref')
    if not session_id or not content:
        return jsonify({'error': 'Session and content required'}), 400
    sess_obj = BrainstormSession.query.get(session_id)
    if not sess_obj:
        return jsonify({'error': 'Session not found'}), 404
    if not ChatGroupMember.query.filter_by(group_id=sess_obj.group_id, user_id=user_id).first():
        return jsonify({'error': 'Not a member of this group'}), 403
    try:
        has_media = bool(image_path or textbook_ref or solved_prob)
        note = BrainstormNote(
            session_id=session_id, user_id=user_id, content=content,
            mentions=json.dumps(mentions) if mentions else None,
            tags=json.dumps(tags) if tags else None,
            mention_ai=mention_ai, image_path=image_path,
            textbook_path=textbook_ref, solved_problem=solved_prob, has_media=has_media
        )
        db.session.add(note)
        db.session.commit()
        ai_response = None
        if mention_ai:
            try:
                resp        = model.generate_content(
                    f"Brainstorm help requested:\nContent: {content}\nTags: {', '.join(tags)}\nBe brief (2-3 sentences).")
                ai_response = resp.text
                db.session.add(BrainstormNote(
                    session_id=session_id, user_id=1,
                    content=f"AI Assistant: {ai_response}", has_media=False
                ))
                db.session.commit()
            except Exception as e:
                logger.error(f'AI response error: {e}')
        return jsonify({'success': True, 'note': {
            'id': note.id, 'user_name': note.user.name, 'content': content,
            'mentions': mentions, 'tags': tags, 'mention_ai': mention_ai,
            'has_media': has_media, 'created_at': note.created_at.isoformat()
        }, 'ai_response': ai_response})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/solve-problem-ai', methods=['POST'])
def solve_problem_ai():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data    = request.json
    problem = data.get('problem', '')
    context = data.get('context', '')
    if not problem:
        return jsonify({'error': 'Problem description required'}), 400
    try:
        response = model.generate_content(
            f"Help solve this problem:\n{problem}\nContext: {context or 'None'}\nProvide a clear step-by-step solution.")
        return jsonify({'success': True, 'solution': response.text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/brainstorm-ai-suggestions', methods=['POST'])
def brainstorm_ai_suggestions():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data          = request.json
    topic         = data.get('topic', '')
    current_ideas = data.get('current_ideas', '')
    if not topic:
        return jsonify({'error': 'Topic required'}), 400
    try:
        response = model.generate_content(
            f"Study group brainstorming:\nTopic: {topic}\nCurrent ideas: {current_ideas or 'Just starting'}\nGenerate 3-4 creative ideas or questions.")
        return jsonify({'success': True, 'suggestions': response.text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/accept-join-request', methods=['POST'])
def accept_join_request():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id      = session['user_id']
    request_id   = request.json.get('request_id')
    join_request = GroupJoinRequest.query.get(request_id)
    if not join_request:
        return jsonify({'error': 'Request not found'}), 404
    admin_member = ChatGroupMember.query.filter_by(
        group_id=join_request.group_id, user_id=user_id).first()
    if not admin_member or admin_member.role != 'admin':
        return jsonify({'error': 'Only admins can accept requests'}), 403
    try:
        db.session.add(ChatGroupMember(
            group_id=join_request.group_id, user_id=join_request.user_id, role='member'))
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
    admin_member = ChatGroupMember.query.filter_by(
        group_id=join_request.group_id, user_id=user_id).first()
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
    admin_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(
        user_id=user_id, role='admin').all()]
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
    reqs = GroupJoinRequest.query.filter_by(user_id=session['user_id']).order_by(
        GroupJoinRequest.created_at.desc()).all()
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
    unread_messages       = Message.query.filter_by(receiver_id=user_id, is_read=False).order_by(
        Message.created_at.desc()).limit(5).all()
    messages_data = [{
        'id': m.id, 'type': 'message', 'sender_id': m.sender_id,
        'sender_name': m.sender.name, 'sender_pic': m.sender.get_profile_pic_url(),
        'content': (m.content[:50] + '...') if len(m.content) > 50 else m.content,
        'created_at': m.created_at.isoformat()
    } for m in unread_messages]
    admin_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(
        user_id=user_id, role='admin').all()]
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
        return jsonify({'success': True, 'message': 'Quiz result saved successfully!',
                        'result_id': result.id}), 200
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
    return send_from_directory('uploads', filename)


# ══════════════════════════════════════════════════════════════════════════════
#  AUDIO / VIDEO CONVERSION ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/convert-to-audio', methods=['POST'])
def convert_to_audio():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    try:
        file = None
        for field in ('file', 'pdf', 'audio_file', 'notes'):
            if field in request.files and request.files[field].filename:
                file = request.files[field]
                break
        if file is None:
            return jsonify({'success': False, 'error': 'No file provided. Please select a PDF or text file.'}), 400

        language = request.form.get('language', 'en')
        speed    = request.form.get('speed', '1')

        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if file_ext == 'pdf':
            text_content = extract_pdf_text(file)
        elif file_ext in ('txt', 'doc', 'docx'):
            text_content = file.read().decode('utf-8', errors='ignore')
        else:
            return jsonify({'success': False, 'error': 'Unsupported format. Use PDF or TXT.'}), 400

        if not text_content or len(text_content) < 10:
            return jsonify({'success': False, 'error': 'Could not extract text from file'}), 400

        import uuid
        audio_filename = f"audio_{uuid.uuid4().hex}.mp3"
        audio_path     = f"uploads/converted_audio/{audio_filename}"

        create_audio_from_text(text_content, audio_path, language, speed)

        if not os.path.exists(audio_path):
            wav_path = audio_path.replace('.mp3', '.wav')
            if os.path.exists(wav_path):
                audio_path     = wav_path
                audio_filename = audio_filename.replace('.mp3', '.wav')

        word_count       = len(text_content.split())
        speed_multiplier = float(speed) if speed else 1.0
        duration_minutes = max(1, int((word_count // 150) / speed_multiplier))

        converted_file = ConvertedFile(
            user_id=user_id,
            original_filename=file.filename,
            converted_filename=audio_filename,
            file_type='audio',
            file_path=audio_path,
            file_size=os.path.getsize(audio_path) if os.path.exists(audio_path) else 0,
            duration=f"{duration_minutes}:00",
            conversion_settings=json.dumps({'language': language, 'speed': speed})
        )
        db.session.add(converted_file)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Audio generated successfully!',
                        'file_id': converted_file.id, 'filename': audio_filename})
    except Exception as e:
        logger.error(f'Audio conversion error: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/convert-to-video', methods=['POST'])
def convert_to_video():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    try:
        file = None
        for field in ('file', 'pdf', 'video_file', 'notes'):
            if field in request.files and request.files[field].filename:
                file = request.files[field]
                break
        if file is None:
            return jsonify({'success': False, 'error': 'No file provided. Please select a PDF or text file.'}), 400

        style    = request.form.get('style', 'slides')
        duration = request.form.get('duration', 'medium')

        file_ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else ''
        if file_ext == 'pdf':
            text_content = extract_pdf_text(file)
        elif file_ext in ('txt', 'doc', 'docx'):
            text_content = file.read().decode('utf-8', errors='ignore')
        else:
            return jsonify({'success': False, 'error': 'Unsupported format. Use PDF or TXT.'}), 400

        if not text_content or len(text_content) < 10:
            return jsonify({'success': False, 'error': 'Could not extract text from file'}), 400

        import uuid
        video_filename = f"video_{uuid.uuid4().hex}.mp4"
        video_path     = f"uploads/converted_video/{video_filename}"

        create_video_from_text(text_content, video_path, style, duration)

        duration_map = {'short': '5:00', 'medium': '15:00', 'long': '25:00'}
        converted_file = ConvertedFile(
            user_id=user_id,
            original_filename=file.filename,
            converted_filename=video_filename,
            file_type='video',
            file_path=video_path,
            file_size=os.path.getsize(video_path) if os.path.exists(video_path) else 0,
            duration=duration_map.get(duration, '15:00'),
            conversion_settings=json.dumps({'style': style, 'duration': duration})
        )
        db.session.add(converted_file)
        db.session.commit()

        return jsonify({'success': True, 'message': 'Video generated successfully!',
                        'file_id': converted_file.id, 'filename': video_filename})
    except Exception as e:
        logger.error(f'Video conversion error: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/converted-files')
def get_converted_files():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        files = ConvertedFile.query.filter_by(user_id=session['user_id']).order_by(
            ConvertedFile.created_at.desc()).all()
        return jsonify({'success': True, 'files': [{
            'id': f.id, 'filename': f.converted_filename, 'type': f.file_type,
            'created_at': f.created_at.strftime('%b %d, %Y'), 'duration': f.duration,
            'size': round(f.file_size / 1024 / 1024, 2) if f.file_size else 0
        } for f in files]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/delete-converted-file/<int:file_id>', methods=['DELETE'])
def delete_converted_file(file_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        f = ConvertedFile.query.filter_by(id=file_id, user_id=session['user_id']).first()
        if not f:
            return jsonify({'success': False, 'error': 'File not found'}), 404
        if os.path.exists(f.file_path):
            os.remove(f.file_path)
        db.session.delete(f)
        db.session.commit()
        return jsonify({'success': True, 'message': 'File deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/download-file/<int:file_id>')
def download_file(file_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        f = ConvertedFile.query.filter_by(id=file_id, user_id=session['user_id']).first()
        if not f:
            return jsonify({'error': 'File not found'}), 404
        if not os.path.exists(f.file_path):
            return jsonify({'error': 'File does not exist on server'}), 404
        return send_from_directory(
            os.path.dirname(os.path.abspath(f.file_path)),
            os.path.basename(f.file_path),
            as_attachment=True
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/preview-file/<int:file_id>')
def preview_file(file_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        f = ConvertedFile.query.filter_by(id=file_id, user_id=session['user_id']).first()
        if not f:
            return jsonify({'success': False, 'error': 'File not found'}), 404
        return jsonify({
            'success':     True,
            'type':        f.file_type,
            'preview_url': f'/api/download-file/{file_id}',
            'duration':    f.duration,
            'filename':    f.converted_filename
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=app.debug)