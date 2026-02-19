from slack_bolt import App
from .sample_action import sample_action_callback
from .approve_action import approve_submission_callback
from .review_modal_action import (
    open_review_modal_callback,
    challenge_select_callback,
    extra_points_select_callback,
    review_accept_callback,
    review_reject_callback,
)


def register(app: App):
    app.action("sample_action_id")(sample_action_callback)
    app.action("approve_submission")(approve_submission_callback)
    app.action("open_review_modal")(open_review_modal_callback)
    app.action("challenge_select")(challenge_select_callback)
    app.action("extra_points_select")(extra_points_select_callback)
    app.action("review_accept")(review_accept_callback)
    app.action("review_reject")(review_reject_callback)
