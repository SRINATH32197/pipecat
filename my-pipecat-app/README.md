T1 : 

cd ~/Documents/Struzon/Dev-Pro/pipecat/my-pipecat-app
uv run --active python exotel/server.py

T2 :

cloudflared tunnel --url http://127.0.0.1:7860