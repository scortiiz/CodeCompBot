from slack_bolt import App
from .sample_view import sample_view_callback
from .reject_reason_view import reject_reason_view_callback


def register(app: App):
    app.view("sample_view_id")(sample_view_callback)
    app.view("reject_reason_modal")(reject_reason_view_callback)
