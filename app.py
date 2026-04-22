import os
import eventlet
eventlet.monkey_patch()
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'fallback_key_for_local_testing')
# New way: ONLY accepts connections from your exact Netlify website
CORS(app, resources={r"/*": {"origins": "https://creative-fox-3bfdbb.netlify.app"}})
socketio = SocketIO(app, cors_allowed_origins="https://creative-fox-3bfdbb.netlify.app")

# 2. Hide the Database URL
# It will look for 'MONGO_URI' on Render. If it can't find it, it crashes (which is safer than leaking!)
mongo_uri = os.environ.get('MONGO_URI') 
client = MongoClient(mongo_uri)
db = client['chat_app_db']
messages_collection = db['messages']
users_collection = db['users']

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
        "friends": [],
        "pending_requests": [],
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
            {"receiver": username},
            {"sender": username}
        ]
    }
    messages = list(messages_collection.find(query, {"_id": 0}))
    return jsonify(messages)

@app.route('/friends/<username>', methods=['GET'])
def get_friend_data(username):
    user = users_collection.find_one({"username": username})
    if not user:
        return jsonify({"error": "User not found"}), 404
        
    return jsonify({
        "friends": user.get('friends', []),
        "pending_requests": user.get('pending_requests', [])
    }), 200

@app.route('/friend_request', methods=['POST'])
def send_friend_request():
    data = request.json
    sender = data.get('sender')
    receiver = data.get('receiver')

    if sender == receiver:
        return jsonify({"error": "Cannot add yourself"}), 400

    target_user = users_collection.find_one({"username": receiver})
    if not target_user:
        return jsonify({"error": "User does not exist"}), 404

    if sender in target_user.get('pending_requests', []) or sender in target_user.get('friends', []):
        return jsonify({"error": "Request already sent or already friends"}), 400

    users_collection.update_one(
        {"username": receiver},
        {"$addToSet": {"pending_requests": sender}}
    )
    
    # Notify receiver in real-time
    socketio.emit('new_friend_request', {"from": sender}, to=receiver)
    
    return jsonify({"message": "Friend request sent!"}), 200

@app.route('/accept_request', methods=['POST'])
def accept_friend_request():
    data = request.json
    receiver = data.get('receiver') 
    sender = data.get('sender')     

    users_collection.update_one(
        {"username": receiver},
        {"$addToSet": {"friends": sender}, "$pull": {"pending_requests": sender}}
    )
    users_collection.update_one(
        {"username": sender},
        {"$addToSet": {"friends": receiver}}
    )

    # Notify both users in real-time
    socketio.emit('friend_request_accepted', {"with": sender}, to=receiver)
    socketio.emit('friend_request_accepted', {"with": receiver}, to=sender)
    
    return jsonify({"message": "Request accepted!"}), 200

@socketio.on('connect')
def handle_connect():
    pass

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in active_users:
        disconnected_user = active_users.pop(request.sid)
        emit('update_active_users', list(set(active_users.values())), broadcast=True)

@socketio.on('user_joined')
def handle_user_joined(data):
    username = data.get('username')
    join_room(username)
    active_users[request.sid] = username
    emit('update_active_users', list(set(active_users.values())), broadcast=True)

@socketio.on('request_clear')
def handle_clear():
    username = active_users.get(request.sid)
    if username:
        messages_collection.delete_many({
            "$or": [{"sender": username}, {"receiver": username}]
        })
    emit('chat_cleared', {"status": "success"}, to=request.sid)

@socketio.on('send_message')
def handle_new_message(data):
    sender = data.get('sender')
    receiver = data.get('receiver')
    
    sender_data = users_collection.find_one({"username": sender})
    
    if receiver not in sender_data.get('friends', []):
        emit('receive_message', {
            "sender": "System", 
            "content": f"You are not friends with {receiver}.", 
            "receiver": sender
        }, to=request.sid)
        return

    data['timestamp'] = datetime.utcnow().isoformat()
    data['seen'] = False  # Start as unseen
    
    messages_collection.insert_one(data.copy())
    
    emit('receive_message', data, to=receiver)
    if sender != receiver:
        emit('receive_message', data, to=sender)

@socketio.on('message_seen')
def handle_message_seen(data):
    sender = data.get('sender')
    receiver = data.get('receiver')

    messages_collection.update_many(
        {"sender": sender, "receiver": receiver, "seen": {"$ne": True}},
        {"$set": {"seen": True}}
    )
    emit('messages_marked_seen', {"seen_by": receiver}, to=sender)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)