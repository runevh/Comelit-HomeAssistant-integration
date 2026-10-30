# Vendored video transport

Source: https://github.com/jfmlima/comelit-vip
Revision: 4c1cba401030c705132174e60fb3886f667a442f
Files: viper/{call,ctp,session,frames,models,errors,tasks,rtp,rtsp}.py
License: MIT, Copyright (c) 2026 Jorge Lima; retained in LICENSE.

This is a narrow snapshot of the standalone video transport, not the upstream
Home Assistant integration. Comelit Local wraps it with command restrictions,
authentication, bounded setup/cleanup, and credential-free error reporting.
The upstream loggers are disabled for these sessions because they can include
raw configuration or replies. Only the wrapper is used by the integration.

Local modifications: rtsp.py skips interleaved RTCP frames while reading RTSP
requests, accepts bounded client limits for all peers, bounds idle clients, and waits for current H.264 parameter sets plus an IDR
before forwarding to a new viewer or after session renewal.
