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
RUN pip install -r requirements.txt

# Bake BOTH driver names SeleniumBase can reach for, at the major version of
# the Chrome installed above, and fail the build if they disagree.
#
# This image ALREADY baked `chromedriver` (since e426ade, 2026-09-11) and a
# webrtc_peer run on 2026-09-22 still pulled 5,481,654 bytes from
# googlechromelabs.github.io between t=0.06 s and t=4.69 s -- 74.9% of every
# byte in the capture, at 10.15 Mbps against a 10 Mbps cap, inside the
# measurement window. The reason is the file NAME: UC mode does not use
# `chromedriver` at all. It looks for `uc_driver` in seleniumbase/drivers/
# (browser_launcher.py: `LOCAL_UC_DRIVER = DRIVER_DIR + "/uc_driver"`) and,
# not finding one at the browser's version, downloads a driver over whatever
# link the container happens to have -- here, the shaped bottleneck under
# measurement.
#
# SeleniumBase will copy `chromedriver` to `uc_driver` instead of downloading,
# but only when `use_version == ch_driver_version`, i.e. when the baked
# driver's major matches the installed Chrome's. Fetching the driver by that
# major, and checking it, is what makes the copy path reachable at all; baking
# `uc_driver` outright means not even the copy is needed.
#
# `--path` ALSO copies to /usr/local/bin (it is not instead of); the copy in
# seleniumbase/drivers/ is the one that decides whether a run fetches.
RUN set -eux; \
    CHROME_FULL="$(google-chrome --version | awk '{print $3}')"; \
    CHROME_MAJOR="${CHROME_FULL%%.*}"; \
    seleniumbase get chromedriver "$CHROME_MAJOR" --path; \
    seleniumbase get uc_driver "$CHROME_MAJOR"; \
    DRIVER_DIR="$(python3 -c 'import os, seleniumbase.drivers as d; print(os.path.dirname(d.__file__))')"; \
    for d in chromedriver uc_driver; do \
      test -x "$DRIVER_DIR/$d" \
        || { echo "BUILD FAILED: $DRIVER_DIR/$d was not installed"; exit 1; }; \
      V="$("$DRIVER_DIR/$d" --version | awk '{print $2}')"; \
      test "${V%%.*}" = "$CHROME_MAJOR" \
        || { echo "BUILD FAILED: $d is $V but Chrome is $CHROME_FULL;" \
                  "a run would fetch a driver over the shaped link"; exit 1; }; \
    done; \
    echo "drivers baked: chromedriver + uc_driver at major $CHROME_MAJOR (chrome $CHROME_FULL)"

COPY collect.py .
# collect.py imports these as siblings. cdp_bytes is a HARD import -- the
# try/except around it only retries with sys.path adjusted, then raises -- so
# omitting it builds an image whose collector dies at import. inpage_log is
# optional (the flag stays off without it) but belongs beside it.
COPY cdp_bytes.py .
COPY inpage_log.py .
# Bot peer for needs_peer apps (meet/teams/zoom) plus the Y4M clip and WAV tone it
# publishes as its camera and microphone. Same image as the collector so both
# share one build and one Chrome. make_test_audio is imported at module level
# by bot_peer.py, so it is required, not optional.
COPY make_test_clip.py .
COPY make_test_audio.py .
COPY bot_peer.py .

ENTRYPOINT ["python3", "collect.py"]
