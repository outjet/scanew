import socket
s = socket.socket()
s.settimeout(10)
s.connect(('172.16.0.40', 22))
print("Socket connect succeeded!")
s.close()