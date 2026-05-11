from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import os
from flask_login import UserMixin
from sqlalchemy.sql import func

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    school = db.Column(db.String(100))
    profession = db.Column(db.String(100))
    study_level = db.Column(db.String(50))
    country = db.Column(db.String(100))
    password_hash = db.Column(db.String(200), nullable=False)
    profile_pic = db.Column(db.String(200), default='default.jpg')
    verification_code = db.Column(db.String(6))
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Push notification subscription (JSON string of the Web Push subscription object)
    push_subscription = db.Column(db.Text, nullable=True)

    # Relationships
    quiz_results = db.relationship('QuizResult', backref='user', lazy=True, cascade='all, delete-orphan')
    connections = db.relationship('Connection', foreign_keys='Connection.user_id', backref='user', lazy=True, cascade='all, delete-orphan')
    tags = db.relationship('UserTag', backref='user', lazy=True, cascade='all, delete-orphan')
    messages_sent = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True, cascade='all, delete-orphan')
    messages_received = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy=True, cascade='all, delete-orphan')
    generated_questions = db.relationship('GeneratedQuestion', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_profile_pic_url(self):
        if self.profile_pic != 'default.jpg':
            return f'/uploads/profiles/{self.profile_pic}'
        return '/static/image/KNOWITNOW.png'

    def get_average_score(self):
        avg = db.session.query(func.avg(QuizResult.score)).filter_by(user_id=self.id).scalar()
        return round(avg) if avg else 0

    def get_total_quizzes(self):
        return db.session.query(func.count(QuizResult.id)).filter_by(user_id=self.id).scalar() or 0

    def get_connection_count(self):
        return len(self.connections)

    def __repr__(self):
        return f'<User {self.username}>'


class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    subject = db.Column(db.String(100))
    difficulty = db.Column(db.String(20), default='medium')
    question_count = db.Column(db.Integer, default=0)
    time_limit = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    results = db.relationship('QuizResult', backref='quiz', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Quiz {self.title}>'


class QuizResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    answers = db.Column(db.Text)
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)
    time_taken = db.Column(db.Integer)

    def __repr__(self):
        return f'<QuizResult {self.user.username} - {self.score}%>'


class Connection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    connected_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    connected_user = db.relationship('User', foreign_keys=[connected_user_id], backref='received_connections')

    __table_args__ = (db.UniqueConstraint('user_id', 'connected_user_id', name='unique_connection'),)

    def __repr__(self):
        return f'<Connection {self.user.username} <-> {self.connected_user.username}>'


class UserTag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    tag = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'tag', name='unique_user_tag'),)

    def __repr__(self):
        return f'<UserTag {self.tag}>'


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Message from {self.sender.username} to {self.receiver.username}>'


class ChatGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_private = db.Column(db.Boolean, default=False)
    password_hash = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    members = db.relationship('ChatGroupMember', backref='group', lazy=True, cascade='all, delete-orphan')
    messages = db.relationship('GroupMessage', backref='group', lazy=True, cascade='all, delete-orphan')
    brainstorm_sessions = db.relationship('BrainstormSession', backref='group', lazy=True, cascade='all, delete-orphan')
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_groups')
    join_requests = db.relationship('GroupJoinRequest', backref='group', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        if password:
            self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return True
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<ChatGroup {self.name}>'


class ChatGroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(20), default='member')
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Mute notifications for this group
    is_muted = db.Column(db.Boolean, default=False)

    user = db.relationship('User', backref='group_memberships')

    __table_args__ = (db.UniqueConstraint('group_id', 'user_id', name='unique_group_member'),)

    def __repr__(self):
        return f'<ChatGroupMember {self.user.username} in {self.group.name}>'


class GroupMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(20), default='text')  # text, image, pdf, poll, ai, brainstorm_note, voice
    image_path = db.Column(db.String(500))
    pdf_path = db.Column(db.String(500))
    voice_path = db.Column(db.String(500))
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=True)
    # Reply threading
    reply_to_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=True)
    # Mentions: JSON array of user IDs mentioned (including 'brainai')
    mentions = db.Column(db.Text, nullable=True)
    # Reactions: JSON object {emoji: [user_id, ...]}
    reactions = db.Column(db.Text, nullable=True)
    is_edited = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    edited_at = db.Column(db.DateTime, nullable=True)

    sender = db.relationship('User', backref='group_messages')
    poll = db.relationship('Poll', backref='group_message', uselist=False)
    reply_to = db.relationship('GroupMessage', remote_side=[id], backref='replies')

    def __repr__(self):
        return f'<GroupMessage from {self.sender.username} in {self.group.name}>'


