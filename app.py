from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from models import db, User, Quiz, QuizResult, Connection, UserTag, Message, ChatGroup, ChatGroupMember, GroupMessage, BrainstormSession, BrainstormNote, GroupJoinRequest, Poll, PollOption, PollVote, GeneratedQuestion
import hashlib
import os
from dotenv import load_dotenv
import google.generativeai as genai
from flask_mail import Mail, Message as MailMessage
import random
import PyPDF2
import io
import json
from datetime import datetime, timedelta
import logging

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Database configuration: PostgreSQL on Render, SQLite locally
if os.getenv('DATABASE_URL'):
    # Render PostgreSQL - CRITICAL: Remove query params for connection pooling
    db_url = os.getenv('DATABASE_URL')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    
    # Remove ?sslmode=require if present (Render handles SSL automatically)
    if '?' in db_url:
        db_url = db_url.split('?')[0]
    
    # IMPORTANT: For Render, add SSL settings
    db_url += '?sslmode=require'
    
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    print(f"Using PostgreSQL: {db_url.split('@')[1].split('/')[0]}")  # Don't log full URL
else:
    # Local SQLite
    os.makedirs('instance', exist_ok=True)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///instance/knowitnow.db'


# Secure secret key from environment
app.secret_key = os.getenv('SECRET_KEY', 'knowitnow_super_secret_key_change_in_production')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'  # For PDF uploads
app.config['PROFILE_UPLOAD_FOLDER'] = 'uploads/profiles'  # For profile pictures
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB


# Email config BEFORE Mail initialization
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')

logger.info(f"Mail config - Server: {app.config['MAIL_SERVER']}, Username: {app.config['MAIL_USERNAME']}")

db.init_app(app)
mail = Mail(app)

with app.app_context():
    db.create_all()

