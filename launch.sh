#!/usr/bin/env bash
# Avvia il tool in background e apre il browser. Chiudere il browser non killa il server;
# per fermarlo, esegui: pkill -f 'streamlit run app.py'
set -e
cd "$(dirname "$0")"

PORT=8501
URL="http://localhost:${PORT}"

# Crea venv se mancante
if [ ! -d venv ]; then
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip --quiet
  ./venv/bin/pip install -r requirements.txt --quiet
fi

# Se già attivo su quella porta, apri solo il browser
if curl -s -o /dev/null "${URL}/_stcore/health" 2>/dev/null; then
  xdg-open "${URL}" >/dev/null 2>&1 &
  exit 0
fi

# Avvia Streamlit in background, log in data/streamlit.log
mkdir -p data
nohup ./venv/bin/streamlit run app.py \
  --server.port ${PORT} \
  --server.headless true \
  --browser.gatherUsageStats false \
  > data/streamlit.log 2>&1 &

# Attendi che il server risponda (max 20s)
for i in $(seq 1 40); do
  if curl -s -o /dev/null "${URL}/_stcore/health" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

xdg-open "${URL}" >/dev/null 2>&1 &
