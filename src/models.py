# models.py

from .extensions import db
from flask_login import UserMixin

class Transcription(db.Model):
    __tablename__ = 'transcriptions'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    timestamp = db.Column(db.String, nullable=False)
    wav_filename = db.Column(db.String)
    transcript = db.Column(db.String, nullable=False)
    notified = db.Column(db.Integer, default=0)
    pushover_code = db.Column(db.Integer)
    response_code = db.Column(db.Integer)
    alert = db.Column(db.Boolean)
    bestof = db.Column(db.Boolean)

class DailyBlotter(db.Model):
    __tablename__ = 'daily_blotter'
    __table_args__ = {'extend_existing': True}

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date = db.Column(db.Date, unique=True, nullable=False)
    content = db.Column(db.Text, nullable=False)

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    google_id = db.Column(db.String, unique=True, nullable=False)
    name = db.Column(db.String, nullable=False)
    email = db.Column(db.String, nullable=False, unique=True)
    profile_pic = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    approved = db.Column(db.Boolean, default=False)
    roles = db.Column(db.String, nullable=False, default="user")



    def has_role(self, role):
        return role in self.roles.split(',')

    def add_role(self, role):
        roles = self.roles.split(',')
        if role not in roles:
            roles.append(role)
            self.roles = ','.join(roles)

    def remove_role(self, role):
        roles = self.roles.split(',')
        if role in roles:
            roles.remove(role)
            self.roles = ','.join(roles)

    def get_id(self):
        return str(self.id)

    def is_active(self):
        return self.approved

    def get_roles_string(self):
        return ','.join(self.roles)
    

class UserLogonAudit(db.Model):
    __tablename__ = 'user_logon_audit'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    ip_address = db.Column(db.String, nullable=False)
    user_agent = db.Column(db.String, nullable=False)
    success = db.Column(db.Boolean, nullable=False)
    logon_timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship('User', backref='logons')
