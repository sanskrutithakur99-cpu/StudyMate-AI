from flask import Flask, request, jsonify, session
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import sqlite3
import os
from google import genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")

CORS(
    app,
    supports_credentials=True,
    origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5173",
        "http://localhost:5173"
    ]
)

DATABASE = "studymate.db"


# Database connection
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


# Create database tables
def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()


# Home / server check
@app.route("/")
def home():
    return jsonify({
        "message": "StudyMate AI Backend is running!",
        "status": "success"
    })


# Register user
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}

    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        return jsonify({"error": "All fields are required"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    hashed_password = generate_password_hash(password)

    conn = get_db()

    try:
        cursor = conn.execute(
            "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
            (name, email, hashed_password)
        )
        conn.commit()

        session["user_id"] = cursor.lastrowid

        return jsonify({
            "message": "Account created successfully!",
            "name": name
        }), 201

    except sqlite3.IntegrityError:
        return jsonify({"error": "Email already registered"}), 409

    finally:
        conn.close()


# Login user
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    conn.close()

    if user and check_password_hash(user["password"], password):
        session["user_id"] = user["id"]

        return jsonify({
            "message": "Login successful!",
            "name": user["name"]
        })

    return jsonify({"error": "Invalid email or password"}), 401


# Check logged-in user
@app.route("/api/me")
def current_user():
    if "user_id" not in session:
        return jsonify({"error": "Please login first"}), 401

    conn = get_db()
    user = conn.execute(
        "SELECT id, name, email FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()
    conn.close()

    if not user:
        session.clear()
        return jsonify({"error": "User not found"}), 404

    return jsonify(dict(user))


# Logout
@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out successfully"})


# Get all notes
@app.route("/api/notes", methods=["GET"])
def get_notes():
    if "user_id" not in session:
        return jsonify({"error": "Please login first"}), 401

    conn = get_db()
    notes = conn.execute(
        "SELECT * FROM notes WHERE user_id = ? ORDER BY id DESC",
        (session["user_id"],)
    ).fetchall()
    conn.close()

    return jsonify([dict(note) for note in notes])


# Create note
@app.route("/api/notes", methods=["POST"])
def create_note():
    if "user_id" not in session:
        return jsonify({"error": "Please login first"}), 401

    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()

    if not title or not content:
        return jsonify({"error": "Title and content are required"}), 400

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO notes (user_id, title, content) VALUES (?, ?, ?)",
        (session["user_id"], title, content)
    )
    conn.commit()
    note_id = cursor.lastrowid
    conn.close()

    return jsonify({
        "message": "Note saved successfully!",
        "id": note_id
    }), 201


# Update note
@app.route("/api/notes/<int:note_id>", methods=["PUT"])
def update_note(note_id):
    if "user_id" not in session:
        return jsonify({"error": "Please login first"}), 401

    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()

    if not title or not content:
        return jsonify({"error": "Title and content are required"}), 400

    conn = get_db()
    cursor = conn.execute(
        """UPDATE notes SET title = ?, content = ?
           WHERE id = ? AND user_id = ?""",
        (title, content, note_id, session["user_id"])
    )
    conn.commit()
    updated = cursor.rowcount
    conn.close()

    if updated == 0:
        return jsonify({"error": "Note not found"}), 404

    return jsonify({"message": "Note updated successfully!"})


# Delete note
@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
def delete_note(note_id):
    if "user_id" not in session:
        return jsonify({"error": "Please login first"}), 401

    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM notes WHERE id = ? AND user_id = ?",
        (note_id, session["user_id"])
    )
    conn.commit()
    deleted = cursor.rowcount
    conn.close()

    if deleted == 0:
        return jsonify({"error": "Note not found"}), 404

    return jsonify({"message": "Note deleted successfully!"})

@app.route("/api/ai/quiz", methods=["POST"])
def ai_quiz():
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "").strip()

    if not topic:
        return jsonify({"error": "Please enter a quiz topic."}), 400

    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=f"""
            Create 5 multiple-choice quiz questions about {topic}.
            Return ONLY valid JSON in this format:
            {{
                "questions": [
                    {{
                        "question": "Question here",
                        "options": ["A", "B", "C", "D"],
                        "answer": 0
                    }}
                ]
            }}
            The answer must be the zero-based index of the correct option.
            """
        )

        import json
        result = json.loads(response.text)
        return jsonify(result), 200

    except Exception as e:
        print("Gemini Quiz Error:", e)
        return jsonify({"error": "Quiz generation failed. Please try again."}), 500
        
@app.route("/api/ai/chat", methods=["POST"])
def ai_chat():
    data = request.get_json(silent=True) or {}
    question = data.get("message", "").strip()

    if not question:
        return jsonify({"error": "Please enter a message."}), 400

    if not GEMINI_API_KEY:
        return jsonify({"error": "Gemini API key is missing."}), 500

    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=f"You are StudyMate AI, a helpful study assistant. Explain concepts simply and clearly for students.\n\nStudent: {question}"
        )

        return jsonify({"reply": response.text}), 200

    except Exception as e:
        print("Gemini Chat Error:", e)
        return jsonify({"error": "AI is temporarily unavailable. Please try again."}), 500

@app.route("/api/ai/summarize", methods=["POST"])
def ai_summarize():
    data = request.get_json(silent=True) or {}
    note_text = data.get("text", "").strip()

    if not note_text:
        return jsonify({"error": "Please enter some notes."}), 400

    if not GEMINI_API_KEY:
        return jsonify({"error": "Gemini API key is missing."}), 500

    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=f"Create a clear, easy-to-understand summary of these study notes. Include the key points:\n\n{note_text}"
        )

        return jsonify({"summary": response.text}), 200

    except Exception as e:
        print("Gemini Error:", e)
        return jsonify({"error": "AI summary could not be generated. Please try again."}), 500

        init_db()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
    
if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)