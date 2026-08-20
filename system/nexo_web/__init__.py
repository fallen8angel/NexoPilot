"""NexoPilot port-7000 web package helpers.

Keep browser-facing branding owned by NexoPilot even when a legacy Carrot web
page or script is reused underneath the NEXO web server.
"""

from http import HTTPStatus


def _install_nexopilot_branding() -> None:
  # Importing web_core here is safe: package __init__ runs before web.py and the
  # web entry point subsequently reuses this already-loaded module.
  try:
    from system.nexo_web import web_core
  except Exception:
    return

  handler = getattr(web_core, "Handler", None)
  if handler is None or getattr(handler, "_nexopilot_branding_installed", False):
    return

  original_send = handler._send
  original_do_get = handler.do_GET
  favicon_href = "/favicon.svg?v=nexopilot-1"
  favicon_link = f'<link rel="icon" type="image/svg+xml" href="{favicon_href}">'
  replacements = (
    ("CarrotPilot", "NexoPilot"),
    ("Carrot Pilot", "NexoPilot"),
    ("CarrotDriver", "NexoPilot"),
    ("Carrot Driver", "NexoPilot"),
    ("carrotpilot", "NexoPilot"),
  )

  def branded_send(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
    if isinstance(body, str):
      for old, new in replacements:
        body = body.replace(old, new)

      # Put the NexoPilot icon last in <head> so it wins over any legacy
      # favicon declaration that a reused Carrot page may still contain.
      if "</head>" in body and favicon_href not in body:
        body = body.replace("</head>", favicon_link + "</head>", 1)

    original_send(self, body, status)

  def branded_do_get(self) -> None:
    if self.path.split("?", 1)[0] == "/favicon.svg":
      data = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="14" fill="#1261ff"/>'
        '<path d="M17 47V17h8l14 19V17h8v30h-8L25 28v19z" fill="white"/>'
        '</svg>'
      ).encode("utf-8")
      self.send_response(HTTPStatus.OK)
      self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
      self.send_header("Content-Length", str(len(data)))
      self.send_header("Cache-Control", "no-store")
      self.send_header("X-Content-Type-Options", "nosniff")
      self.end_headers()
      self.wfile.write(data)
      return

    original_do_get(self)

  handler._send = branded_send
  handler.do_GET = branded_do_get
  handler._nexopilot_branding_installed = True


_install_nexopilot_branding()
