import audioop
import base64
import json
from flask import Flask, request
from flask_sock import Sock, ConnectionClosed
from twilio.twiml.voice_response import VoiceResponse, Start
from twilio.rest import Client
from urllib.parse import quote
import websocket
import base64
from threading import Thread
from pydub import AudioSegment
import io
from urllib.parse import urlencode
import requests
from context import context
from data import data


ASSEMBLYAI_API_KEY = ""
ACCOUNT_SID = ""
AUTH_TOKEN = ""

BASE_URL = ""

app = Flask(__name__)
sock = Sock(app)
twilio_client = Client()

CL = '\x1b[0K'
BS = '\x08'



# Twilio Text to Speech on Call
def speak(text):

    client = Client(ACCOUNT_SID, AUTH_TOKEN)

    encoded_text = quote(text)

    # Update the ongoing call with the new TwiML URL
    try:
        global answering_question
        if answering_question:
            call = client.calls(call_sid).update(url=BASE_URL +"/response?text="+encoded_text, method='POST')
            print(f'Call updated with text: {text}')
            
        else:
            print(f'Not updating call with text: {text}')
    except Exception as e:
        print(f'Error updating call: {e}')
    

def ask(question):
    url = "https://lemur.assemblyai-solutions.com"
    global conversation_history
    payload = json.dumps({
    "question": question,
    "conversation_history": conversation_history,
    "context": context
    })
    headers = {
    'Content-Type': 'application/json',
    'Authorization': ASSEMBLYAI_API_KEY
    }
    response = requests.request("POST", url, headers=headers, data=payload)
    answer = response.text
    
    conversation_history.append({"question": question, "answer": answer})
    return answer

# AssemblyAI WebSocket Response Handler
def handle_assembly_messages(assembly_ws):
    current_statement = ""
    try:
        while True:
            message = assembly_ws.recv()
            if not message:
                break
            message = json.loads(message)

            if message["message_type"] == "SessionBegins":
                session_id = message["session_id"]
                expires_at = message["expires_at"]
                print(f"Session ID: {session_id}")
                print(f"Expires at: {expires_at}")
            elif message["message_type"] == "PartialTranscript":
                global answering_question
                if not answering_question:
                    if len(message['text']) > 0:
                        current_statement = message['text']
                        print(f"Partial transcript received: {message['text']}")
                    else:
                        if len(current_statement) > 0:
                            answering_question = True
                            print(f"Question: {current_statement}")
                            response = ask(current_statement)
                            speak(response)
                            current_statement = ""

    except websocket.WebSocketConnectionClosedException:
        print("WebSocket closed")
    except Exception as e:
        print(f"Error in handle_assembly_messages: {e}")


# Twilio Voice Request Handler
@app.route('/call', methods=['POST'])
def call():
    """Accept a phone call."""
    response = VoiceResponse()
    start = Start()
    start.stream(url=f'wss://{request.host}/stream')
    response.append(start)
    response.say("Hello, how can I help you?")
    response.pause(length=60)
    print(request.form.get("CallSid"))
    global call_sid
    call_sid = request.form.get("CallSid")
    print(f'Incoming call from {request.form["From"]}')
    return str(response), 200, {'Content-Type': 'text/xml'}


# Twilio Voice Request Handler
@app.route('/response', methods=['POST'])
def respond():
    """Accept a phone call."""
    response = VoiceResponse()
    start = Start()
    start.stream(url=f'wss://{request.host}/stream')
    response.append(start)
    response.say(request.args.get('text'))
    response.pause(length=60)
    return str(response), 200, {'Content-Type': 'text/xml'}

@sock.route('/stream')
def stream(ws):
    """Receive and transcribe audio stream."""

    global answering_question
    answering_question = False

    # AssemblyAI WebSocket connection
    sample_rate = 16000
    word_boost = ["Intuit", "AssemblyAI", "Garvan", "garvan"]
    params = {"sample_rate": sample_rate, "word_boost": json.dumps(word_boost)}
    assembly_ws = websocket.create_connection(
        f"wss://api.assemblyai.com/v2/realtime/ws?{urlencode(params)}",
        header={"Authorization": ASSEMBLYAI_API_KEY},
    )

    # Create a separate thread for handling incoming messages from AssemblyAI
    assembly_messages_thread = Thread(target=handle_assembly_messages, args=(assembly_ws,))
    assembly_messages_thread.start()

    audio_buffer = b""
    try:
        while True:
            message = ws.receive()
            packet = json.loads(message)
            if packet['event'] == 'start':
                print('Streaming is starting')
            elif packet['event'] == 'stop':
                print('\nStreaming has stopped')
            elif packet['event'] == 'media':
                audio = base64.b64decode(packet['media']['payload'])
                audio = audioop.ulaw2lin(audio, 2)
                audio = audioop.ratecv(audio, 2, 1, 8000, 16000, None)[0]
                audio_buffer += audio

                # Calculate the duration of the buffered audio data in milliseconds
                audio_segment = AudioSegment.from_file(io.BytesIO(audio_buffer), format="raw", sample_width=2, channels=1, frame_rate=16000)
                duration_ms = len(audio_segment)
                # If the buffered audio data's duration is within the acceptable range, send it to AssemblyAI
                if 120 <= duration_ms <= 2000:
                    # Send the audio data to AssemblyAI
                    payload = {
                        "audio_data": base64.b64encode(audio_buffer).decode("utf-8")
                    }
                    assembly_ws.send(json.dumps(payload))
                    audio_buffer = b""  # Clear the buffer

    except ConnectionClosed:
        print("Connection closed")
    finally:
        # Close the AssemblyAI WebSocket connection
        assembly_ws.close()
        # Wait for the AssemblyAI messages handling thread to finish
        assembly_messages_thread.join()

if __name__ == '__main__':
    call_sid = None
    conversation_history = []
    answering_question = False
    port = 5000
    number = twilio_client.incoming_phone_numbers.list()[0]
    print(f'Waiting for calls on {number.phone_number}')
    app.run(port=port)
  