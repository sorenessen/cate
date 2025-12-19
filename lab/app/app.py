from flask import Flask, request, make_response

app = Flask(__name__)

@app.route("/")
def home():
    resp = make_response("<h1>Home</h1>")
    resp.set_cookie("session_id", "demo-session-123")
    return resp

@app.route("/login", methods=["GET", "POST"])
def login():
    return "<h1>Login Page</h1>"

@app.route("/admin")
def admin():
    return "<h1>Admin Area</h1>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

