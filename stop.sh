#!/usr/bin/env bash
# Ferma il server Streamlit del tool outreach
pkill -f 'streamlit run app.py' && echo "Server fermato." || echo "Nessun server attivo."
