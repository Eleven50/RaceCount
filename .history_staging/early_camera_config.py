"""
Camera + network configuration.

Per the hardware spec, none of the connection parameters below should
change — but the password is intentionally NOT hardcoded. Set it via the
RACECOUNT_CAMERA_PASSWORD environment variable (e.g. in the systemd unit
file's Environment= line, or a local .env loaded before launch) so the
credential never sits in source control.
"""
import os

CAMERA_IP = "192.168.0.3"
CAMERA_PORT = 554
CAMERA_USER = "admin"

# Fallback only used if the env var isn't set — deliberately not a real
# credential. Override with: export RACECOUNT_CAMERA_PASSWORD='...'
_CAMERA_PASS_FALLBACK = "CHANGE_ME"

# Reolink's standard main-stream path. Some firmware versions expose
# "/Preview_01_main" without the "h264" prefix — if the stream fails to
# open, check the RTSP URL shown in the Reolink app's advanced settings
# and update RTSP_PATH below.
RTSP_PATH = "/h264Preview_01_main"

# Frames are downscaled to this width immediately after capture (aspect
# ratio preserved) before detection, overlay drawing, or MJPEG encoding.
# The Reolink P320 delivers 5MP frames — running every downstream stage
# at full resolution wastes CPU for no accuracy benefit. Raise this only
# if sheep are too small in-frame for reliable detection at your camera's
# mounting distance.
PROCESSING_WIDTH = 960


def get_camera_password() -> str:
    password = os.environ.get("RACECOUNT_CAMERA_PASSWORD", _CAMERA_PASS_FALLBACK)
    if password == _CAMERA_PASS_FALLBACK:
        import logging
        logging.getLogger("racecount.camera").warning(
            "RACECOUNT_CAMERA_PASSWORD is not set — using placeholder "
            "credential, which will fail to connect. Set the env var "
            "before running in production."
        )
    return password


def build_rtsp_url() -> str:
    password = get_camera_password()
    return f"rtsp://{CAMERA_USER}:{password}@{CAMERA_IP}:{CAMERA_PORT}{RTSP_PATH}"