class Poll(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question = db.Column(db.String(500), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    group = db.relationship('ChatGroup', backref='polls')
    creator = db.relationship('User', backref='created_polls')
    options = db.relationship('PollOption', backref='poll', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Poll {self.question[:50]}>'


class PollOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    option_text = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    votes = db.relationship('PollVote', backref='option', lazy=True, cascade='all, delete-orphan')

    def get_vote_count(self):
        return len(self.votes)

    def __repr__(self):
        return f'<PollOption {self.option_text[:50]}>'


class PollVote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey('poll_option.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('poll_id', 'user_id', name='unique_user_poll_vote'),)

    def __repr__(self):
        return f'<PollVote by user {self.user_id} on option {self.option_id}>'


class BrainstormSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    scheduled_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='scheduled')  # scheduled, ongoing, completed
    # Shared whiteboard content (JSON)
    whiteboard_data = db.Column(db.Text, nullable=True)
    # Shared document content
    shared_doc = db.Column(db.Text, nullable=True)
    # Teacher (who can broadcast voice)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    teacher = db.relationship('User', foreign_keys=[teacher_id], backref='taught_sessions')

    notes = db.relationship('BrainstormNote', backref='session', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<BrainstormSession {self.title}>'


class BrainstormNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('brainstorm_session.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    note_type = db.Column(db.String(20), default='text')  # text, idea, question, resource, task
    color = db.Column(db.String(20), default='#ff4f30')   # sticky note colour
    mentions = db.Column(db.Text)
    tags = db.Column(db.Text)
    mention_ai = db.Column(db.Boolean, default=False)
    image_path = db.Column(db.String(500))
    textbook_path = db.Column(db.String(500))
    solved_problem = db.Column(db.Text)
    has_media = db.Column(db.Boolean, default=False)
    upvotes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='brainstorm_notes')

    def __repr__(self):
        return f'<BrainstormNote by {self.user.username}>'


class GroupJoinRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime)

    user = db.relationship('User', backref='group_join_requests')

    __table_args__ = (db.UniqueConstraint('group_id', 'user_id', name='unique_join_request'),)

    def __repr__(self):
        return f'<GroupJoinRequest {self.user.username} -> {self.group.name}>'


class GeneratedQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    options = db.Column(db.Text, nullable=False)
    correct_answer = db.Column(db.Text, nullable=False)
    explanation = db.Column(db.Text)
    source_hash = db.Column(db.String(64))
    difficulty = db.Column(db.String(20))
    question_type = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<GeneratedQuestion {self.question_text[:50]}...>'


class ConvertedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    original_filename = db.Column(db.String(200), nullable=False)
    converted_filename = db.Column(db.String(200), nullable=False)
    file_type = db.Column(db.String(20), nullable=False)
    file_path = db.Column(db.String(300), nullable=False)
    file_size = db.Column(db.Integer)
    duration = db.Column(db.String(20))
    conversion_settings = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='converted_files')


class TopicMastery(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    total_questions = db.Column(db.Integer, default=0)
    correct_answers = db.Column(db.Integer, default=0)
    attempts = db.Column(db.Integer, default=0)
    last_score = db.Column(db.Float, default=0.0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref='topic_masteries')

    __table_args__ = (db.UniqueConstraint('user_id', 'topic', name='unique_user_topic'),)

    @property
    def mastery_score(self):
        if self.total_questions == 0:
            return 0
        return round((self.correct_answers / self.total_questions) * 100)

    @property
    def level(self):
        s = self.mastery_score
        if self.attempts == 0: return 'not_started'
        if s < 60:  return 'learning'
        if s < 80:  return 'practicing'
        if s < 95:  return 'mastered'
        return 'expert'


class WrongAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(200), nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    correct_answer = db.Column(db.String(10))
    user_answer = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='wrong_answers')


# ── In-app notification system ────────────────────────────────────────────────
class AppNotification(db.Model):
    """In-app notifications for every relevant event."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    notif_type = db.Column(db.String(40), nullable=False)
    # Types: message, group_message, mention, brainai_mention, brainstorm_scheduled,
    #        brainstorm_starting, join_request, join_approved, join_rejected, reaction
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    link_type = db.Column(db.String(30))   # group, dm, brainstorm
    link_id = db.Column(db.Integer)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='notifications')

    def __repr__(self):
        return f'<AppNotification {self.notif_type} for user {self.user_id}>'


class HandRaise(db.Model):
    __tablename__ = 'hand_raise'
    id            = db.Column(db.Integer, primary_key=True)
    session_id    = db.Column(db.Integer, db.ForeignKey('brainstorm_session.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status        = db.Column(db.String(20), default='raised')
    question_text = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    answered_at   = db.Column(db.DateTime)

    user    = db.relationship('User', foreign_keys=[user_id], backref='hand_raises')
    session = db.relationship('BrainstormSession', foreign_keys=[session_id], backref='hand_raises')

    def __repr__(self):
        return f'<HandRaise {self.user_id} in session {self.session_id}>'
        id = db.Column(db.Integer, primary_key=True)
        session_id = db.Column(db.Integer, db.ForeignKey('brainstorm_session.id'), nullable=False)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        status = db.Column(db.String(20), default='raised')  # raised, acknowledged, answered, dismissed
        question_text = db.Column(db.Text, nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        answered_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', backref='hand_raises')
    session = db.relationship('BrainstormSession', backref='hand_raises')
    
class HandRaise(db.Model):
    __tablename__ = 'hand_raise'
    id            = db.Column(db.Integer, primary_key=True)
    session_id    = db.Column(db.Integer, db.ForeignKey('brainstorm_session.id'), nullable=False)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status        = db.Column(db.String(20), default='raised')
    question_text = db.Column(db.Text)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    answered_at   = db.Column(db.DateTime)

    user    = db.relationship('User', foreign_keys=[user_id])
    session = db.relationship('BrainstormSession', foreign_keys=[session_id])
    def __repr__(self):
        return f'<HandRaise {self.user.username} in session {self.session_id}>'

    def __repr__(self):
        return f'<AppNotification {self.notif_type} for user {self.user_id}>'