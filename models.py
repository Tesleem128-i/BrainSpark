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

    # Relationships
    quiz_results = db.relationship('QuizResult', backref='user', lazy=True, cascade='all, delete-orphan')
    connections = db.relationship('Connection', foreign_keys='Connection.user_id', backref='user', lazy=True, cascade='all, delete-orphan')
    tags = db.relationship('UserTag', backref='user', lazy=True, cascade='all, delete-orphan')
    messages_sent = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True, cascade='all, delete-orphan')
    messages_received = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_profile_pic_url(self):
        if self.profile_pic != 'default.jpg':
            return f'/static/uploads/profiles/{self.profile_pic}'
        return '/static/image/KNOWITNOW.png'

    def get_average_score(self):
        """Calculate average score from all quizzes"""
        avg = db.session.query(func.avg(QuizResult.score)).filter_by(user_id=self.id).scalar()
        return round(avg) if avg else 0

    def get_total_quizzes(self):
        """Get total number of quizzes completed"""
        return db.session.query(func.count(QuizResult.id)).filter_by(user_id=self.id).scalar() or 0

    def get_connection_count(self):
        """Get number of connections"""
        return len(self.connections)

    def __repr__(self):
        return f'<User {self.username}>'


class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    subject = db.Column(db.String(100))
    difficulty = db.Column(db.String(20), default='medium')  # easy, medium, hard
    question_count = db.Column(db.Integer, default=0)
    time_limit = db.Column(db.Integer)  # in seconds
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    results = db.relationship('QuizResult', backref='quiz', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Quiz {self.title}>'


class QuizResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)  # percentage
    answers = db.Column(db.Text)  # JSON format of answers
    completed_at = db.Column(db.DateTime, default=datetime.utcnow)
    time_taken = db.Column(db.Integer)  # in seconds

    def __repr__(self):
        return f'<QuizResult {self.user.username} - {self.score}%>'


class Connection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    connected_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationship for the connected user
    connected_user = db.relationship('User', foreign_keys=[connected_user_id], backref='received_connections')

    __table_args__ = (db.UniqueConstraint('user_id', 'connected_user_id', name='unique_connection'),)

    def __repr__(self):
        return f'<Connection {self.user.username} <-> {self.connected_user.username}>'


class UserTag(db.Model):
    """Tags for user interests/subjects"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    tag = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'tag', name='unique_user_tag'),)

    def __repr__(self):
        return f'<UserTag {self.tag}>'


class Message(db.Model):
    """Direct messages between connected users"""
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Message from {self.sender.username} to {self.receiver.username}>'


class ChatGroup(db.Model):
    """Chat groups for brainstorming and collaboration"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_private = db.Column(db.Boolean, default=False)
    password_hash = db.Column(db.String(200))  # For private groups
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
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
    """Members of a chat group"""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    role = db.Column(db.String(20), default='member')  # admin, moderator, member
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='group_memberships')
    
    __table_args__ = (db.UniqueConstraint('group_id', 'user_id', name='unique_group_member'),)
    
    def __repr__(self):
        return f'<ChatGroupMember {self.user.username} in {self.group.name}>'


class GroupMessage(db.Model):
    """Messages in a chat group"""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    sender = db.relationship('User', backref='group_messages')
    
    def __repr__(self):
        return f'<GroupMessage from {self.sender.username} in {self.group.name}>'


class BrainstormSession(db.Model):
    """Brainstorm sessions scheduled within a group"""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    scheduled_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='scheduled')  # scheduled, ongoing, completed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    notes = db.relationship('BrainstormNote', backref='session', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<BrainstormSession {self.title}>'


class BrainstormNote(db.Model):
    """Notes taken during brainstorm sessions - with rich content support"""
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('brainstorm_session.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)  # Main note text
    mentions = db.Column(db.Text)  # JSON array of mentioned user IDs
    tags = db.Column(db.Text)  # JSON array of topic tags
    mention_ai = db.Column(db.Boolean, default=False)  # Whether AI was tagged
    image_path = db.Column(db.String(500))  # Path to uploaded image
    textbook_path = db.Column(db.String(500))  # Path to textbook file
    solved_problem = db.Column(db.Text)  # Solution/working for a problem
    has_media = db.Column(db.Boolean, default=False)  # Whether note has images/attachments
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='brainstorm_notes')
    
    def __repr__(self):
        return f'<BrainstormNote by {self.user.username}>'


class GroupJoinRequest(db.Model):
    """Join requests for private groups"""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('chat_group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime)
    
    user = db.relationship('User', backref='group_join_requests')
    
    __table_args__ = (db.UniqueConstraint('group_id', 'user_id', name='unique_join_request'),)
    
    def __repr__(self):
        return f'<GroupJoinRequest {self.user.username} -> {self.group.name}>'

