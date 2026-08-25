#!/usr/bin/env python3
"""Small localhost TCP forwarder for Vast's host:container port config."""
import argparse
import socket
import socketserver
import threading


class ForwardHandler(socketserver.BaseRequestHandler):
    def handle(self):
        target = self.server.target
        try:
            upstream = socket.create_connection(target, timeout=5)
        except OSError:
            return

        def pipe(source, destination):
            try:
                while True:
                    data = source.recv(65536)
                    if not data:
                        break
                    destination.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        a = threading.Thread(target=pipe, args=(self.request, upstream), daemon=True)
        b = threading.Thread(target=pipe, args=(upstream, self.request), daemon=True)
        a.start()
        b.start()
        a.join()
        b.join()
        upstream.close()


class ForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("listen", type=int)
    parser.add_argument("target", type=int)
    args = parser.parse_args()
    server = ForwardServer(("127.0.0.1", args.listen), ForwardHandler)
    server.target = ("127.0.0.1", args.target)
    print(f"forwarding 127.0.0.1:{args.listen} -> 127.0.0.1:{args.target}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
