#config.py

import os
basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
from datetime import timedelta

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(24)
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    SESSION_COOKIE_SECURE = False
        #that was for dev testing - set it back to true
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REDIS_URL = 'redis://localhost:6379'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
    LOG_FILE = '/var/log/gunicorn/dispatch-debug.log'
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(basedir, "transcriptions.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    BLOTTER_FILE_PATH = os.path.join(basedir, 'blotter.txt')
