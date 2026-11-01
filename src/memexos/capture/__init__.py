"""Capture: turning a link or a file into a raw source.

    fetch.py    read-only primitives (`memex fetch`): page text, PDF text, RSS entries, video metadata and subtitles.
                Agents that decide what to keep (admission, inbox research) get only these: no write operations exist.
    redact.py   scrub secrets before anything is written into the vault
    add.py      `memex add-url`: dispatch by link type and write the raw file
    video.py    `memex add-video`: subtitles, or local transcription when there are none
    asr.py      local speech-to-text (whisper.cpp), chunked so a language switch or a hallucination loop cannot spread
    visual.py   `memex add-visual`: when the information is on screen, sample frames and have a vision model read them
    cookies.py  export one site's login cookie from your browser for the tools that need it (optional plugins)
"""
