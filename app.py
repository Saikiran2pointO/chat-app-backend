import eventlet
eventlet.monkey_patch()
from flask import Flask, jsonify, request
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime


app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev_key_123'
socketio = SocketIO(app, cors_allowed_origins="*")

client = MongoClient('mongodb+srv://chat_admin:mg160426@cluster0.xsdf5ih.mongodb.net/?appName=Cluster0')
db = client['chat_app_db']
messages_collection = db['messages']
users_collection = db['users']

# ---> NEW: Dictionary to track who is online (Session ID -> Username) <---
active_users = {}

@app.route('/register', methods=['POST'])
def register_user():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    if users_collection.find_one({"username": username}):
        return jsonify({"error": "Username already taken"}), 400

    hashed_password = generate_password_hash(password)
    users_collection.insert_one({
        "username": username,
        "password": hashed_password,
        "created_at": datetime.utcnow().isoformat()
    })
    return jsonify({"message": "Registration successful"}), 201

@app.route('/login', methods=['POST'])
def login_user():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    user = users_collection.find_one({"username": username})
    if not user or not check_password_hash(user['password'], password):
        return jsonify({"error": "Invalid username or password"}), 401

    return jsonify({"message": "Login successful"}), 200

@app.route('/history/<username>', methods=['GET'])
def get_history(username):
    query = {
        "$or": [
            {"receiver": "Global"},
            {"receiver": {"$exists": False}},
            {"receiver": username},
            {"sender": username, "receiver": {"$ne": "Global"}}
        ]
    }
    messages = list(messages_collection.find(query, {"_id": 0}))
    return jsonify(messages)

@app.route('/clear', methods=['DELETE'])
def clear_history():
    messages_collection.delete_many({})
    socketio.emit('chat_cleared', broadcast=True)
    return jsonify({"status": "cleared"})

@socketio.on('connect')
def handle_connect():
    print("🟢 A client connected!")

# ---> UPDATED: Handle disconnections and update the active list <---
@socketio.on('disconnect')
def handle_disconnect():
    print("🔴 A client disconnected!")
    # If the disconnected user was logged in, remove them from the active list
    if request.sid in active_users:
        disconnected_user = active_users.pop(request.sid)
        print(f"👋 {disconnected_user} left.")
        # Broadcast the updated list of unique usernames
        emit('update_active_users', list(set(active_users.values())), broadcast=True)

# ---> UPDATED: Add user to active list when they log in <---
@socketio.on('user_joined')
def handle_user_joined(data):
    username = data.get('username')
    join_room(username)
    
    # Map their unique connection ID to their username
    active_users[request.sid] = username
    
    # Broadcast the updated list to everyone
    emit('update_active_users', list(set(active_users.values())), broadcast=True)

@socketio.on('request_clear')
def handle_clear_request():
    messages_collection.delete_many({})
    emit('chat_cleared', {'status': 'success'}, broadcast=True)

@socketio.on('send_message')
def handle_new_message(data):
    data['timestamp'] = datetime.utcnow().isoformat()
    receiver = data.get('receiver', 'Global')
    
    messages_collection.insert_one(data.copy())
    
    if receiver == 'Global':
        emit('receive_message', data, broadcast=True)
    else:
        emit('receive_message', data, to=receiver)
        if data['sender'] != receiver:
            emit('receive_message', data, to=data['sender'])

if __name__ == '__main__':
    print("🚀 Starting server on http://localhost:5000...")
    socketio.run(app, debug=True, port=5000)