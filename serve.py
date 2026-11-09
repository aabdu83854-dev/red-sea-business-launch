"""Local / Replit preview server: serves the built site on port 5000 and maps /page to page.html."""
import http.server, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
class H(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        p = self.path.split("?")[0].split("#")[0]
        last = p.rsplit("/", 1)[-1]
        if p != "/" and "." not in last:
            cand = p.rstrip("/") + ".html"
            if os.path.exists("." + cand): self.path = cand
            elif not os.path.isdir("." + p): self.path = "/404.html"
        return super().send_head()
print("Preview on port 5000")
http.server.ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", 5000))), H).serve_forever()
