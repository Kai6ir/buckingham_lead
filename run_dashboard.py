"""Start the lead-bot dashboard. Open http://127.0.0.1:5057 in your browser."""

import webbrowser

from dashboard.app import create_app

if __name__ == "__main__":
    print("Dashboard running at http://127.0.0.1:5057  (Ctrl+C to quit)")
    webbrowser.open("http://127.0.0.1:5057")
    create_app().run(host="127.0.0.1", port=5057, debug=False, threaded=True)
