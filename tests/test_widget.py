import io
import json
import os
import random
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import widget


class Reply(io.BytesIO):
    pass


class Opener:
    def __init__(self, body=b'{"ok":true}', error=None):
        self.calls = []
        self.body = body
        self.error = error

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return Reply(self.body)


class WidgetTests(unittest.TestCase):
    def prepare(self, payload=None, raw=None, **inputs):
        with tempfile.NamedTemporaryFile() as source:
            source.write(raw if raw is not None else json.dumps(payload or {"html": "<b>Ready</b>"}).encode())
            source.flush()
            env = {"INPUT_WIDGET_FILE": source.name, "INPUT_TOKEN": "123:secret", "INPUT_TO": "456"}
            env.update(inputs)
            return widget.prepare(env)

    def test_caption_and_destination(self):
        token, body = self.prepare({"html": "<b>OK</b>", "caption": "Build passed",
                                    "width": 240, "height": 100, "chat_data": "{}"},
                                   INPUT_MESSAGE_THREAD_ID="42", INPUT_DISABLE_NOTIFICATION="true")
        self.assertEqual(token, "123:secret")
        self.assertEqual(body["chat_id"], "456")
        self.assertEqual(body["caption"], "Build passed")
        self.assertEqual(body["message_thread_id"], 42)
        self.assertIs(body["disable_notification"], True)

    def test_all_conflicting_modes(self):
        for name in widget.CONFLICTS:
            with self.subTest(name=name), self.assertRaises(widget.WidgetError):
                self.prepare(**{"INPUT_" + name.upper(): "value"})

    def test_destination_and_credential_override_rejected(self):
        for key in ("token", "chat_id", "message_thread_id", "business_connection_id", "unknown"):
            with self.subTest(key=key), self.assertRaises(widget.WidgetError):
                self.prepare({"html": "hello", key: "override"})

    def test_invalid_json(self):
        for raw in (b"[]", b"null", b"{", b'{"html":"a","html":"b"}',
                    b'{"html":NaN}', b'{"html":"\xff"}'):
            with self.subTest(raw=raw), self.assertRaises(widget.WidgetError):
                self.prepare(raw=raw)

    def test_types(self):
        for key, value in (("html", 1), ("width", True), ("height", 1.5),
                           ("allow_queries", "false"), ("caption_entities", {}),
                           ("reply_markup", []), ("caption_entities", [1])):
            with self.subTest(key=key), self.assertRaises(widget.WidgetError):
                self.prepare({"html": "ok", key: value})

    def test_utf8_html_boundary(self):
        widget.validate({"html": "é" * 16384})
        for html in ("", "é" * 16385, "\ud800", "hello\x00world"):
            with self.assertRaises(widget.WidgetError):
                widget.validate({"html": html})

    def test_compressed_html_limit(self):
        rng = random.Random(42)
        html = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(10000))
        self.assertLess(len(html.encode()), 32768)
        with self.assertRaises(widget.WidgetError):
            widget.validate({"html": html})

    def test_state_and_caption_boundaries(self):
        widget.validate({"html": "x", "chat_data": "é" * 512, "caption": "x" * 1024})
        for key, value in (("chat_data", "é" * 513), ("public_data", "x" * 1025),
                           ("caption", "x" * 1025)):
            with self.assertRaises(widget.WidgetError):
                widget.validate({"html": "x", key: value})
        # Formatted lengths are validated after entity parsing by Telegram.
        widget.validate({"html": "x", "caption": "<b>" + "x" * 1024 + "</b>", "parse_mode": "HTML"})

    def test_caption_format_conflict(self):
        with self.assertRaises(widget.WidgetError):
            self.prepare({"html": "x", "parse_mode": "HTML", "caption_entities": []})

    def test_duplicate_notification_setting(self):
        with self.assertRaises(widget.WidgetError):
            self.prepare({"html": "x", "disable_notification": False}, INPUT_DISABLE_NOTIFICATION="true")

    def test_invalid_action_inputs(self):
        for inputs in ({"INPUT_TO": "1,2"}, {"INPUT_TOKEN": "secret/other"},
                       {"INPUT_MESSAGE_THREAD_ID": "0"}, {"INPUT_DISABLE_NOTIFICATION": "yes"}):
            with self.subTest(inputs=inputs), self.assertRaises(widget.WidgetError):
                self.prepare(**inputs)

    def test_file_limit(self):
        with self.assertRaises(widget.WidgetError):
            self.prepare(raw=b" " * (widget.MAX_FILE_BYTES + 1))

    def test_exact_single_request(self):
        opener = Opener()
        payload = {"html": "<b>é</b>", "chat_id": "123", "caption": "Passed"}
        widget.send("secret", payload, opener)
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, "https://api.telegram.org/botsecret/sendWidget")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), payload)
        self.assertEqual(timeout, 30)

    def test_failures_never_retry_or_leak(self):
        for opener in (Opener(error=TimeoutError("secret")),
                       Opener(error=urllib.error.HTTPError("https://secret", 429, "secret", {}, None)),
                       Opener(b'{"ok":false,"description":"secret"}'),
                       Opener(b"secret"), Opener(b"[]")):
            with self.subTest(opener=opener):
                with self.assertRaises(widget.WidgetError) as error:
                    widget.send("secret", {"html": "secret"}, opener)
                self.assertNotIn("secret", str(error.exception))
                self.assertEqual(len(opener.calls), 1)

    def test_redirects_rejected(self):
        self.assertIsNone(widget.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other"))

    def test_main_error_output_is_sanitized(self):
        with patch.dict(os.environ, {"INPUT_WIDGET_FILE": "/secret/path"}, clear=True), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(widget.main(), 1)
            self.assertNotIn("/secret/path", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
