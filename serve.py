"""
Events Connect — Serveur de production local (Waitress)
Accessible depuis tous les appareils du même réseau WiFi.
"""
import socket
from waitress import serve
from app import app

HOST = '0.0.0.0'
PORT = 5000

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

if __name__ == '__main__':
    local_ip = get_local_ip()
    print()
    print('=' * 52)
    print('  Events Connect -- Serveur local lance !')
    print('=' * 52)
    print(f'  [PC]      localhost        : http://localhost:{PORT}')
    print(f'  [Mobile]  reseau local     : http://{local_ip}:{PORT}')
    print(f'  [WiFi]    autres appareils : http://{local_ip}:{PORT}')
    print('=' * 52)
    print('  Ctrl+C pour arreter le serveur.')
    print('=' * 52)
    print()
    serve(app, host=HOST, port=PORT, threads=4)