# Google AI config (Gemini 2.5 Flash - Latest)
genai.configure(api_key=os.getenv('GOOGLE_AI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

# Allowed files
ALLOWED_EXTENSIONS = {'pdf'}
os.makedirs('static/uploads/profiles', exist_ok=True)
os.makedirs('uploads', exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'png', 'jpg', 'jpeg', 'gif'}


def extract_pdf_text(file_storage):
    """Extract text from a PDF file (FileStorage object). Returns text or None."""
    try:
        pdf_bytes = file_storage.read()
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
        
        file_storage.seek(0)  # Reset for potential re-read
        return text.strip() if text.strip() else None
    except Exception as e:
        logger.error(f'Error extracting PDF text: {str(e)}', exc_info=True)
        return None


@app.route('/')
def index():
    theme = session.get('theme', 'light')
    return render_template('index.html', theme=theme)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        username = request.form['username']
        email = request.form['email'].strip().lower()
        school = request.form.get('school', '')
        profession = request.form.get('profession', '')
        study_level = request.form['study_level']
        country = request.form['country']
        password = request.form['password']
        
        # Check existing
        if User.query.filter_by(username=username).first():
            return jsonify({'error': 'Username already taken'})
        if User.query.filter_by(email=email).first():
            return jsonify({'error': 'Email already registered'})
        
        # Create user
        user = User(
            name=name, username=username, email=email,
            school=school, profession=profession, 
            study_level=study_level, country=country
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.flush()  # Get ID for filename
        
        # Profile pic
        if 'profile_pic' in request.files and request.files['profile_pic'].filename:
            file = request.files['profile_pic']
            if file and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{user.id}.{ext}"
                filepath = os.path.join(app.config['PROFILE_UPLOAD_FOLDER'], filename)
                file.save(filepath)
                print(f"Profile pic saved: {filename} at {filepath}")
                user.profile_pic = filename

        
        # Gen & send verification code
        code = ''.join(random.choices('0123456789', k=6))
        user.verification_code = code
        
        db.session.commit()
        
        msg = MailMessage('Brainspark - Verify Your Email', recipients=[email])
        msg.body = f"""
Your Brainspark verification code is:

{code}

Enter this code on the signup page to verify your account. Code expires in 15 minutes.

Questions? Contact support@Brainspark.com

Best,
Brainspark Team
        """
        try:
            mail.send(msg)
            logger.info(f"Verification email sent to {email}")
        except Exception as e:
            logger.error(f"Email send failed: {str(e)}", exc_info=True)
            db.session.rollback()
            return jsonify({'error': f'Email send failed: {str(e)}'})
        
        return jsonify({'success': True, 'email': email, 'user_id': user.id})
    
    theme = session.get('theme', 'light')
    return render_template('signup.html', theme=theme)

@app.route('/verify', methods=['POST'])
def verify():
    data = request.json
    code = data.get('code', '').strip()
    email = data.get('email', '').strip().lower()
    
    logger.info(f"Verify attempt - email: {email}, code: {code}")
    
    if not code or not email:
        return jsonify({'error': 'Code and email are required.'}), 400
    
    # Look up by email and code for accuracy (case-insensitive email)
    user = User.query.filter(
        db.func.lower(User.email) == email,
        User.verification_code == code,
        User.is_verified == False
    ).first()
    
    if not user:
        # Check if user exists but is already verified
        existing_verified = User.query.filter(
            db.func.lower(User.email) == email,
            User.is_verified == True
        ).first()
        if existing_verified:
            return jsonify({'error': 'This email is already verified. Please log in.'})
        
        # Check if user exists with different code
        existing_user = User.query.filter(
            db.func.lower(User.email) == email,
            User.is_verified == False
        ).first()
        if existing_user:
            logger.warning(f"Wrong code for {email}. Expected: {existing_user.verification_code}, Got: {code}")
            return jsonify({'error': 'Invalid code. Please check your email and try again, or request a new code.'})
        
        logger.warning(f"No unverified user found for email: {email}, code: {code}")
        return jsonify({'error': 'Invalid or expired code. Please request new one.'})
    
    user.is_verified = True
    user.verification_code = None
    db.session.commit()
    logger.info(f"User {email} verified successfully")
    
    return jsonify({'success': True, 'message': 'Account verified! Redirecting to login...', 'redirect': '/login'})

@app.route('/verify-email')
def verify_email_page():
    email = request.args.get('email', '')
    theme = session.get('theme', 'light')
    return render_template('verify_email.html', email=email, theme=theme)

@app.route('/resend-verification', methods=['POST'])
def resend_verification():
    data = request.json
    email = data.get('email')
    if not email:
        return jsonify({'error': 'Email required'}), 400
    user = User.query.filter_by(email=email, is_verified=False).first()
    if not user:
        return jsonify({'error': 'User not found or already verified'}), 404
    code = ''.join(random.choices('0123456789', k=6))
    user.verification_code = code
    db.session.commit()
    msg = MailMessage('Brainspark - Verify Your Email', recipients=[email])
    msg.body = f"""
Your new Brainspark verification code is:

{code}

Enter this code on the verification page to verify your account. Code expires in 15 minutes.

Questions? Contact support@Brainspark.com

Best,
Brainspark Team
    """
    try:
        mail.send(msg)
        logger.info(f"Verification email resent to {email}")
        return jsonify({'success': True, 'message': 'Code resent successfully'})
    except Exception as e:
        logger.error(f"Email resend failed: {str(e)}", exc_info=True)
        return jsonify({'error': f'Email send failed: {str(e)}'}), 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not user.is_verified:
                return jsonify({'error': 'Please verify your email first'})
            
            session['user_id'] = user.id
            session['username'] = user.username
            
            return jsonify({'success': True, 'message': 'Login successful!', 'redirect': '/dashboard'})
        else:
            return jsonify({'error': 'Invalid username or password'})
    
    theme = session.get('theme', 'light')
    return render_template('login.html', theme=theme)

@app.route('/toggle_mode', methods=['POST'])
def toggle_mode():
    current_theme = session.get('theme', 'light')
    new_theme = 'dark' if current_theme == 'light' else 'light'
    session['theme'] = new_theme
    return jsonify({'theme': new_theme, 'message': f'Switched to {new_theme} mode! 🌙/☀️'})

@app.route('/send_email', methods=['POST'])
def send_email():
    try:
        data = request.json
        msg = MailMessage(
            subject=f"Brainspark Contact: {data.get('name', 'No Name')}",
            recipients=[os.getenv('MAIL_USERNAME')],
            body=f"""
            New contact form submission:
            
            Name: {data.get('name', 'N/A')}
            Email: {data.get('email', 'N/A')}
            Message: {data.get('message', 'N/A')}
            
            ---
            Brainspark Team
            """,
            sender=os.getenv('MAIL_USERNAME')
        )
        mail.send(msg)
        return jsonify({'message': 'Message sent successfully! 🎉'})
    except Exception as e:
        return jsonify({'message': f'Error: {str(e)}'}), 500

def parse_generated_questions(questions_str):
    """Parse AI-generated questions from various string formats"""
    try:
        if isinstance(questions_str, dict):
            return questions_str
        if isinstance(questions_str, list):
            return {"questions": questions_str}
        
        text = questions_str.strip()
        start_idx = text.find('{')
        end_idx = text.rfind('}')
        if start_idx != -1 and end_idx != -1:
            json_str = text[start_idx:end_idx+1]
            return json.loads(json_str)
        
        return json.loads(text)
    except Exception as e:
        logger.error(f'Error parsing generated questions: {str(e)}')
        return {"questions": []}


@app.route('/upload_notes', methods=['POST'])
def upload_notes():
    """PDF upload -> Gemini questions with deduplication via DB storage"""
    if 'user_id' not in session:
        return jsonify({'error': 'Please login first'}), 401
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        try:
            # Extract text with error handling
            text = ''
            with open(filepath, 'rb') as pdf_file:
                try:
                    pdf_reader = PyPDF2.PdfReader(pdf_file)
                    
                    # Check if PDF is valid
                    if not pdf_reader.pages:
                        return jsonify({'error': 'PDF appears to be empty or invalid. Please upload a valid PDF file.'}), 400
                    
                    for page in pdf_reader.pages:
                        try:
                            extracted = page.extract_text()
                            if extracted:
                                text += extracted
                        except Exception as page_error:
                            logger.warning(f'Error extracting text from page: {str(page_error)}')
                            continue
                            
                except PyPDF2.errors.PdfReadError as pdf_error:
                    try:
                        if os.path.exists(filepath):
                            os.remove(filepath)
                    except:
                        pass
                    return jsonify({'error': f'The PDF file appears to be corrupted or invalid. Please upload a valid PDF file. Error: {str(pdf_error)[:100]}'}), 400
            
            if not text or len(text.strip()) == 0:
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except:
                    pass
                return jsonify({'error': 'Could not extract any text from the PDF. Please ensure the PDF contains readable text.'}), 400
            
            # Generate questions with Gemini
            question_type = request.form.get('type', 'objective')
            question_count = request.form.get('question_count', '10')
            hardness = request.form.get('hardness', 'medium')
            time_limit = request.form.get('time', '30')
            
            # Validate question count (max 100)
            try:
                question_count_int = int(question_count)
            except ValueError:
                question_count_int = 10
            
            if question_count_int > 100:
                question_count_int = 100
                question_count = '100'
            elif question_count_int < 1:
                question_count_int = 1
                question_count = '1'
            
            user_id = session['user_id']
            source_hash = hashlib.md5(text.encode()).hexdigest()
            
            # Fetch existing questions for this user to avoid duplicates
            existing_questions = GeneratedQuestion.query.filter_by(user_id=user_id).all()
            existing_question_texts = [q.question_text for q in existing_questions]
            
            # Build deduplication context for prompt
            dedup_context = ""
            if existing_question_texts:
                recent_existing = existing_question_texts[-50:]
                dedup_context = "\n\nCRITICAL: The following questions have ALREADY been generated for this user. You MUST NOT generate any question that is identical or very similar to these:\n"
                for i, q_text in enumerate(recent_existing, 1):
                    dedup_context += f"{i}. {q_text[:200]}\n"
                dedup_context += "\nGenerate completely NEW and DIFFERENT questions only."
            
            # Create appropriate prompt based on question type
            if question_type == 'theory':
                prompt = f"""
                From this note text: {text[:4000]}...
                
                Generate {question_count} theory/essay type questions at {hardness} difficulty.
                For each question include:
                - A clear theory/concept question that requires explanation
                - Exactly 4 answer options
                - The correct answer text (must match one of the 4 options exactly)
                - A brief explanation for why this is correct
                
                IMPORTANT: Return ONLY valid JSON, no other text.
                Format: {{"questions": [{{"question": "", "options": ["option1", "option2", "option3", "option4"], "answer": "", "explanation": ""}}]}}
                The "answer" field MUST be exactly one of the options.
                Time limit: {time_limit}s per question.
                {dedup_context}
                """
            else:  # objective
                prompt = f"""
                From this note text: {text[:4000]}...
                
                Generate {question_count} multiple choice objective questions at {hardness} difficulty.
                For each question include:
                - A clear objective question
                - Exactly 4 answer options
                - The correct answer text (must match one of the options exactly)
                - A brief explanation
                
                IMPORTANT: Return ONLY valid JSON, no other text.
                Format: {{"questions": [{{"question": "", "options": ["option1", "option2", "option3", "option4"], "answer": "", "explanation": ""}}]}}
                The "answer" field MUST be exactly one of the options.
                Time limit: {time_limit}s per question.
                {dedup_context}
                """
            
            try:
                response = model.generate_content(prompt)
                questions_text = response.text
                
                # Parse and deduplicate
                parsed_data = parse_generated_questions(questions_text)
                new_questions = parsed_data.get('questions', [])
                
                # Filter out exact duplicates already in DB
                unique_questions = []
                for q in new_questions:
                    q_text = q.get('question', '').strip()
                    if q_text and q_text not in existing_question_texts:
                        unique_questions.append(q)
                        existing_question_texts.append(q_text)
                
                # Store new unique questions in DB
                for q in unique_questions:
                    gq = GeneratedQuestion(
                        user_id=user_id,
                        question_text=q.get('question', '').strip(),
                        options=json.dumps(q.get('options', [])),
                        correct_answer=q.get('answer', '').strip(),
                        explanation=q.get('explanation', '').strip(),
                        source_hash=source_hash,
                        difficulty=hardness,
                        question_type=question_type
                    )
                    db.session.add(gq)
                db.session.commit()
                
                # Re-serialize the deduplicated questions for session storage
                final_questions = json.dumps({"questions": unique_questions})
                
                # Cleanup - safely remove file
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except:
                    pass
                
                # Store questions in session to avoid URL length issues
                session['quiz_questions'] = final_questions
                session.modified = True
                
                return jsonify({'questions': final_questions, 'success': True, 'generated_count': len(unique_questions)})
            except Exception as e:
                # Cleanup on error too
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                except:
                    pass
                logger.error(f'Error generating questions: {str(e)}', exc_info=True)
                return jsonify({'error': f'Error generating questions: {str(e)}'}), 500
        except Exception as e:
            # Cleanup on any error
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
            logger.error(f'Error processing PDF: {str(e)}', exc_info=True)
            return jsonify({'error': f'Error processing PDF file: {str(e)[:100]}'}), 500
    
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash('Please login first')
        return redirect(url_for('index'))
    
    user_id = session['user_id']
    user = User.query.get(user_id)
    theme = session.get('theme', 'light')
    return render_template('dashboard.html', user=user, theme=theme)

@app.route('/quiz')
def quiz():
    """Quiz page for taking quizzes"""
    if 'user_id' not in session:
        flash('Please login first')
        return redirect(url_for('index'))
    
    theme = session.get('theme', 'light')
    return render_template('quiz.html', theme=theme)

@app.route('/study-buddies')
def study_buddies():
    """Find study buddies page"""
    if 'user_id' not in session:
        flash('Please login first')
        return redirect(url_for('index'))
    
    user_id = session['user_id']
    user = User.query.get(user_id)
    theme = session.get('theme', 'light')
    return render_template('study-buddies.html', user=user, theme=theme)

@app.route('/api/get-quiz-questions')
def get_quiz_questions():
    """Fetch quiz questions from session"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    questions = session.get('quiz_questions')
    
    if not questions:
        return jsonify({'error': 'No quiz data found. Please upload a PDF again.'}), 400
    
    return jsonify({'success': True, 'questions': questions})

@app.route('/api/dashboard-stats')
def dashboard_stats():
    """API endpoint to fetch dashboard statistics"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    try:
        user_id = session['user_id']
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404

        logger.debug(f'Fetching stats for user {user_id}')
        
        # Get stats
        total_quizzes = user.get_total_quizzes()
        average_score = user.get_average_score()
        connection_count = user.get_connection_count()
        
        logger.debug(f'Stats - Quizzes: {total_quizzes}, Avg Score: {average_score}, Connections: {connection_count}')

        # Get recent activity (last 5 quiz results)
        recent_results = QuizResult.query.filter_by(user_id=user_id).order_by(
            QuizResult.completed_at.desc()
        ).limit(5).all()
        
        logger.debug(f'Found {len(recent_results)} recent results')

        recent_activity = []
        for result in recent_results:
            recent_activity.append({
                'quiz_title': result.quiz.title,
                'score': result.score,
                'completed_at': result.completed_at.strftime('%Y-%m-%d %H:%M:%S'),
                'time_ago': get_time_ago(result.completed_at)
            })

        # Get performance data for the last 7 days
        daily_scores = {}
        
        for i in range(7):
            day = datetime.utcnow() - timedelta(days=6-i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            day_results = QuizResult.query.filter(
                QuizResult.user_id == user_id,
                QuizResult.completed_at >= day_start,
                QuizResult.completed_at <= day_end
            ).all()
            
            if day_results:
                avg_score = sum(r.score for r in day_results) / len(day_results)
                daily_scores[day.strftime('%a')] = round(avg_score)
            else:
                daily_scores[day.strftime('%a')] = 0

        logger.debug(f'Performance data: {daily_scores}')

        return jsonify({
            'success': True,
            'stats': {
                'total_quizzes': total_quizzes,
                'average_score': average_score,
                'connection_count': connection_count
            },
            'recent_activity': recent_activity,
            'performance_data': daily_scores
        })
    except Exception as e:
        logger.error(f'Error fetching dashboard stats: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': f'Error fetching stats: {str(e)}'}), 500

def get_time_ago(dt):
    """Convert datetime to 'time ago' format"""
    now = datetime.utcnow()
    diff = now - dt
    
    seconds = diff.total_seconds()
    
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif seconds < 3600:
        return f"{int(seconds / 60)} minutes ago"
    elif seconds < 86400:
        return f"{int(seconds / 3600)} hours ago"
    elif seconds < 604800:
        return f"{int(seconds / 86400)} days ago"
    else:
        return dt.strftime('%Y-%m-%d')

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'})

# ==================== NEW FEATURES ====================

@app.route('/api/find-study-buddies')
def find_study_buddies():
    """Find users with filters, prioritizing same country/school/level"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    user = User.query.get(user_id)
    
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    # Get filter from query params
    search_query = request.args.get('search', '').lower()
    country_filter = request.args.get('country', '')
    school_filter = request.args.get('school', '')
    level_filter = request.args.get('level', '')
    
    # Build base query - get all verified users except self
    query = User.query.filter(
        User.id != user_id,
        User.is_verified == True
    )
    
    # Apply country filter only if explicitly set (not 'all' or empty)
    if country_filter and country_filter != 'all':
        query = query.filter(User.country == country_filter)
    
    # Apply school filter only if explicitly set (not 'all' or empty)
    if school_filter and school_filter != 'all':
        query = query.filter(User.school == school_filter)
    
    # Apply level filter only if explicitly set (not 'all' or empty)
    if level_filter and level_filter != 'all':
        query = query.filter(User.study_level == level_filter)
    
    # Search by name or username
    if search_query:
        query = query.filter(
            (User.name.ilike(f'%{search_query}%')) |
            (User.username.ilike(f'%{search_query}%'))
        )
    
    buddies = query.limit(100).all()
    
    logger.info(f'Find buddies: found {len(buddies)} users for user {user_id}')
    
    # Prioritize: same country + school + level > same country + school > same country > other
    buddies_data = []
    for buddy in buddies:
        # Check if already connected
        is_connected = Connection.query.filter(
            ((Connection.user_id == user_id) & (Connection.connected_user_id == buddy.id)) |
            ((Connection.user_id == buddy.id) & (Connection.connected_user_id == user_id))
        ).first() is not None
        
        # Get user tags
        tags = [t.tag for t in buddy.tags]
        
        # Calculate priority score
        priority = 0
        if buddy.country == user.country:
            priority += 100
        if buddy.school == user.school:
            priority += 50
        if buddy.study_level == user.study_level:
            priority += 25
        
        buddies_data.append({
            'id': buddy.id,
            'name': buddy.name,
            'username': buddy.username,
            'profile_pic': buddy.get_profile_pic_url(),
            'school': buddy.school,
            'study_level': buddy.study_level,
            'country': buddy.country,
            'tags': tags,
            'total_quizzes': buddy.get_total_quizzes(),
            'average_score': buddy.get_average_score(),
            'is_connected': is_connected,
            'priority': priority
        })
    
    # Sort by priority (highest first), then by name
    buddies_data.sort(key=lambda x: (-x['priority'], x['name']))
    
    # Remove priority field before returning (it's just for sorting)
    for buddy in buddies_data:
        del buddy['priority']
    
    return jsonify({'success': True, 'buddies': buddies_data})

@app.route('/api/add-tag', methods=['POST'])
def add_tag():
    """Add a tag to current user"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    tag = data.get('tag', '').strip()
    
    if not tag or len(tag) > 50:
        return jsonify({'error': 'Invalid tag'}), 400
    
    user_id = session['user_id']
    
    # Check if tag already exists
    existing = UserTag.query.filter_by(user_id=user_id, tag=tag).first()
    if existing:
        return jsonify({'error': 'Tag already exists'}), 400
    
    try:
        user_tag = UserTag(user_id=user_id, tag=tag)
        db.session.add(user_tag)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Tag "{tag}" added!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Error adding tag: {str(e)}'}), 500

@app.route('/api/remove-tag/<int:tag_id>', methods=['DELETE'])
def remove_tag(tag_id):
    """Remove a tag from current user"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    tag = UserTag.query.filter_by(id=tag_id, user_id=user_id).first()
    
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
    """Get current user's tags"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    tags = UserTag.query.filter_by(user_id=user_id).order_by(UserTag.created_at.desc()).all()
    
    tags_data = [{'id': t.id, 'tag': t.tag} for t in tags]
    return jsonify({'success': True, 'tags': tags_data})

@app.route('/api/send-message', methods=['POST'])
def send_message_api():
    """Send message to another user"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    receiver_id = data.get('receiver_id')
    content = data.get('content', '').strip()
    
    if not receiver_id or not content:
        return jsonify({'error': 'Missing receiver or message content'}), 400
    
    if len(content) > 5000:
        return jsonify({'error': 'Message too long'}), 400
    
    sender_id = session['user_id']
    
    # Check if connected
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
        
        return jsonify({
            'success': True,
            'message': {
                'id': message.id,
                'sender_id': sender_id,
                'receiver_id': receiver_id,
                'content': content,
                'created_at': message.created_at.isoformat()
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-messages/<int:buddy_id>')
def get_messages(buddy_id):
    """Get conversation with a specific user"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Get all messages between these two users
    messages = Message.query.filter(
        ((Message.sender_id == user_id) & (Message.receiver_id == buddy_id)) |
        ((Message.sender_id == buddy_id) & (Message.receiver_id == user_id))
    ).order_by(Message.created_at.asc()).all()
    
    # Mark received messages as read
    Message.query.filter(
        (Message.sender_id == buddy_id) &
        (Message.receiver_id == user_id) &
        (Message.is_read == False)
    ).update({Message.is_read: True})
    db.session.commit()
    
    messages_data = []
    for msg in messages:
        messages_data.append({
            'id': msg.id,
            'sender_id': msg.sender_id,
            'sender_name': msg.sender.name,
            'receiver_id': msg.receiver_id,
            'content': msg.content,
            'is_read': msg.is_read,
            'created_at': msg.created_at.isoformat()
        })
    
    return jsonify({'success': True, 'messages': messages_data})

@app.route('/api/get-connections')
def get_connections():
    """Get all user connections (bidirectional)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Get connections where user is the initiator
    initiated = Connection.query.filter_by(user_id=user_id).all()
    
    # Get connections where user is the recipient (someone connected to them)
    received = Connection.query.filter_by(connected_user_id=user_id).all()
    
    connections_data = []
    seen_ids = set()
    
    # Process initiated connections
    for conn in initiated:
        if conn.connected_user_id not in seen_ids:
            seen_ids.add(conn.connected_user_id)
            # Get unread message count
            unread_count = Message.query.filter(
                (Message.sender_id == conn.connected_user_id) &
                (Message.receiver_id == user_id) &
                (Message.is_read == False)
            ).count()
            
            # Get tags
            tags = [t.tag for t in conn.connected_user.tags]
            
            connections_data.append({
                'id': conn.connected_user.id,
                'name': conn.connected_user.name,
                'username': conn.connected_user.username,
                'profile_pic': conn.connected_user.get_profile_pic_url(),
                'study_level': conn.connected_user.study_level,
                'average_score': conn.connected_user.get_average_score(),
                'tags': tags,
                'unread_count': unread_count,
                'connected_at': conn.created_at.isoformat()
            })
    
    # Process received connections
    for conn in received:
        if conn.user_id not in seen_ids:
            seen_ids.add(conn.user_id)
            # Get unread message count
            unread_count = Message.query.filter(
                (Message.sender_id == conn.user_id) &
                (Message.receiver_id == user_id) &
                (Message.is_read == False)
            ).count()
            
            # Get tags
            tags = [t.tag for t in conn.user.tags]
            
            connections_data.append({
                'id': conn.user.id,
                'name': conn.user.name,
                'username': conn.user.username,
                'profile_pic': conn.user.get_profile_pic_url(),
                'study_level': conn.user.study_level,
                'average_score': conn.user.get_average_score(),
                'tags': tags,
                'unread_count': unread_count,
                'connected_at': conn.created_at.isoformat()
            })
    
    return jsonify({'success': True, 'connections': connections_data})

@app.route('/api/discussions')
def get_discussions():
    """Get brainstorm discussions"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Return sample discussions for now
    discussions = [
        {
            'id': 1,
            'title': 'Understanding Calculus Derivatives',
            'description': 'Let\'s discuss how derivatives work and best ways to solve problems',
            'members': 12,
            'messages': 45
        },
        {
            'id': 2,
            'title': 'Physics Mechanics Help',
            'description': 'Need help with Newton\'s laws and motion problems',
            'members': 8,
            'messages': 23
        },
        {
            'id': 3,
            'title': 'Chemistry Reactions',
            'description': 'Balancing equations and understanding reactions',
            'members': 15,
            'messages': 67
        }
    ]
    
    return jsonify({'success': True, 'discussions': discussions})

@app.route('/api/ask-ai', methods=['POST'])
def ask_ai():
    """Ask AI for explanations on topics - supports multi-turn conversations and PDF uploads"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Support both JSON and multipart form data
    if request.content_type and 'multipart/form-data' in request.content_type:
        question = request.form.get('question', '')
        reset_conversation = request.form.get('reset', False)
    else:
        data = request.json or {}
        question = data.get('question', '')
        reset_conversation = data.get('reset', False)
    
    if not question:
        return jsonify({'error': 'Please provide a question'}), 400
    
    try:
        # Initialize or retrieve conversation history
        if reset_conversation or 'ai_conversation' not in session:
            conversation_history = []
            session['ai_conversation'] = []
        else:
            conversation_history = session.get('ai_conversation', [])
        
        # Build context from conversation history
        context = ""
        if conversation_history:
            context = "Previous conversation:\n"
            for i, exchange in enumerate(conversation_history, 1):
                context += f"\nQ{i}: {exchange['question']}\nA{i}: {exchange['answer']}\n"
            context += "\n---\n\n"
        
        # Handle PDF upload if provided
        pdf_text = None
        if 'pdf' in request.files:
            pdf_file = request.files['pdf']
            if pdf_file and pdf_file.filename and allowed_file(pdf_file.filename):
                pdf_text = extract_pdf_text(pdf_file)
                logger.info(f'PDF uploaded for AI chat by user {session["user_id"]}, extracted {len(pdf_text) if pdf_text else 0} chars')
        
        # Build prompt with PDF content if available
        if pdf_text:
            prompt = f"""{context}The user has uploaded a PDF document. Here is the extracted text from the PDF:

--- PDF CONTENT START ---
{pdf_text[:4000]}
--- PDF CONTENT END ---

Based on the PDF content above, answer this question concisely and accurately:

{question}

If the question cannot be answered from the PDF content, say so clearly. Keep the response focused on what's asked."""
        else:
            prompt = f"""{context}Answer this question concisely and accurately:

{question}

Keep the response brief and focused on what's asked."""
        
        response = model.generate_content(prompt)
        explanation = response.text
        
        # Add to conversation history
        conversation_history.append({
            'question': question,
            'answer': explanation
        })
        session['ai_conversation'] = conversation_history
        session.modified = True
        
        return jsonify({
            'success': True,
            'explanation': explanation,
            'conversation_count': len(conversation_history),
            'pdf_processed': pdf_text is not None
        })
    except Exception as e:
        logger.error(f'Error in ask-ai: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error generating explanation: {str(e)}'}), 500


@app.route('/api/connect-user', methods=['POST'])
def connect_user():
    """Add a user as a connection/study buddy - bidirectional"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    connected_user_id = data.get('user_id')
    
    if not connected_user_id:
        return jsonify({'error': 'User ID required'}), 400
    
    user_id = session['user_id']
    
    # Check if already connected (in either direction)
    existing = Connection.query.filter(
        ((Connection.user_id == user_id) & (Connection.connected_user_id == connected_user_id)) |
        ((Connection.user_id == connected_user_id) & (Connection.connected_user_id == user_id))
    ).first()
    
    if existing:
        return jsonify({'error': 'Already connected with this user'}), 400
    
    try:
        # Create bidirectional connections
        connection1 = Connection(user_id=user_id, connected_user_id=connected_user_id)
        connection2 = Connection(user_id=connected_user_id, connected_user_id=user_id)
        db.session.add(connection1)
        db.session.add(connection2)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Connected successfully!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# ==================== BRAINSTORMING CHAT GROUPS ====================

@app.route('/api/create-group', methods=['POST'])
def create_group():
    """Create a new brainstorming group"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    name = data.get('name', '').strip()
    description = data.get('description', '').strip()
    is_private = data.get('is_private', False)
    password = data.get('password', '')
    
    if not name or len(name) < 3:
        return jsonify({'error': 'Group name must be at least 3 characters'}), 400
    
    try:
        group = ChatGroup(
            name=name,
            description=description,
            created_by=user_id,
            is_private=is_private
        )
        
        if is_private and password:
            group.set_password(password)
        
        db.session.add(group)
        db.session.flush()
        
        # Add creator as admin
        member = ChatGroupMember(group_id=group.id, user_id=user_id, role='admin')
        db.session.add(member)
        db.session.commit()
        
        logger.info(f'Group {group.name} created by user {user_id}')
        return jsonify({'success': True, 'group_id': group.id, 'message': 'Group created successfully!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-groups')
def get_groups():
    """Get all groups the user is part of"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Get groups user is member of
    user_memberships = ChatGroupMember.query.filter_by(user_id=user_id).all()
    group_ids = [m.group_id for m in user_memberships]
    
    groups_data = []
    for membership in user_memberships:
        group = membership.group
        member_count = ChatGroupMember.query.filter_by(group_id=group.id).count()
        message_count = GroupMessage.query.filter_by(group_id=group.id).count()
        
        groups_data.append({
            'id': group.id,
            'name': group.name,
            'description': group.description,
            'is_private': group.is_private,
            'created_by': group.created_by,
            'creator_name': group.creator.name,
            'member_count': member_count,
            'message_count': message_count,
            'your_role': membership.role,
            'created_at': group.created_at.isoformat()
        })
    
    return jsonify({'success': True, 'groups': groups_data})

@app.route('/api/discover-groups')
def discover_groups():
    """Discover all available groups (public + private with pending requests)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Get all groups
    all_groups = ChatGroup.query.all()
    
    # Get groups user is already member of
    user_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(user_id=user_id).all()]
    
    groups_data = []
    for group in all_groups:
        # Skip groups user is already member of
        if group.id in user_group_ids:
            continue
        
        member_count = ChatGroupMember.query.filter_by(group_id=group.id).count()
        message_count = GroupMessage.query.filter_by(group_id=group.id).count()
        
        # Check if user has pending request
        pending_request = GroupJoinRequest.query.filter_by(
            group_id=group.id,
            user_id=user_id,
            status='pending'
        ).first()
        
        groups_data.append({
            'id': group.id,
            'name': group.name,
            'description': group.description,
            'is_private': group.is_private,
            'created_by': group.created_by,
            'creator_name': group.creator.name,
            'member_count': member_count,
            'message_count': message_count,
            'has_pending_request': pending_request is not None,
            'created_at': group.created_at.isoformat()
        })
    
    return jsonify({'success': True, 'groups': groups_data})

@app.route('/api/search-groups', methods=['GET'])
def search_groups():
    """Search for groups by name or description"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    query = request.args.get('q', '').strip()
    
    if not query or len(query) < 2:
        return jsonify({'error': 'Search query must be at least 2 characters'}), 400
    
    # Get all groups
    all_groups = ChatGroup.query.all()
    
    # Get groups user is already member of
    user_group_ids = [m.group_id for m in ChatGroupMember.query.filter_by(user_id=user_id).all()]
    
    groups_data = []
    for group in all_groups:
        # Skip groups user is already member of
        if group.id in user_group_ids:
            continue
        
        # Search in name and description
        if query.lower() not in group.name.lower() and query.lower() not in (group.description or '').lower():
            continue
        
        member_count = ChatGroupMember.query.filter_by(group_id=group.id).count()
        message_count = GroupMessage.query.filter_by(group_id=group.id).count()
        
        # Check if user has pending request
        pending_request = GroupJoinRequest.query.filter_by(
            group_id=group.id,
            user_id=user_id,
            status='pending'
        ).first()
        
        groups_data.append({
            'id': group.id,
            'name': group.name,
            'description': group.description,
            'is_private': group.is_private,
            'created_by': group.created_by,
            'creator_name': group.creator.name,
            'member_count': member_count,
            'message_count': message_count,
            'has_pending_request': pending_request is not None,
            'created_at': group.created_at.isoformat()
        })
    
    return jsonify({'success': True, 'groups': groups_data})

@app.route('/api/add-member-to-group', methods=['POST'])
def add_member_to_group():
    """Add a member to a group (admin only or join request)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    group_id = data.get('group_id')
    target_user_id = data.get('user_id', user_id)  # Default to current user
    password = data.get('password')
    
    if not group_id:
        return jsonify({'error': 'Group ID required'}), 400
    
    group = ChatGroup.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    
    # Check if user is member and admin
    current_member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    
    if user_id == target_user_id:
        # User trying to join themselves
        if current_member:
            return jsonify({'error': 'Already a member of this group'}), 400
        
        # Check if group is private
        if group.is_private:
            # Verify password for private group
            if not password or not group.check_password(password):
                return jsonify({'error': 'Invalid group password'}), 401
            
            # Create join request
            existing_request = GroupJoinRequest.query.filter_by(
                group_id=group_id, user_id=user_id
            ).first()
            if existing_request:
                return jsonify({'error': 'Join request already pending'}), 400
            
            request_obj = GroupJoinRequest(group_id=group_id, user_id=user_id)
            db.session.add(request_obj)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Join request sent!'})
        else:
            # Auto-join public group
            member = ChatGroupMember(group_id=group_id, user_id=user_id, role='member')
            db.session.add(member)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Joined group successfully!'})
    else:
        # Admin adding another user
        if not current_member or current_member.role != 'admin':
            return jsonify({'error': 'Only admins can add members'}), 403
        
        # Check if target user is already member
        existing = ChatGroupMember.query.filter_by(group_id=group_id, user_id=target_user_id).first()
        if existing:
            return jsonify({'error': 'User already in group'}), 400
        
        # Check if users are connected
        is_connected = Connection.query.filter(
            ((Connection.user_id == user_id) & (Connection.connected_user_id == target_user_id)) |
            ((Connection.user_id == target_user_id) & (Connection.connected_user_id == user_id))
        ).first()
        
        if not is_connected:
            return jsonify({'error': 'You can only add connected users'}), 403
        
        member = ChatGroupMember(group_id=group_id, user_id=target_user_id, role='member')
        db.session.add(member)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Member added successfully!'})

@app.route('/api/remove-member-from-group', methods=['POST'])
def remove_member_from_group():
    """Remove a member from group (admin only)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    group_id = data.get('group_id')
    target_user_id = data.get('user_id')
    
    group = ChatGroup.query.get(group_id)
    if not group:
        return jsonify({'error': 'Group not found'}), 404
    
    # Check if requester is admin
    admin_member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not admin_member or admin_member.role != 'admin':
        return jsonify({'error': 'Only admins can remove members'}), 403
    
    # Cannot remove creator
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
    """Get all members of a group"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    members = ChatGroupMember.query.filter_by(group_id=group_id).all()
    
    members_data = []
    for member in members:
        members_data.append({
            'id': member.user.id,
            'name': member.user.name,
            'username': member.user.username,
            'profile_pic': member.user.get_profile_pic_url(),
            'role': member.role,
            'joined_at': member.joined_at.isoformat()
        })
    
    return jsonify({'success': True, 'members': members_data})

@app.route('/api/send-group-message', methods=['POST'])
def send_group_message():
    """Send message to group - supports text, image, pdf, poll, and AI message types"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Support both JSON and multipart form data
    if request.content_type and 'multipart/form-data' in request.content_type:
        group_id = request.form.get('group_id')
        content = request.form.get('content', '').strip()
        message_type = request.form.get('message_type', 'text')
    else:
        data = request.json or {}
        group_id = data.get('group_id')
        content = data.get('content', '').strip()
        message_type = data.get('message_type', 'text')
    
    if not group_id:
        return jsonify({'error': 'Group ID required'}), 400
    
    if not content and message_type == 'text':
        return jsonify({'error': 'Content required for text messages'}), 400
    
    if len(content) > 5000:
        return jsonify({'error': 'Message too long'}), 400
    
    # Check if user is member
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403
    
    try:
        image_path = None
        pdf_path = None
        
        # Handle image upload
        if message_type == 'image' and 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename):
                os.makedirs('uploads/group_chat', exist_ok=True)
                timestamp = datetime.utcnow().timestamp()
                ext = file.filename.rsplit('.', 1)[1].lower()
                image_path = f"group_{group_id}_{user_id}_{timestamp}.{ext}"
                filepath = os.path.join('uploads/group_chat', image_path)
                file.save(filepath)
                logger.info(f'Group chat image uploaded: {image_path}')
        
        # Handle PDF upload
        if message_type == 'pdf' and 'pdf' in request.files:
            file = request.files['pdf']
            if file and file.filename and allowed_file(file.filename):
                os.makedirs('uploads/group_chat', exist_ok=True)
                timestamp = datetime.utcnow().timestamp()
                ext = file.filename.rsplit('.', 1)[1].lower()
                pdf_path = f"group_{group_id}_{user_id}_{timestamp}.{ext}"
                filepath = os.path.join('uploads/group_chat', pdf_path)
                file.save(filepath)
                logger.info(f'Group chat PDF uploaded: {pdf_path}')
        
        msg = GroupMessage(
            group_id=group_id, 
            sender_id=user_id, 
            content=content,
            message_type=message_type,
            image_path=image_path,
            pdf_path=pdf_path
        )
        db.session.add(msg)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': {
                'id': msg.id,
                'sender_id': user_id,
                'sender_name': msg.sender.name,
                'content': content,
                'message_type': message_type,
                'image_url': f'/uploads/group_chat/{image_path}' if image_path else None,
                'pdf_url': f'/uploads/group_chat/{pdf_path}' if pdf_path else None,
                'created_at': msg.created_at.isoformat()
            }
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error sending group message: {str(e)}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/get-group-messages/<int:group_id>')
def get_group_messages(group_id):
    """Get messages from a group - includes text, image, poll, and AI message types"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Check if user is member
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403
    
    messages = GroupMessage.query.filter_by(group_id=group_id).order_by(GroupMessage.created_at.asc()).all()
    
    messages_data = []
    for msg in messages:
        msg_data = {
            'id': msg.id,
            'sender_id': msg.sender_id,
            'sender_name': msg.sender.name,
            'sender_pic': msg.sender.get_profile_pic_url(),
            'content': msg.content,
            'message_type': msg.message_type,
            'image_url': f'/uploads/group_chat/{msg.image_path}' if msg.image_path else None,
            'pdf_url': f'/uploads/group_chat/{msg.pdf_path}' if msg.pdf_path else None,
            'created_at': msg.created_at.isoformat(),
            'is_sent': msg.sender_id == user_id
        }
        
        # Include poll data if this is a poll message
        if msg.message_type == 'poll' and msg.poll_id:
            poll = Poll.query.get(msg.poll_id)

            if poll:
                options_data = []
                for opt in poll.options:
                    vote_count = len(opt.votes)
                    has_voted = PollVote.query.filter_by(option_id=opt.id, user_id=user_id).first() is not None
                    options_data.append({
                        'id': opt.id,
                        'text': opt.option_text,
                        'votes': vote_count,
                        'has_voted': has_voted
                    })
                total_votes = sum(opt['votes'] for opt in options_data)
                msg_data['poll'] = {
                    'id': poll.id,
                    'question': poll.question,
                    'is_active': poll.is_active,
                    'options': options_data,
                    'total_votes': total_votes
                }
        
        messages_data.append(msg_data)
    
    return jsonify({'success': True, 'messages': messages_data, 'current_user_id': user_id})

@app.route('/api/create-poll', methods=['POST'])
def create_poll():
    """Create a poll in a group"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    group_id = data.get('group_id')
    question = data.get('question', '').strip()
    options = data.get('options', [])
    
    if not group_id or not question:
        return jsonify({'error': 'Group ID and question required'}), 400
    
    if len(options) < 2 or len(options) > 6:
        return jsonify({'error': 'Poll must have 2-6 options'}), 400
    
    # Check if user is member
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403
    
    try:
        # Create poll
        poll = Poll(
            group_id=group_id,
            creator_id=user_id,
            question=question
        )
        db.session.add(poll)
        db.session.flush()
        
        # Create options
        for opt_text in options:
            if opt_text.strip():
                option = PollOption(poll_id=poll.id, option_text=opt_text.strip())
                db.session.add(option)
        
        # Create poll message in group chat
        poll_msg = GroupMessage(
            group_id=group_id,
            sender_id=user_id,
            content=f"📊 Poll: {question}",
            message_type='poll',
            poll_id=poll.id
        )
        db.session.add(poll_msg)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'poll_id': poll.id,
            'message_id': poll_msg.id,
            'message': 'Poll created successfully!'
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error creating poll: {str(e)}', exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-polls/<int:group_id>')
def get_polls(group_id):
    """Get active polls for a group"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Check if user is member
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403
    
    polls = Poll.query.filter_by(group_id=group_id, is_active=True).order_by(Poll.created_at.desc()).all()
    
    polls_data = []
    for poll in polls:
        options_data = []
        for opt in poll.options:
            vote_count = len(opt.votes)
            has_voted = PollVote.query.filter_by(option_id=opt.id, user_id=user_id).first() is not None
            options_data.append({
                'id': opt.id,
                'text': opt.option_text,
                'votes': vote_count,
                'has_voted': has_voted
            })
        total_votes = sum(opt['votes'] for opt in options_data)
        polls_data.append({
            'id': poll.id,
            'question': poll.question,
            'creator_name': poll.creator.name,
            'options': options_data,
            'total_votes': total_votes,
            'created_at': poll.created_at.isoformat()
        })
    
    return jsonify({'success': True, 'polls': polls_data})

@app.route('/api/vote-poll', methods=['POST'])
def vote_poll():
    """Vote on a poll option"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    poll_id = data.get('poll_id')
    option_id = data.get('option_id')
    
    if not poll_id or not option_id:
        return jsonify({'error': 'Poll ID and option ID required'}), 400
    
    poll = Poll.query.get(poll_id)
    if not poll or not poll.is_active:
        return jsonify({'error': 'Poll not found or inactive'}), 404
    
    # Check if user is member of group
    member = ChatGroupMember.query.filter_by(group_id=poll.group_id, user_id=user_id).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403
    
    # Check if user already voted
    existing_vote = PollVote.query.filter_by(poll_id=poll_id, user_id=user_id).first()
    if existing_vote:
        # Change vote
        existing_vote.option_id = option_id
        db.session.commit()
        return jsonify({'success': True, 'message': 'Vote updated!'})
    
    try:
        vote = PollVote(poll_id=poll_id, option_id=option_id, user_id=user_id)
        db.session.add(vote)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Vote cast!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/ask-ai-group', methods=['POST'])
def ask_ai_group():
    """Ask AI for brainstorming ideas, problem solving, or explanations in group context - supports images"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # Handle both JSON and multipart form data
    if request.content_type and 'multipart/form-data' in request.content_type:
        question = request.form.get('question', '')
        group_id = request.form.get('group_id')
        context = request.form.get('context', '')
    else:
        data = request.json or {}
        question = data.get('question', '')
        group_id = data.get('group_id')
        context = data.get('context', '')
    
    if not question:
        return jsonify({'error': 'Please provide a question'}), 400
    
    # Check group membership if group_id provided
    if group_id:
        member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=session['user_id']).first()
        if not member:
            return jsonify({'error': 'You are not a member of this group'}), 403
    
    try:
        # Build base prompt
        prompt = f"""You are an AI Brainstorm Helper for a study group.

Group Context: {context if context else 'General study group discussion'}

User Question: {question}

Provide a helpful, clear, and actionable response. Keep it concise but thorough."""
        
        # Handle image if provided
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename):
                try:
                    import io
                    from PIL import Image
                    
                    # Read and process image
                    image_data = file.read()
                    image = Image.open(io.BytesIO(image_data))
                    
                    # Send to Gemini with image
                    response = model.generate_content([prompt, image])
                    logger.info(f'AI group request with image processed for user {session["user_id"]}')
                except Exception as img_error:
                    logger.warning(f'Error processing image: {str(img_error)}, falling back to text-only')
                    response = model.generate_content(prompt)
            else:
                response = model.generate_content(prompt)
        else:
            response = model.generate_content(prompt)
        
        explanation = response.text
        
        return jsonify({
            'success': True,
            'explanation': explanation
        })
    except Exception as e:
        logger.error(f'Error generating AI group response: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/api/schedule-brainstorm', methods=['POST'])
