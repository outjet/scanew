from flask import Flask, redirect, url_for, request, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_session import Session
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import MismatchingStateError
import logging
from .routes import dispatch_bp
from .extensions import db, login_manager
from dotenv import load_dotenv
import os
# Explicitly load .env from the project root
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)
from .web_config import Config  # <- ADD THIS
from .models import User
from werkzeug.middleware.proxy_fix import ProxyFix

def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    import sys
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG)
    app.config.from_object(Config)  # <- USE THE CLASS HERE
    app.logger.info(f"Loaded GOOGLE_CLIENT_ID: {app.config.get('GOOGLE_CLIENT_ID')}")
    app.logger.info(f"Loaded GOOGLE_CLIENT_SECRET: {app.config.get('GOOGLE_CLIENT_SECRET')[:5]}...") # Log only first 5 chars for security
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    app.config['SESSION_TYPE'] = 'filesystem'
    Session(app)

    oauth = OAuth(app)
    google = oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )

    app.register_blueprint(dispatch_bp, url_prefix="/")

    @app.context_processor
    def static_cache_buster():
        def static_url(filename: str, endpoint: str = "dispatch.static"):
            static_root = os.path.join(app.root_path, "static")
            file_path = os.path.join(static_root, filename)
            version = None
            try:
                version = int(os.path.getmtime(file_path))
            except OSError:
                version = None

            if version is None:
                return url_for(endpoint, filename=filename)
            return url_for(endpoint, filename=filename, v=version)

        return {"static_url": static_url}

    @app.route("/login")
    def login():
        session['next'] = request.args.get('next') or url_for('dispatch.view_transcriptions')
        redirect_uri = url_for("authorize", _external=True)
        app.logger.info(f"Redirecting to: {redirect_uri}")
        return google.authorize_redirect(redirect_uri)

    @app.route("/authorize")
    def authorize():
        app.logger.info("Starting /authorize")
        try:
            token = google.authorize_access_token()
            app.logger.info(f"Received token: {token}")

            if not token:
                app.logger.error("No token received in authorize_access_token()")
                return "Authorization failed: No token received.", 400

            resp = google.get("https://www.googleapis.com/oauth2/v3/userinfo", token=token)
            app.logger.info(f"Google userinfo response status: {resp.status_code}")

            if resp.status_code != 200:
                app.logger.error(f"Failed to fetch userinfo: {resp.text}")
                return "Authorization failed: Could not fetch user info.", 500

            user_info = resp.json()
            app.logger.info(f"User info: {user_info}")

            db_user = User.query.filter_by(google_id=user_info["sub"]).first()
            if not db_user:
                db_user = User(
                    google_id=user_info["sub"],
                    name=user_info["name"],
                    email=user_info["email"],
                    profile_pic=user_info.get("picture"),
                    approved=True,
                    roles="user"
                )
                db.session.add(db_user)
                db.session.commit()
                app.logger.info(f"Created new user: {db_user.email}")

            session.permanent = True
            login_user(db_user, remember=True)
            app.logger.info(f"Logged in user: {db_user.email}")

            return redirect(session.get("next", url_for("dispatch.view_transcriptions")))

        except MismatchingStateError as e:
            app.logger.error(f"Mismatched state error during OAuth: {e}")
            # It's common to redirect to login and show an error message
            return redirect(url_for('login'))
        except Exception as e:
            app.logger.exception("Error during OAuth authorization")
            return "Internal Server Error during authorization.", 500

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        session.pop("next", None)
        return redirect(url_for("login"))

    return app



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
