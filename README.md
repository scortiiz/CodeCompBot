# Bolt for Python Template App

This is a generic Bolt for Python template app used to build out Slack apps.

Before getting started, make sure you have a development workspace where you have permissions to install apps. If you don’t have one setup, go ahead and [create one](https://slack.com/create).
## Installation

#### Create a Slack App
1. Open [https://api.slack.com/apps/new](https://api.slack.com/apps/new) and choose "From an app manifest"
2. Choose the workspace you want to install the application to
3. Copy the contents of [manifest.json](./manifest.json) into the text box that says `*Paste your manifest code here*` (within the JSON tab) and click *Next*
4. Review the configuration and click *Create*
5. Click *Install to Workspace* and *Allow* on the screen that follows. You'll then be redirected to the App Configuration dashboard.

#### Environment Variables
Before you can run the app, you'll need to store some environment variables.

1. Open your apps configuration page from this list, click **OAuth & Permissions** in the left hand menu, then copy the Bot User OAuth Token. You will store this in your environment as `SLACK_BOT_TOKEN`.
2. Click **Basic Information** and copy the **Signing Secret** (required for HTTPS mode).

```zsh
# Bot token and signing secret (required)
export SLACK_BOT_TOKEN=<your-bot-token>
export SLACK_SIGNING_SECRET=<your-signing-secret>

# Challenge app specific
export CHALLENGE_CHANNEL_ID=<challenges-channel-id>
export REVIEW_CHANNEL_ID=<review-channel-id>
export SPREADSHEET_ID=<google-sheet-id>
export ADMIN_SLACK_IDS=U12345,U67890   # Comma-separated Slack user IDs for admins
export GOOGLE_APPLICATION_CREDENTIALS=service_account.json
```

### Setup Your Local Project
```zsh
# Clone this project onto your machine
git clone https://github.com/slack-samples/bolt-python-starter-template.git

# Change into this project directory
cd bolt-python-starter-template

# Setup your python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the dependencies
pip install -r requirements.txt

# Start your local server
python3 app.py
```

### HTTPS mode (production / deployed)

This app uses HTTPS (not Socket Mode). Configure your Slack app:

1. **Event Subscriptions** – Toggle *Enable Events* on. Set **Request URL** to your public HTTPS endpoint (e.g. `https://your-domain.com/slack/events`). Slack will verify it.
2. **Interactivity & Shortcuts** – Set **Request URL** to the same endpoint (Bolt handles all paths).
3. **Socket Mode** – Must be *disabled* in app settings (or in manifest `socket_mode_enabled: false`).

For production, run with gunicorn:

```zsh
gunicorn -b 0.0.0.0:$PORT app:app
```

Set `PORT` (default 3000). Bolt mounts at `/slack/events` by default.

For local development, use [ngrok](https://ngrok.com) to expose your port, then use the ngrok HTTPS URL as the Request URL in Slack.

#### Linting
```zsh
# Run flake8 from root directory for linting
flake8 *.py && flake8 listeners/

# Run black from root directory for code formatting
black .
```

#### Testing
```zsh
# Run pytest from root directory for unit testing
pytest .
```

## Message Commands

Type these messages in the challenge or review channel (as noted). All commands are case-insensitive.

### Public commands (Challenge or Review channel)

| Message | Description |
|---------|-------------|
| `standings` / `standing` / `leaderboard` / `leader` / `leaderbord` | List all teams and their points |
| `challenges left` / `challenge left` | Show challenges remaining for your team (excludes negative pts) |
| `challenges left [team name]` | Show challenges remaining for a specific team |
| `challenges left [pts]` or `challenges left [team] [pts]` | Filter by point value (e.g. `challenges left 3` for 3‑pt challenges) |
| `challenge randomize` / `challenge randomise` | Pick a random challenge no team has completed |
| `challenge randomize available` / `challenge randomize unclaimed` | Same as above |
| `challenge randomize team` / `challenge randomize my team` | Pick a random challenge your team hasn't completed |

### Challenge channel only (submissions)

| Message | Description |
|---------|-------------|
| `challenge [description]` | Submit a challenge (requires photo or video attachment) |

### Admin only

| Message | Where | Description |
|---------|-------|-------------|
| `admin submit [team] [description]` | Any channel | Submit on behalf of a team; assign challenge in Review modal (attach photo) |
| `reset semester` | Review channel | Clear ledger, submissions, and queue for new semester |
| `queue` / `resend queue` / `resend review queue` | Review channel | Post or refresh the review queue message (approve/reject replies thread under it) |
| `surprise [points] [challenge name] \| [optional prize]` | Review channel | Create a surprise challenge (e.g. `surprise 5 3+ show up to CodeSoccer \| free boba`) |

### Other actions

| Action | Description |
|--------|-------------|
| **Review** button (on queue message) | Open the review modal to approve/reject the next pending submission. Approve/reject replies appear as threads under the queue message. |
| **Add to review queue** (message shortcut) | Right‑click a message → shortcut → add to review queue for unmarked submissions (admin only) |

**Note:** Sheet headers must match: `slack_user_id`, `name`, `team` (Members); `challenge_key`, `challenge_name`, `points`, `min_num` (Challenges); `submission_id`, `created_at`, `slack_user_id`, `team`, `member_text`, `message_url`, `photo_url`, `status`, `challenge_key`, `points`, `reviewed_by` (Submissions); `timestamp`, `team`, `points_delta`, `challenge_key`, `submission_id`, `reviewed_by` (Ledger); `message_ts`, `channel_id` (Queue – one row for the single review queue message).

## Project Structure

### `manifest.json`

`manifest.json` is a configuration for Slack apps. With a manifest, you can create an app with a pre-defined configuration, or adjust the configuration of an existing app.

### `app.py`

`app.py` is the entry point for the application and is the file you'll run to start the server. This project aims to keep this file as thin as possible, primarily using it as a way to route inbound requests.

### `/listeners`

Every incoming request is routed to a "listener". Inside this directory, we group each listener based on the Slack Platform feature used, so `/listeners/shortcuts` handles incoming [Shortcuts](https://api.slack.com/interactivity/shortcuts) requests, `/listeners/views` handles [View submissions](https://api.slack.com/reference/interaction-payloads/views#view_submission) and so on.

## App Distribution / OAuth

Only implement OAuth if you plan to distribute your application across multiple workspaces. A separate `app_oauth.py` file can be found with relevant OAuth settings.

When using OAuth, Slack requires a public URL where it can send requests. In this template app, we've used [`ngrok`](https://ngrok.com/download). Checkout [this guide](https://ngrok.com/docs#getting-started-expose) for setting it up.

Start `ngrok` to access the app on an external network and create a redirect URL for OAuth. 

```
ngrok http 3000
```

This output should include a forwarding address for `http` and `https` (we'll use `https`). It should look something like the following:

```
Forwarding   https://3cb89939.ngrok.io -> http://localhost:3000
```

Navigate to **OAuth & Permissions** in your app configuration and click **Add a Redirect URL**. The redirect URL should be set to your `ngrok` forwarding address with the `slack/oauth_redirect` path appended. For example:

```
https://3cb89939.ngrok.io/slack/oauth_redirect
```