def schedule_brainstorm():
    """Schedule a brainstorm session - notifies all group members"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    group_id = data.get('group_id')
    title = data.get('title', '').strip()
    description = data.get('description', '').strip()
    scheduled_time = data.get('scheduled_time')
    
    if not group_id or not title or not scheduled_time:
        return jsonify({'error': 'Group ID, title, and scheduled time required'}), 400
    
    # Check if user is admin
    member = ChatGroupMember.query.filter_by(group_id=group_id, user_id=user_id).first()
    if not member or member.role != 'admin':
        return jsonify({'error': 'Only group admins can schedule sessions'}), 403
    
    try:
        session_obj = BrainstormSession(
            group_id=group_id,
            title=title,
            description=description,
            scheduled_time=datetime.fromisoformat(scheduled_time)
        )
        db.session.add(session_obj)
        db.session.flush()
        
        # Get all group members
        group_members = ChatGroupMember.query.filter_by(group_id=group_id).all()
        creator = User.query.get(user_id)
        
        # Send notification message to each member
        for member_record in group_members:
            if member_record.user_id != user_id:  # Don't notify creator
                notif_msg = GroupMessage(
                    group_id=group_id,
                    sender_id=user_id,
                    content=f"📅 Brainstorm Session Scheduled!\n\n{title}\n\n{description}\n\nTime: {scheduled_time}\n\nJoin us!"
                )
                db.session.add(notif_msg)
        
        db.session.commit()
        logger.info(f'Brainstorm session "{title}" scheduled for group {group_id} - notified {len(group_members)} members')
        return jsonify({'success': True, 'session_id': session_obj.id, 'message': f'Session scheduled! Notified {len(group_members)} members'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-group-sessions/<int:group_id>')
def get_group_sessions(group_id):
    """Get brainstorm sessions for a group"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    sessions = BrainstormSession.query.filter_by(group_id=group_id).order_by(BrainstormSession.scheduled_time.desc()).all()
    
    sessions_data = []
    for sess in sessions:
        sessions_data.append({
            'id': sess.id,
            'title': sess.title,
            'description': sess.description,
            'scheduled_time': sess.scheduled_time.isoformat(),
            'status': sess.status,
            'note_count': len(sess.notes),
            'created_at': sess.created_at.isoformat()
        })
    
    return jsonify({'success': True, 'sessions': sessions_data})

