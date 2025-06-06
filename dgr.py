import asyncio
import json
from flask import Flask, render_template, jsonify
from flask_cors import CORS
from flask_sockets import Sockets
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler
from deepgram import DeepgramClient, DeepgramClientOptions, LiveTranscriptionEvents, LiveOptions

app = Flask(__name__)
CORS(app)
sockets = Sockets(app)

# --- ĐẶT THẲNG DEEPGRAM_API_KEY Ở ĐÂY ---
DEEPGRAM_API_KEY = "95e26fe061960fecb8fc532883f92af0641b89d0"
# ------------------------------------------------

if not DEEPGRAM_API_KEY:
    raise ValueError("Vui lòng đặt DEEPGRAM_API_KEY hợp lệ")

# Cấu hình Deepgram Client
config = DeepgramClientOptions(
    verbose=1,
    options={"keepalive": "true"}
)
deepgram_client = DeepgramClient(DEEPGRAM_API_KEY, config)

# Biến toàn cục để tạm lưu transcript
transcripts = []

@app.route('/')
def index():
    """
    Trả về trang HTML giao diện (dgr.html)
    """
    return render_template('dgr.html')


@sockets.route('/listen')
def listen_websocket(ws):
    """
    WebSocket endpoint:
    - Nhận audio chunks (bytes) từ client
    - Gửi sang Deepgram để STT
    - Gửi transcript ngược lại client
    """
    global transcripts
    transcripts = []  # reset mỗi kết nối

    print("--- Client vừa kết nối WebSocket ---")

    # Tạo asyncio loop riêng vì Deepgram SDK dùng asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Khởi tạo kết nối với Deepgram (phiên streaming)
    dg_connection = deepgram_client.listen.asynclive.v("1")
    options = LiveOptions(
        punctuate=True,
        language="vi",
        model="nova-2",
        encoding="linear16",
        sample_rate=16000,
        channels=1,
        interim_results=False,
        smart_format=True
    )

    try:
        loop.run_until_complete(dg_connection.start(options))
        print("--- Đã kết nối tới Deepgram ---")
    except Exception as e:
        print("Lỗi khi kết nối Deepgram:", e)
        return

    # Callback khi có transcript mới từ Deepgram
    def on_transcript(_, result, **kwargs):
        sentence = result.channel.alternatives[0].transcript
        if sentence:
            transcripts.append(sentence)
            try:
                ws.send(json.dumps({"transcript": sentence}))
            except Exception as send_err:
                print("Không gửi được dữ liệu qua WebSocket:", send_err)

    # Đăng ký event handlers cho Deepgram
    dg_connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
    dg_connection.on(LiveTranscriptionEvents.SpeechStarted, lambda *a, **k: print("Speech Started"))
    dg_connection.on(LiveTranscriptionEvents.SpeechEnded, lambda *a, **k: print("Speech Ended"))
    dg_connection.on(LiveTranscriptionEvents.Error, lambda *a, **k: print(f"Deepgram Error: {a[0]}"))
    dg_connection.on(LiveTranscriptionEvents.Open, lambda *a, **k: print("Deepgram connection opened"))
    dg_connection.on(LiveTranscriptionEvents.Close, lambda *a, **k: print("Deepgram connection closed"))

    # Nhận dữ liệu từ client
    try:
        while not ws.closed:
            message = ws.receive()  # blocking call
            if message is None:
                break

            # Nếu client gửi chuỗi "stop" (thao tác dừng ghi âm)
            if isinstance(message, str) and message == "stop":
                print("Client yêu cầu dừng streaming.")
                break

            # Nếu là bytes (audio chunk)
            if isinstance(message, (bytes, bytearray)):
                try:
                    loop.run_until_complete(dg_connection.send(message))
                except Exception as send_err:
                    print("Lỗi khi gửi audio lên Deepgram:", send_err)
            else:
                print("Nhận dữ liệu WS không xác định:", type(message))

    except Exception as e:
        print("Lỗi trong khi nhận dữ liệu WebSocket:", e)

    finally:
        # Khi client đóng WS hoặc gửi stop, kết thúc kết nối Deepgram
        if dg_connection and dg_connection.is_connected:
            try:
                loop.run_until_complete(dg_connection.finish())
                print("--- Đã đóng kết nối Deepgram ---")
            except Exception as finish_err:
                print("Lỗi khi finish Deepgram:", finish_err)

        loop.close()
        print("--- Kết nối WebSocket đã đóng hoàn toàn ---")


@app.route('/get_transcripts')
def get_transcripts():
    """
    Nếu client cần lấy toàn bộ transcript tạm lưu (nếu có)
    """
    return jsonify(transcripts=transcripts)


if __name__ == '__main__':
    """
    Khởi động server bằng gevent trên 0.0.0.0:<PORT> do Render cung cấp.
    """
    import os
    port = int(os.environ.get("PORT", 5000))
    print(f"Khởi động server bằng gevent trên 0.0.0.0:{port} ...")
    server = pywsgi.WSGIServer(
        ('0.0.0.0', port),
        app,
        handler_class=WebSocketHandler
    )
    server.serve_forever()
