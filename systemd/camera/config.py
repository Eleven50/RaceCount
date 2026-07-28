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

# "main" = full 5MP (2560x1920), ~5-6Mbps default bitrate, up to 30fps.
# "sub"  = much lower bitrate (roughly 256Kbps-1Mbps depending on
#          firmware), lower resolution, up to 15fps.
#
# Defaults to "sub" here because decoding the main stream in real time
# competes for CPU with YOLO inference on the same Pi — if that
# contention causes the decode thread to fall behind, FFmpeg can end up
# discarding a reference frame mid-GOP, which shows up as exactly the
# kind of "cabac decode failed" / "error while decoding MB" corruption
# H.264 produces when a P-frame loses the I-frame it depends on. The
# substream needs far less CPU to decode, which removes that failure
# mode rather than working around its symptoms.
#
# Tradeoff: substream resolution is meaningfully lower than main, so if
# sheep end up too small/blurry to detect reliably at your camera's
# mounting distance, that's the sign to switch back to "main" — and
# instead address the CPU contention from the other side (lower the
# main stream's own bitrate/framerate in the Reolink app's Encode
# settings, and/or export the YOLO model to ONNX per
# tools/export_model.py, which reduces inference's own CPU footprint).
STREAM_TYPE = "sub"  # "main" or "sub"

# Reolink's standard stream paths. Some firmware versions expose
# "/Preview_01_main" / "/Preview_01_sub" without the "h264" prefix — if
# the stream fails to open, check the RTSP URL shown in the Reolink
# app's advanced settings.
RTSP_PATH = f"/h264Preview_01_{STREAM_TYPE}"

# Frames are downscaled to this width immediately after capture (aspect
# ratio preserved) before detection, overlay drawing, or MJPEG encoding.
# Only takes effect if the source is wider than this — the substream is
# likely already narrower, in which case this is a no-op. Raise this
# only if sheep are too small in-frame for reliable detection at your
# camera's mounting distance.
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