@app.route('/api/add-brainstorm-note', methods=['POST'])
def add_brainstorm_note():
    """Add a note during brainstorming"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    session_id = data.get('session_id')
    content = data.get('content', '').strip()
    mentions = data.get('mentions', [])  # Array of user IDs
    tags = data.get('tags', [])  # Array of tag strings
    
    if not session_id or not content:
        return jsonify({'error': 'Session ID and content required'}), 400
    
    session_obj = BrainstormSession.query.get(session_id)
    if not session_obj:
        return jsonify({'error': 'Session not found'}), 404
    
    # Check if user is member of group
    member = ChatGroupMember.query.filter_by(
        group_id=session_obj.group_id,
        user_id=user_id
    ).first()
    if not member:
        return jsonify({'error': 'You are not a member of this group'}), 403
    
    try:
        note = BrainstormNote(
            session_id=session_id,
            user_id=user_id,
            content=content,
            mentions=json.dumps(mentions) if mentions else None,
            tags=json.dumps(tags) if tags else None
        )
        db.session.add(note)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'note': {
                'id': note.id,
                'user_name': note.user.name,
                'content': content,
                'mentions': mentions,
                'tags': tags,
                'created_at': note.created_at.isoformat()
            }
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-brainstorm-notes/<int:session_id>')
def get_brainstorm_notes(session_id):
    """Get all notes from a brainstorm session with rich content"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    notes = BrainstormNote.query.filter_by(session_id=session_id).order_by(BrainstormNote.created_at.asc()).all()
    
    notes_data = []
    for note in notes:
        note_dict = {
            'id': note.id,
            'user_id': note.user_id,
            'user_name': note.user.name,
            'user_pic': note.user.get_profile_pic_url(),
            'content': note.content,
            'mentions': json.loads(note.mentions) if note.mentions else [],
            'tags': json.loads(note.tags) if note.tags else [],
            'mention_ai': note.mention_ai,
            'has_media': note.has_media,
            'image_url': f'/uploads/{note.image_path}' if note.image_path else None,
            'textbook_url': f'/uploads/{note.textbook_path}' if note.textbook_path else None,
            'solved_problem': note.solved_problem,
            'created_at': note.created_at.isoformat()
        }
        notes_data.append(note_dict)
    
    return jsonify({'success': True, 'notes': notes_data})

