from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import boto3
import os
from dotenv import load_dotenv
import base64
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
import time
import openai
import requests
from sqlalchemy.exc import IntegrityError  # Import IntegrityError

app = Flask(__name__)
app.secret_key = '70ce371f656cc2e7da62aacf9a7ecb43'

# Configure the SQLAlchemy part of the app instance
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Create the SQLAlchemy db instance
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Flask-Login configuration
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# Load environment variables
load_dotenv()

# AWS configuration
aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
aws_region = os.getenv('AWS_REGION')

# Initialize S3 and Textract clients
s3 = boto3.client(
    's3',
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name=aws_region
)

textract = boto3.client(
    'textract',
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
    region_name=aws_region
)

openai.api_key = 'sk-proj-KobHfSS76kOWBGSYnJU9T3BlbkFJKJI4fD5nILrq3LoRij8D'


# User model
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/simulate')
def simulate():
    return render_template('simulate.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash('Logged in successfully.')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password.')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.')
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_password)
        try:
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful. You can now log in.')
            return redirect(url_for('login'))
        except IntegrityError:
            db.session.rollback()
            flash('Username already exists. Please choose a different username.')
    return render_template('register.html')


@app.route('/process-document', methods=['POST'])
@login_required
def process_document():
    try:
        data = request.get_json()
        file_content = data['fileContent']
        file_name = data['fileName']

        # Decode the base64 file content
        file_bytes = base64.b64decode(file_content)

        # Upload the file to S3
        bucket_name = 'my-textaract-bucket-2'
        s3_key = f'uploads/{file_name}'

        s3.put_object(Bucket=bucket_name, Key=s3_key, Body=file_bytes)
        print(f'File uploaded to S3: {s3_key}')

        # Start document text detection
        response = textract.start_document_text_detection(
            DocumentLocation={'S3Object': {'Bucket': bucket_name, 'Name': s3_key}}
        )
        job_id = response['JobId']
        print(f'Job started with ID: {job_id}')

        # Poll for the result
        result = get_job_results(job_id)
        return jsonify(result)
    except (NoCredentialsError, PartialCredentialsError) as e:
        return jsonify({'error': 'AWS credentials not found or incomplete.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def get_job_results(job_id):
    while True:
        response = textract.get_document_text_detection(JobId=job_id)
        status = response['JobStatus']
        if status == 'SUCCEEDED':
            return response
        elif status == 'FAILED':
            raise Exception('Text detection job failed')
        else:
            print('Job in progress, checking again in 5 seconds...')
            time.sleep(5)


@app.route('/correct-text', methods=['POST'])
@login_required
def correct_text():
    data = request.get_json()
    text = data['text']

    if text:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {openai.api_key}',
        }
        payload = {
            'model': 'gpt-4',
            'messages': [
                {"role": "system",
                 "content": "Correct any spelling errors in the following text without changing the grammar or adding or removing any words. Format the text using HTML tags to identify paragraphs, new lines, bullet points, headings, and subheadings. Wherever changes were made to the text put a red font color html tag and styling. Return the formatted text in HTML format:"},
                {"role": "user", "content": text}
            ],
            'n': 1,
            'stop': None,
            'temperature': 0.9,
        }
        response = requests.post('https://api.openai.com/v1/chat/completions', headers=headers, json=payload)
        response_data = response.json()
        corrected_text = response_data['choices'][0]['message']['content'].strip()
        return jsonify({'corrected_text': corrected_text})
    return jsonify({'error': 'No text provided'}), 400


@app.route('/evaluate-text', methods=['POST'])
@login_required
def evaluate_text():
    data = request.get_json()
    text = data['text']
    question = data['question']  # Get the question dynamically

    if text and question:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {openai.api_key}',
        }
        prompt = [
            {
                "role": "system",
                "content": f"""Evaluate the following descriptive answer based on the given criteria. Provide the feedback in two formats: textual and scores. The textual feedback should be detailed, explaining what was good, what wasn't good, and what can be improved, with examples if applicable. Be strict and detailed in your feedback. The scores should follow the provided marking scheme, and do not give scores more than 75%.

            Question: {question}


            Instructions/Parameters for textual feedback:

            1. Understand the Question: Deciphering the demand of the question.

            2. Word Limit: Should not be more than 20% more or less than the word limit indicated.

            3. Structure: The answer should be well-structured, generally following an introduction-body-conclusion format.

            4. Introduction: A brief introduction of the topic or defining the terms involved in the question. Present facts/data from authentic sources. Should be 10-15% of word limit.

            5. Body: The main discussion on the question. Write the answer in point format. Highlight the main keywords. Substantiate points with facts/data/examples wherever possible or required. Should be 70-80% of word limit.

            6. Conclusion: Provide a way forward by highlighting the issue or providing a solution. Highlight government initiatives, legislation, programs, or civil society initiatives. Should be 10-15% of word limit.

            7. Content: Ensure the content is factually correct and up-to-date. Backed by relevant data if needed. For subjective questions, consider multiple perspectives. Include government-released data/facts and government schemes wherever possible.

            8. Language: The language should be simple, clear, and grammatically correct.

            9. Presentation: Present points logically and coherently. Ensure a smooth flow of ideas. Use tables/flowcharts wherever applicable to enhance understanding and presentation.

            Instructions/Parameters for Marking Scheme (out of 100%):

            • Understanding of the Question (10%): Correct interpretation of the question and addressing all parts.

            • Content (40%): Relevance, depth, and breadth of knowledge. Inclusion of facts, examples, and case studies. Accuracy and up-to-date information.

            • Structure and Organization (20%): Logical flow of ideas. Clear introduction, body, and conclusion. Effective use of paragraphs and subheadings. Coherence and connectivity between points.

            • Analysis and Argumentation (20%): Critical analysis and reasoning. Balanced and objective viewpoints. Effective use of arguments to support the answer. Addressing counterarguments where relevant.

            • Language and Expression (10%): Clarity and conciseness. Proper grammar, spelling, and punctuation. Appropriate use of technical terms. Professional and formal tone.

            Also, provide three points for improvement mainly on structure and organisation and content, each point should be substantiated with examples and three reading material links on the topic. Provide feedback as key-value pairs in HTML format.

            Example format:
            {{
                "Feedback": "",
                "Scores": "",
                "Improvement": "",
                "Links": ""
            }}
            """
            }
            ,
            {"role": "user", "content": text}
        ]
        payload = {
            'model': 'gpt-4',
            'messages': prompt,
            'n': 1,
            'stop': None,
            'temperature': 0.9,
        }
        try:
            response = requests.post('https://api.openai.com/v1/chat/completions', headers=headers, json=payload)
            response_data = response.json()
            feedback = response_data['choices'][0]['message']['content'].strip()
            return jsonify({'feedback': feedback})
        except Exception as e:
            print(f'Error evaluating text: {e}')
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'No text or question provided'}), 400


if __name__ == '__main__':
    app.run(debug=True)