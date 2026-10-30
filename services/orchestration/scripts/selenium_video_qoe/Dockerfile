FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

# Default environment for the virtual X display.
ENV DISPLAY=:99 \
    RESOLUTION=1920x1080x24 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps: Xvfb + fluxbox for a real (non-headless) rendering target,
# plus Chrome's runtime libs and Xlib/pyautogui support.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget unzip git xauth \
    xvfb fluxbox \
    libnss3 libxss1 libasound2 libatk-bridge2.0-0 libgtk-3-0 libgbm1 fonts-liberation \
    python3-xlib python3-tk scrot \
  && rm -rf /var/lib/apt/lists/*

# Install real Google Chrome (not vanilla Chromium) - required for Vimeo's
# MSE playback path to actually run.
RUN wget -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
  && apt-get update \
  && apt-get install -y --no-install-recommends /tmp/chrome.deb \
  && rm -f /tmp/chrome.deb \
  && rm -rf /var/lib/apt/lists/*

# Python deps: seleniumbase (undetected-chromedriver), selenium, pyautogui.
COPY requirements.txt .
RUN pip install -r requirements.txt \
  && seleniumbase get chromedriver --path

COPY collect.py .
# collect.py imports these as siblings. cdp_bytes is a HARD import -- the
# try/except around it only retries with sys.path adjusted, then raises -- so
# omitting it builds an image whose collector dies at import. inpage_log is
# optional (the flag stays off without it) but belongs beside it.
COPY cdp_bytes.py .
COPY inpage_log.py .
# Bot peer for needs_peer apps (meet/zoom) plus the Y4M clip and WAV tone it
# publishes as its camera and microphone. Same image as the collector so both
# share one build and one Chrome. make_test_audio is imported at module level
# by bot_peer.py, so it is required, not optional.
COPY make_test_clip.py .
COPY make_test_audio.py .
COPY bot_peer.py .

ENTRYPOINT ["python3", "collect.py"]