@app.route('/api/upload-brainstorm-image', methods=['POST'])
def upload_brainstorm_image():
    """Upload image for brainstorm note"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    if 'image' not in request.files:
        return jsonify({'error': 'No image file'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if file and allowed_file(file.filename):
        try:
            # Create brainstorm uploads folder
            os.makedirs('uploads/brainstorm', exist_ok=True)
            
            user_id = session['user_id']
            timestamp = datetime.utcnow().timestamp()
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"brainstorm_{user_id}_{timestamp}.{ext}"
            filepath = os.path.join('uploads/brainstorm', filename)
            
            file.save(filepath)
            logger.info(f'Brainstorm image uploaded: {filename}')
            
            return jsonify({
                'success': True,
                'filename': filename,
                'url': f'/uploads/brainstorm/{filename}'
            })
        except Exception as e:
            logger.error(f'Error uploading brainstorm image: {str(e)}', exc_info=True)
            return jsonify({'error': str(e)}), 500
    
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/api/add-brainstorm-note-rich', methods=['POST'])
def add_brainstorm_note_rich():
    """Add a rich note during brainstorming with images, AI mentions, problem solving"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    session_id = data.get('session_id')
    content = data.get('content', '').strip()
    mentions = data.get('mentions', [])  # Array of user IDs
    tags = data.get('tags', [])  # Array of tag strings
    mention_ai = data.get('mention_ai', False)  # Whether AI was tagged
    image_path = data.get('image_path')  # Uploaded image filename
    solved_problem = data.get('solved_problem')  # Problem solution
    textbook_ref = data.get('textbook_ref')  # Textbook reference
    
    if not session_id or not content:
        return jsonify({'error': 'Session and content required'}), 400
    
    session_obj = BrainstormSession.query.get(session_id)
    if not session_obj:
        return jsonify({'error': 'Session not found'}), 404
    
    # Check if user is member of group
    member = ChatGroupMember.query.filter_by(
        group_id=session_obj.group_id,
        user_id=user_id
    ).first()
    if not member:
        return jsonify({'error': 'Not a member of this group'}), 403
    
    try:
        has_media = bool(image_path or textbook_ref or solved_problem)
        
        note = BrainstormNote(
            session_id=session_id,
            user_id=user_id,
            content=content,
            mentions=json.dumps(mentions) if mentions else None,
            tags=json.dumps(tags) if tags else None,
            mention_ai=mention_ai,
            image_path=image_path,
            textbook_path=textbook_ref,
            solved_problem=solved_problem,
            has_media=has_media
        )
        db.session.add(note)
        db.session.commit()
        
        # If AI was mentioned, generate AI response
        ai_response = None
        if mention_ai:
            try:
                prompt = f"""The user is brainstorming on this topic and mentioned you for help:

Content: {content}
Mentioned in tags: {', '.join(tags) if tags else 'General help'}

Provide a helpful, concise response to assist with their brainstorming. Keep it brief (2-3 sentences max)."""
                
                response = model.generate_content(prompt)
                ai_response = response.text
                
                # Add AI's response as a note
                ai_note = BrainstormNote(
                    session_id=session_id,
                    user_id=1,  # Special AI user ID (system)
                    content=f"AI Assistant: {ai_response}",
                    has_media=False
                )
                db.session.add(ai_note)
                db.session.commit()
            except Exception as e:
                logger.error(f'Error generating AI response: {str(e)}', exc_info=True)
        
        return jsonify({
            'success': True,
            'note': {
                'id': note.id,
                'user_name': note.user.name,
                'content': content,
                'mentions': mentions,
                'tags': tags,
                'mention_ai': mention_ai,
                'has_media': has_media,
                'created_at': note.created_at.isoformat()
            },
            'ai_response': ai_response
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error adding brainstorm note: {str(e)}', exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/solve-problem-ai', methods=['POST'])
def solve_problem_ai():
    """Use AI to help solve a problem during brainstorming"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    problem = data.get('problem', '')
    context = data.get('context', '')
    
    if not problem:
        return jsonify({'error': 'Problem description required'}), 400
    
    try:
        prompt = f"""Help solve this problem:

Problem: {problem}

Context/Background: {context if context else 'No additional context provided'}

Provide a clear step-by-step solution. Be concise but thorough."""
        
        response = model.generate_content(prompt)
        solution = response.text
        
        return jsonify({
            'success': True,
            'solution': solution
        })
    except Exception as e:
        logger.error(f'Error solving problem with AI: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/api/brainstorm-ai-suggestions', methods=['POST'])
def brainstorm_ai_suggestions():
    """Get AI suggestions during brainstorming"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    topic = data.get('topic', '')
    current_ideas = data.get('current_ideas', '')
    
    if not topic:
        return jsonify({'error': 'Topic required'}), 400
    
    try:
        prompt = f"""You are helping a study group brainstorm on this topic:

Topic: {topic}

Current ideas discussed: {current_ideas if current_ideas else 'Just starting out'}

Generate 3-4 creative ideas or questions to deepen their brainstorming. Make them specific and actionable."""
        
        response = model.generate_content(prompt)
        suggestions = response.text
        
        return jsonify({
            'success': True,
            'suggestions': suggestions
        })
    except Exception as e:
        logger.error(f'Error generating brainstorm suggestions: {str(e)}', exc_info=True)
        return jsonify({'error': f'Error: {str(e)}'}), 500

@app.route('/api/accept-join-request', methods=['POST'])
def accept_join_request():
    """Accept a group join request (admin only)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    request_id = data.get('request_id')
    join_request = GroupJoinRequest.query.get(request_id)
    
    if not join_request:
        return jsonify({'error': 'Request not found'}), 404
    
    # Check if user is admin of group
    admin_member = ChatGroupMember.query.filter_by(
        group_id=join_request.group_id,
        user_id=user_id
    ).first()
    if not admin_member or admin_member.role != 'admin':
        return jsonify({'error': 'Only admins can accept requests'}), 403
    
    try:
        # Add member
        member = ChatGroupMember(
            group_id=join_request.group_id,
            user_id=join_request.user_id,
            role='member'
        )
        db.session.add(member)
        
        # Update request
        join_request.status = 'approved'
        join_request.responded_at = datetime.utcnow()
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Request accepted!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/reject-join-request', methods=['POST'])
def reject_join_request():
    """Reject a group join request (admin only)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    data = request.json
    
    request_id = data.get('request_id')
    join_request = GroupJoinRequest.query.get(request_id)
    
    if not join_request:
        return jsonify({'error': 'Request not found'}), 404
    
    # Check if user is admin
    admin_member = ChatGroupMember.query.filter_by(
        group_id=join_request.group_id,
        user_id=user_id
    ).first()
    if not admin_member or admin_member.role != 'admin':
        return jsonify({'error': 'Only admins can reject requests'}), 403
    
    try:
        join_request.status = 'rejected'
        join_request.responded_at = datetime.utcnow()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Request rejected!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/get-pending-join-requests')
def get_pending_join_requests():
    """Get all pending join requests for groups the user is admin of"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Get groups where user is admin
    admin_groups = ChatGroupMember.query.filter_by(
        user_id=user_id,
        role='admin'
    ).all()
    
    admin_group_ids = [m.group_id for m in admin_groups]
    
    # Get pending requests for these groups
    pending_requests = GroupJoinRequest.query.filter(
        GroupJoinRequest.group_id.in_(admin_group_ids),
        GroupJoinRequest.status == 'pending'
    ).order_by(GroupJoinRequest.created_at.desc()).all()
    
    requests_data = []
    for req in pending_requests:
        requests_data.append({
            'id': req.id,
            'group_id': req.group_id,
            'group_name': req.group.name,
            'user_id': req.user_id,
            'user_name': req.user.name,
            'user_username': req.user.username,
            'user_pic': req.user.get_profile_pic_url(),
            'created_at': req.created_at.isoformat()
        })
    
    return jsonify({'success': True, 'requests': requests_data})

@app.route('/api/get-my-join-requests')
def get_my_join_requests():
    """Get all join requests sent by the current user"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    requests = GroupJoinRequest.query.filter_by(
        user_id=user_id
    ).order_by(GroupJoinRequest.created_at.desc()).all()
    
    requests_data = []
    for req in requests:
        requests_data.append({
            'id': req.id,
            'group_id': req.group_id,
            'group_name': req.group.name,
            'status': req.status,
            'created_at': req.created_at.isoformat(),
            'responded_at': req.responded_at.isoformat() if req.responded_at else None
        })
    
    return jsonify({'success': True, 'requests': requests_data})

