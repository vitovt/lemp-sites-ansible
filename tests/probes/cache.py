from pathlib import Path
import socket, socketserver, struct, threading, subprocess, tempfile, urllib.request, time
repo=Path(__file__).resolve().parents[2]
t=Path(tempfile.mkdtemp(prefix='lemp-audit-nginx-'))
count=0
class FCGI(socketserver.BaseRequestHandler):
    def handle(self):
        global count
        def readn(n):
            b=b''
            while len(b)<n:
                x=self.request.recv(n-len(b))
                if not x: raise EOFError
                b+=x
            return b
        while True:
            v,typ,rid,n,pad,_=struct.unpack('!BBHHBB',readn(8))
            body=readn(n);readn(pad)
            if typ==5 and n==0: break
        count+=1
        out=f'Content-Type: text/plain\r\nCache-Control: private, no-store\r\nSet-Cookie: private_session={count}; Path=/\r\n\r\nprivate response {count}'.encode()
        def send(typ,b): self.request.sendall(struct.pack('!BBHHBB',1,typ,rid,len(b),0,0)+b)
        send(6,out);send(6,b'');send(3,struct.pack('!IB3x',0,0))
server=socketserver.TCPServer(('127.0.0.1',0),FCGI)
threading.Thread(target=server.serve_forever,daemon=True).start()
s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
cache=(repo/'roles/nginx/templates/fastcgi-cache.conf.j2').read_text().replace('{{ ansible_managed }}','audit').replace('{{ nginx_fastcgi_cache_path }}',str(t/'cache')).replace('{{ nginx_fastcgi_cache_size }}','10m').replace('{{ nginx_fastcgi_cache_inactive }}','60m')
maps=(repo/'roles/nginx/templates/fastcgi-cache-skip.conf.j2').read_text().replace('{{ ansible_managed }}','audit')
(t/'htdocs/uploads').mkdir(parents=True)
(t/'other-site-config.php').write_text('<?php /* PRIVATE CONFIG SENTINEL */')
(t/'htdocs/uploads/leak.txt').symlink_to(t/'other-site-config.php')
conf=f'''pid {t}/nginx.pid;
error_log {t}/error.log;
worker_processes 1;
daemon off;
events {{ worker_connections 32; }}
http {{
 access_log off;
 client_body_temp_path {t}/body;
 fastcgi_temp_path {t}/fcgi;
 {cache}
 {maps}
 server {{
 listen 127.0.0.1:{port};
 root {t}/htdocs;
 location /private {{
 fastcgi_pass 127.0.0.1:{server.server_address[1]};
 fastcgi_param SCRIPT_FILENAME /index.php;
 fastcgi_cache WORDPRESS;
 fastcgi_cache_valid 200 301 302 30m;
 fastcgi_cache_bypass $wp_skip_cache;
 fastcgi_no_cache $wp_skip_cache;
 add_header X-Cache $upstream_cache_status always;
 }}
 location /uploads/ {{ try_files $uri =404; }}
 }}
}}
'''
(t/'nginx.conf').write_text(conf)
p=subprocess.Popen(['nginx','-p',str(t)+'/', '-c',str(t/'nginx.conf')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
try:
    for _ in range(30):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.1): break
        except OSError: time.sleep(.1)
    results=[]
    for i in range(2):
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/private') as r:
            results.append((r.headers.get('X-Cache'), r.headers.get('Set-Cookie')))
            print(f'guest {i+1}: cache={r.headers.get("X-Cache")}, cookie={r.headers.get("Set-Cookie")}, cache-control={r.headers.get("Cache-Control")}, body={r.read().decode()}')
    assert results[0][1] != results[1][1], 'private session cookie leaked through cache'
    assert all(x[0] != 'HIT' for x in results), 'private response was cached'
finally:
    p.terminate();p.communicate(timeout=5);server.shutdown()
print('PASS private response cache regression')
import shutil
shutil.rmtree(t)
