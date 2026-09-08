from flask import Flask, jsonify, render_template, request

app = Flask(
    __name__,
    template_folder="../templates",
    static_folder="../static",
)


def get_client_ip():
    """Return the first proxy-provided client address when available."""
    for header in (
        "CF-Connecting-IP",
        "X-Vercel-Forwarded-For",
        "X-Forwarded-For",
        "X-Real-IP",
        "True-Client-IP",
    ):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.remote_addr


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/connection")
def connection():
    return jsonify({"ip": get_client_ip()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
