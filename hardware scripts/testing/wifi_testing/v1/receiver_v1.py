
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from datetime import datetime


class TelemetryHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        # Only accept telemetry at the expected endpoint
        if self.path != "/telemetry":
            self.send_error(404, "Unknown endpoint")
            return

        # Read the incoming HTTP request body
        content_length = int(self.headers.get("Content-Length", 0))
        raw_data = self.rfile.read(content_length)

        try:
            # Decode JSON
            data = json.loads(raw_data.decode("utf-8"))

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            print("\n========== TELEMETRY RECEIVED ==========")
            print(f"Received at: {timestamp}")
            print(f"Device ID:   {data.get('device_id')}")
            print(f"Sequence:    {data.get('sequence')}")
            print(f"Temperature: {data.get('temperature_c')} C")
            print(f"Vibration:   {data.get('vibration_rms_g')} g")
            print("========================================")

            # Tell the Heltec that the message was received
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            response = json.dumps({
                "success": True,
                "message": "Telemetry received"
            })

            self.wfile.write(response.encode("utf-8"))

        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400, "Invalid JSON")

    def log_message(self, format, *args):
        # Suppress default HTTP request logging
        pass


HOST = "0.0.0.0"
PORT = 5000

server = HTTPServer((HOST, PORT), TelemetryHandler)

print(f"MachineGuard receiver listening on port {PORT}")
print("Waiting for telemetry...\n")

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nShutting down receiver...")
    server.server_close()

