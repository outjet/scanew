from flask import Flask, redirect, url_for, request, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_session import Session
from authlib.integrations.flask_client import OAuth
from authlib.integrations.base_client.errors import MismatchingStateError
import logging
from .routes import dispatch_bp
from .extensions import db, login_manager
from dotenv import load_dotenv
load_dotenv() #Keep this above Config
from .web_config import Config  # <- ADD THIS
from .models import User

def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    import sys
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG)
    app.config.from_object(Config)  # <- USE THE CLASS HERE
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

    @app.route("/login")
    def login():
        session['next'] = request.args.get('next') or url_for('dispatch.view_transcriptions')
        redirect_uri = url_for("authorize", _external=True)
        return google.authorize_redirect(redirect_uri)

    @app.route("/authorize")
    def authorize():
        app.logger.debug("Starting /authorize")
        try:
            token = google.authorize_access_token()
            app.logger.debug(f"Received token: {token}")
            
            if not token:
                app.logger.error("No token received in authorize_access_token()")
                return "Authorization failed: No token received.", 400

            resp = google.get("https://www.googleapis.com/oauth2/v3/userinfo", token=token)
            app.logger.debug(f"Google userinfo response: {resp.status_code}")
            
            if resp.status_code != 200:
                app.logger.error(f"Failed to fetch userinfo: {resp.text}")
                return "Authorization failed: Could not fetch user info.", 500

            user_info = resp.json()
            app.logger.debug(f"User info: {user_info}")

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

            login_user(db_user)
            app.logger.info(f"Logged in user: {db_user.email}")

            return redirect(session.get("next", url_for("dispatch.view_transcriptions")))
        
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