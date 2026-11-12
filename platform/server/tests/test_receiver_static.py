"""Static dashboard files served at /."""

import socket


def write_web(tmp_path):
    web = tmp_path / "webroot"
    (web / "css").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>dash</title>")
    (web / "app.js").write_text("console.log(1)")
    (web / "css" / "site.css").write_text("body{}")
    (web / "data.json").write_text("{}")
    (web / "logo.svg").write_text("<svg/>")
    (web / "pic.png").write_bytes(b"\x89PNG\r\n")
    (web / "font.woff2").write_bytes(b"wOF2")
    (web / "favicon.ico").write_bytes(b"\x00\x00")
    (tmp_path / "secret.txt").write_text("do not serve")
    return web


def raw_get(port, path):
    """Send the path exactly as given, without client-side normalisation."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


def test_index_and_content_types(make_receiver, tmp_path):
    _, client = make_receiver(web_dir=write_web(tmp_path))
    status, headers, body = client.request("GET", "/")
    assert status == 200 and body == b"<!doctype html><title>dash</title>"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    expected = {
        "/index.html": "text/html",
        "/app.js": "text/javascript",
        "/css/site.css": "text/css",
        "/data.json": "application/json",
        "/logo.svg": "image/svg+xml",
        "/pic.png": "image/png",
        "/font.woff2": "font/woff2",
        "/favicon.ico": "image/x-icon",
    }
    for path, kind in expected.items():
        status, headers, _ = client.request("GET", path)
        assert status == 200, path
        assert headers["Content-Type"].startswith(kind), path
        assert headers["Cache-Control"] == "no-store", path


def test_query_string_ignored_and_missing_file_404(make_receiver, tmp_path):
    _, client = make_receiver(web_dir=write_web(tmp_path))
    status, _, body = client.request("GET", "/app.js?v=3")
    assert status == 200 and body == b"console.log(1)"
    status, _, _ = client.request("GET", "/nope.js")
    assert status == 404


def test_path_escape_refused(make_receiver, tmp_path):
    receiver, _ = make_receiver(web_dir=write_web(tmp_path))
    for path in (
        "/../secret.txt",
        "/css/../../secret.txt",
        "/%2e%2e/secret.txt",
        "/%2E%2E%2Fsecret.txt",
        "/..%2fsecret.txt",
        "/css/..%5c..%5csecret.txt",
        "//etc/passwd",
        "/%00index.html",
    ):
        status, body = raw_get(receiver.port, path)
        assert status == 404, path
        assert b"do not serve" not in body and b"root:" not in body, path


def test_symlink_out_of_web_dir_refused(make_receiver, tmp_path):
    web = write_web(tmp_path)
    (web / "link.txt").symlink_to(tmp_path / "secret.txt")
    receiver, _ = make_receiver(web_dir=web)
    status, body = raw_get(receiver.port, "/link.txt")
    assert status == 404 and b"do not serve" not in body


def test_missing_web_dir_serves_notice_and_api_still_works(make_receiver, tmp_path):
    _, client = make_receiver(web_dir=tmp_path / "does-not-exist")
    status, headers, body = client.request("GET", "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b"Dashboard files are not there" in body
    status, _, _ = client.request("GET", "/app.js")
    assert status == 404
    status, state = client.json("GET", "/api/state")
    assert status == 200 and state["ok"] is True


def test_web_dir_without_index(make_receiver, tmp_path):
    web = tmp_path / "noindex"
    web.mkdir()
    (web / "app.js").write_text("x")
    _, client = make_receiver(web_dir=web)
    status, _, body = client.request("GET", "/")
    assert status == 200 and b"Dashboard files are not there" in body
    status, _, body = client.request("GET", "/app.js")
    assert status == 200 and body == b"x"


def test_head_request(make_receiver, tmp_path):
    _, client = make_receiver(web_dir=write_web(tmp_path))
    status, headers, body = client.request("HEAD", "/app.js")
    assert status == 200 and body == b"" and headers["Content-Length"] == "14"
