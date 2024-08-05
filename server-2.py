from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_pymongo import PyMongo
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
from bson import ObjectId
from bson.binary import Binary
import json
from utils import extract_text_from_blocks, convert_html_to_text, clean_and_convert


# Load environment variables
load_dotenv()


app = Flask(__name__)


# Configure the SQLAlchemy part of the app instance
app.config["MONGO_URI"] = "mongodb+srv://admin:1234@cluster0.dicy2zo.mongodb.net/main-db?retryWrites=true&w=majority"
mongo = PyMongo(app)

# Flask-Login configuration
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'



# AWS configuration
aws_access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
aws_region = os.getenv('AWS_REGION')
open_ai_key = os.getenv('OPENAI_API_KEY')
app_secret_key = os.getenv('APP_SECRET_KEY')


app.secret_key = app_secret_key
openai.api_key = open_ai_key


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



# User model
class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

    @staticmethod
    def find_by_username(username):
        user = mongo.db.users.find_one({"username": username})
        if user:
            return User(id=str(user["_id"]), username=user["username"], password_hash=user["password"])
        return None

    @staticmethod
    def find_by_id(user_id):
        try:
            user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
            if user:
                return User(id=str(user["_id"]), username=user["username"], password_hash=user["password"])
        except:
            return None

    @staticmethod
    def create(username, password_hash):
        mongo.db.users.insert_one({"username": username, "password": password_hash})

    def get_id(self):
        return str(self.id)

    @staticmethod
    def create(username, password_hash):
        mongo.db.users.insert_one({"username": username, "password": password_hash})

    def get_id(self):
        return str(self.id)


@login_manager.user_loader
def load_user(user_id):
    return User.find_by_id(user_id)


@app.route('/')
def index():
    return render_template('index-2.html')



@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.find_by_username(username)
        if user and check_password_hash(user.password_hash, password):
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
        if User.find_by_username(username):
            flash('Username already exists. Please choose a different username.', 'danger')
        else:
            User.create(username, hashed_password)
            flash('Registration successful. You can now log in.', 'success')
            return redirect(url_for('login'))
    return render_template('register_new.html')



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
        mongo.db.pdfs.insert_one({
            'user_id': current_user.get_id(),
            'file_name': file_name,
            'file_content': Binary(file_bytes),
            's3_key': s3_key,
            'evaluation_result': None,
            'uploaded_on': time.time()
        })
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
        blocks = extract_text_from_blocks(result['Blocks'])
        blocks_result = correct_text(blocks)
        evaluation_result = evaluate_multi_text(blocks_result['corrected_text'])

        # Update the MongoDB document with the evaluation result
        mongo.db.pdfs.update_one(
            {'user_id': current_user.get_id(), 's3_key': s3_key},
            {'$set': {'evaluation_result': evaluation_result["feedback"]}}
        )
        return evaluation_result
    except (NoCredentialsError, PartialCredentialsError) as e:
        return jsonify({'error': 'AWS credentials not found or incomplete.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def correct_text(text):
    try:
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
        return {'corrected_text': corrected_text}
    except Exception as e:
        return {'error': str(e)}



def evaluate_multi_text(text):
    text = convert_html_to_text(text)
    question = "What is Secularism? How is Indian secularism different from Western Secularism?"
    if text and question:
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {openai.api_key}',
        }
        prompt = [
            {
                "role": "system",
                "content": f"""Evaluate the following descriptive text to identify the questions and answers. For each identified question and answer pair, provide detailed feedback and scores. The feedback should be detailed, explaining what was good, what wasn't good, and what can be improved, with examples if applicable. Be strict and detailed in your feedback. The scores should follow the provided marking scheme, and do not give scores more than 75%.

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

                Also, provide three points for improvement mainly on structure and organization and content, each point should be substantiated with examples and three reading material links on the topic. Provide feedback as a list of JSON objects.

                Example format for each pair:
                [
                    {{
                        "Question": "Identified question",
                        "Answer": "Identified answer",
                        "Feedback": "Detailed feedback",
                        "Scores": {{
                            "Understanding of the Question": 0-10,
                            "Content": 0-40,
                            "Structure and Organization": 0-20,
                            "Analysis and Argumentation": 0-20,
                            "Language and Expression": 0-10
                        }},
                        "Improvement": {{
                            "1": "First point for improvement with examples",
                            "2": "Second point for improvement with examples",
                            "3": "Third point for improvement with examples"
                        }},
                        "Links": [
                            "http://example1.com",
                            "http://example2.com",
                            "http://example3.com"
                        ]
                    }},
                    // Additional question-answer pairs
                ]

                Text to evaluate:
                {text}
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
            feedback_list = clean_and_convert(feedback)
            return {'feedback': feedback_list}
        except Exception as e:
            print(f'Error evaluating text: {e}')
            return {'error': str(e)}
    return {'error': 'No text or question provided'}


@app.route('/get-documents', methods=['GET'])
@login_required
def get_documents():
    try:
        user_id = current_user.get_id()

        # Query the MongoDB 'pdfs' collection for documents belonging to the current user
        pdf_documents = mongo.db.pdfs.find({'user_id': user_id})

        # Function to calculate days ago
        def days_ago(timestamp):
            return (time.time() - timestamp) // (24 * 3600)

        # Format the documents data
        files = [
            {
                'id': str(doc['_id']),
                'name': doc['file_name'],
                'days_ago': days_ago(doc['uploaded_on']),
                'status': 'loaded' if doc['evaluation_result'] else 'loading'
            }
            for doc in pdf_documents
        ]

        return jsonify(files)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/document/<document_id>', methods=['GET'])
def get_document(document_id):
    try:
        document = mongo.db.pdfs.find_one({'_id': ObjectId(document_id)})
        if document:
            file_content_base64 = base64.b64encode(document['file_content']).decode('utf-8')
            return render_template('display_pdf.html', document=document, file_content_base64=file_content_base64)
        else:
            return jsonify({'error': 'Document not found or access denied'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500



if __name__ == '__main__':
    app.run(debug=True)