@app.route('/api/get-unread-notifications')
def get_unread_notifications():
    """Get all unread notifications (messages + join requests)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = session['user_id']
    
    # Get unread messages count
    unread_messages_count = Message.query.filter_by(
        receiver_id=user_id,
        is_read=False
    ).count()
    
    # Get unread messages with sender info
    unread_messages = Message.query.filter_by(
        receiver_id=user_id,
        is_read=False
    ).order_by(Message.created_at.desc()).limit(5).all()
    
    messages_data = []
    for msg in unread_messages:
        messages_data.append({
            'id': msg.id,
            'type': 'message',
            'sender_id': msg.sender_id,
            'sender_name': msg.sender.name,
            'sender_pic': msg.sender.get_profile_pic_url(),
            'content': msg.content[:50] + '...' if len(msg.content) > 50 else msg.content,
            'created_at': msg.created_at.isoformat()
        })
    
    # Get pending join requests for groups user is admin of
    admin_groups = ChatGroupMember.query.filter_by(
        user_id=user_id,
        role='admin'
    ).all()
    
    admin_group_ids = [m.group_id for m in admin_groups]
    
    pending_requests = GroupJoinRequest.query.filter(
        GroupJoinRequest.group_id.in_(admin_group_ids),
        GroupJoinRequest.status == 'pending'
    ).order_by(GroupJoinRequest.created_at.desc()).limit(5).all()
    
    requests_count = len(pending_requests)
    
    requests_data = []
    for req in pending_requests:
        requests_data.append({
            'id': req.id,
            'type': 'join_request',
            'request_id': req.id,
            'group_id': req.group_id,
            'group_name': req.group.name,
            'user_id': req.user_id,
            'user_name': req.user.name,
            'user_pic': req.user.get_profile_pic_url(),
            'created_at': req.created_at.isoformat()
        })
    
    # Combine and sort by date
    all_notifications = messages_data + requests_data
    all_notifications.sort(key=lambda x: x['created_at'], reverse=True)
    
    return jsonify({
        'success': True,
        'unread_messages_count': unread_messages_count,
        'pending_requests_count': requests_count,
        'total_notifications': unread_messages_count + requests_count,
        'notifications': all_notifications
    })

@app.route('/api/save-quiz-result', methods=['POST'])
def save_quiz_result():
    """Save quiz result to database"""
    logger.debug('Save quiz result endpoint called')
    
    if 'user_id' not in session:
        logger.error('User not in session')
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    try:
        data = request.json
        score = data.get('score')
        time_taken = data.get('time_taken')
        answers = data.get('answers', {})
        
        logger.debug(f'Received data - Score: {score}, Time: {time_taken}, Answers keys: {list(answers.keys())}')
        
        if score is None:
            logger.error('Score is None')
            return jsonify({'success': False, 'error': 'Score is required'}), 400
        
        user_id = session['user_id']
        logger.debug(f'User ID: {user_id}')
        
        # Verify user exists
        user = User.query.get(user_id)
        if not user:
            logger.error(f'User {user_id} not found')
            return jsonify({'success': False, 'error': 'User not found'}), 404
        
        # Create a default quiz if none exists (for PDFs)
        quiz = Quiz.query.first()
        if not quiz:
            logger.info('Creating default quiz')
            quiz = Quiz(
                title='Generated Quiz',
                description='Quiz generated from uploaded PDF',
                subject='General',
                difficulty='medium',
                question_count=10,
                time_limit=300
            )
            db.session.add(quiz)
            db.session.flush()
            logger.debug(f'Default quiz created with ID: {quiz.id}')
        
        logger.debug(f'Using quiz ID: {quiz.id}')
        
        # Save quiz result with JSON serialized answers
        result = QuizResult(
            user_id=user_id,
            quiz_id=quiz.id,
            score=score,
            answers=json.dumps(answers),  # Serialize to JSON string
            time_taken=time_taken,
            completed_at=datetime.utcnow()
        )
        
        logger.debug(f'QuizResult object created - ID: {id(result)}')
        db.session.add(result)
        db.session.commit()
        
        logger.info(f'Quiz result SAVED - Result ID: {result.id}, User: {user_id}, Score: {score}%, Time: {time_taken}s')
        
        # Clear quiz questions from session
        if 'quiz_questions' in session:
            del session['quiz_questions']
            session.modified = True
            logger.debug('Cleared quiz_questions from session')
        
        return jsonify({
            'success': True,
            'message': 'Quiz result saved successfully!',
            'result_id': result.id
        }), 200
    except Exception as e:
        logger.error(f'Error saving quiz result: {str(e)}', exc_info=True)
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Error saving result: {str(e)}'}), 500

# ==================== DEBUG ENDPOINTS ====================

@app.route('/debug/db-status')
def debug_db_status():
    """Debug endpoint to check database status"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        user_id = session['user_id']
        user = User.query.get(user_id)
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Check QuizResult entries
        quiz_results = QuizResult.query.filter_by(user_id=user_id).all()
        total_in_db = QuizResult.query.count()
        
        results_data = []
        for result in quiz_results:
            results_data.append({
                'id': result.id,
                'user_id': result.user_id,
                'quiz_id': result.quiz_id,
                'score': result.score,
                'time_taken': result.time_taken,
                'completed_at': result.completed_at.isoformat() if result.completed_at else None,
                'answers_length': len(result.answers) if result.answers else 0
            })
        
        return jsonify({
            'success': True,
            'user_id': user_id,
            'user_name': user.name,
            'total_quiz_results_in_db': total_in_db,
            'user_quiz_results_count': len(quiz_results),
            'user_quiz_results': results_data,
            'user_stats': {
                'total_quizzes': user.get_total_quizzes(),
                'average_score': user.get_average_score(),
                'connection_count': user.get_connection_count()
            }
        })
    except Exception as e:
        logger.error(f'Error in debug endpoint: {str(e)}', exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve uploaded files (group chat images, brainstorm images, etc.)"""
    return send_from_directory('uploads', filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=app.debug)
