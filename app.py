import os
from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import requests
from dotenv import load_dotenv
from openai import OpenAI
import time
from threading import Thread

app = Flask(__name__)

load_dotenv()

LISTEN_CHANNEL_ID = os.getenv("LISTEN_CHANNEL_ID")
MESSAGE_CHANNEL_ID = os.getenv("MESSAGE_CHANNEL_ID")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# Set up the clients
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
slack_client = WebClient(token=SLACK_BOT_TOKEN)

# Set up the model
transcription_model = "whisper-1"
summary_model = "gpt-4-turbo"

# Set up the summary system prompts
summary_system_prompt = """
You are an expert meeting summarizer. 
Format your output in Slack-compatible markdown with the following sections:

*Action Items*
*Commitments*
*Summary of Key Points*

Be concise. Use bullet points.
"""

@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.get_json()

    # Step 1: Handle Slack challenge
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})

    # Step 2: Process file_shared events
    if data.get("type") == "event_callback":
        event = data["event"]
        if event["type"] == "file_shared":
            thread = Thread(target=handle_file_shared, args=(event,))
            thread.start()

    return jsonify({"status": "ok"})


def file_shared_in_target_channel(file_info, target_channel_id):
    shares = file_info.get("shares", {})
    for share_type in shares.values():  # e.g., 'public' or 'private'
        for channel_id, _ in share_type.items():
            if channel_id == target_channel_id:
                return True
    return False


def handle_file_shared(event):
    file_id = event["file_id"]
    try:
        # Wait for Slack to finish indexing the file
        for attempt in range(5):
            file_info = slack_client.files_info(file=file_id)["file"]
            filetype = file_info.get("filetype")

            if file_shared_in_target_channel(file_info, LISTEN_CHANNEL_ID):
                break
            
            print(f"Attempt {attempt+1}: File not in target channel yet.")
            time.sleep(1)
        else:
            print("❌ File not found in target channel after 5 attempts.")
            return

        if filetype != "mp3":
            print("Not an mp3 — skipping.")
            return

        # Download the file
        file_url = file_info["url_private_download"]
        headers = {"Authorization": f"Bearer {SLACK_BOT_TOKEN}"}
        response = requests.get(file_url, headers=headers)

        filename = f"meeting_{file_id}.mp3"
        with open(filename, "wb") as f:
            f.write(response.content)

        print("✅ MP3 downloaded. Ready for processing.")

        # Transcribe the audio file
        with open(filename, "rb") as audio_file:
            transcription = openai_client.audio.transcriptions.create(
                model=transcription_model,
                file=audio_file
            )
            print("✅ Transcription complete.")

        # Summarize the transcription
        response = openai_client.chat.completions.create(
            model=summary_model,
            messages=[
                {"role": "system", "content": summary_system_prompt},
                {"role": "user", "content": transcription.text}
            ]
        )
        print("✅ Summary complete.")

        # Post the summary to the channel
        slack_client.chat_postMessage(
            channel=MESSAGE_CHANNEL_ID,
            text=response.choices[0].message.content
        )
        print("✅ Summary posted to channel.")

    except SlackApiError as e:
        print(f"Slack API error: {e.response['error']}")


if __name__ == "__main__":
    app.run(port=5000)
