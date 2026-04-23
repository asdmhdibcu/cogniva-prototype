#!/bin/bash

# 1. Start the Flask API (Backend) in the background
# It binds to the local container network so Streamlit can talk to it
gunicorn --bind 127.0.0.1:5000 api:app &

# 2. Start Streamlit (Frontend) in the foreground
# It binds to the external port provided by Railway ($PORT)
streamlit run app.py --server.port="${PORT:-8080}" --server.address="0.0.0.0"
