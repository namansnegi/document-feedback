from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.from_object('app.config.Config')

    db.init_app(app)

    with app.app_context():
        from . import models
        db.create_all()

        from . import views
        app.register_blueprint(views.bp)

        from .job_processor import start_job_processor
        start_job_processor(app)

    return app
