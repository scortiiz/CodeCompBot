from slack_bolt import App
from .sample_shortcut import sample_shortcut_callback
from .add_to_review_queue_shortcut import add_to_review_queue_callback


def register(app: App):
    app.shortcut("sample_shortcut_id")(sample_shortcut_callback)
    app.shortcut("add_to_review_queue_shortcut")(add_to_review_queue_callback)
