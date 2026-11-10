"""One-shot sendWidget transport; all other modes remain in drone-telegram."""
import json
import os
import sys
import urllib.error
import urllib.request
import zlib

MAX_FILE_BYTES = 1024 * 1024
STRINGS = {"html", "public_data", "chat_data", "caption", "parse_mode", "message_effect_id"}
INTEGERS = {"width", "height"}
BOOLEANS = {"show_caption_above_media", "allow_queries", "is_shared",
            "disable_notification", "protect_content", "allow_paid_broadcast"}
OBJECTS = {"reply_parameters", "reply_markup"}
ALLOWED = STRINGS | INTEGERS | BOOLEANS | OBJECTS | {"caption_entities"}
CONFLICTS = ("message", "message_file", "rich_message_file", "photo", "document",
             "sticker", "audio", "voice", "location", "venue", "video",
             "format", "disable_web_page_preview", "socks5")


class WidgetError(Exception):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WidgetError("Duplicate JSON field")
        result[key] = value
    return result


def validate(payload):
    if not isinstance(payload, dict) or set(payload) - ALLOWED:
        raise WidgetError("Widget must be an object containing only supported fields")
    if "html" not in payload:
        raise WidgetError("Widget html is required")
    for key, value in payload.items():
        expected = (str if key in STRINGS else int if key in INTEGERS else
                    bool if key in BOOLEANS else dict if key in OBJECTS else list)
        if type(value) is not expected:
            raise WidgetError("Invalid widget field type")
    try:
        html = payload["html"].encode("utf-8")
        if b"\x00" in html:
            raise WidgetError("Widget HTML must not contain NUL characters")
        if not 1 <= len(html) <= 32768 or len(zlib.compress(html, 9)) > 4096:
            raise WidgetError("Widget HTML exceeds byte or compressed-size limits")
        for key in ("public_data", "chat_data"):
            if len(payload.get(key, "").encode("utf-8")) > 1024:
                raise WidgetError("Widget state exceeds 1024 UTF-8 bytes")
        # Also reject unpaired surrogates in other strings, before transport.
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except UnicodeError:
        raise WidgetError("Widget contains invalid Unicode") from None
    if "parse_mode" in payload and "caption_entities" in payload:
        raise WidgetError("Use parse_mode or caption_entities, not both")
    if "parse_mode" in payload and payload["parse_mode"] not in ("HTML", "Markdown", "MarkdownV2"):
        raise WidgetError("Unsupported caption parse_mode")
    # Telegram validates the length after parsing formatted captions.
    if "parse_mode" not in payload and len(payload.get("caption", "")) > 1024:
        raise WidgetError("Caption exceeds 1024 characters")
    if any(type(entity) is not dict for entity in payload.get("caption_entities", [])):
        raise WidgetError("Caption entities must be objects")
    return payload


def prepare(env):
    if any(env.get("INPUT_" + key.upper(), "") for key in CONFLICTS):
        raise WidgetError("widget_file is mutually exclusive with message, rich message, media, format, preview and proxy inputs")
    token = env.get("INPUT_TOKEN", "").strip()
    destination = env.get("INPUT_TO", "").strip()
    if not token or not destination or any(c in token for c in "/?#\r\n"):
        raise WidgetError("A token and one destination are required")
    # No fan-out: a failure must never lead to replaying successful sends.
    if "," in destination or any(c.isspace() for c in destination):
        raise WidgetError("widget_file requires one destination")
    try:
        with open(env["INPUT_WIDGET_FILE"], "rb") as source:
            raw = source.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise WidgetError("Widget JSON file exceeds 1 MiB")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(WidgetError("Invalid JSON number")))
    except (OSError, UnicodeError, ValueError, KeyError):
        raise WidgetError("Cannot read valid UTF-8 widget JSON") from None
    validate(payload)
    payload["chat_id"] = destination
    thread = env.get("INPUT_MESSAGE_THREAD_ID", "")
    if thread:
        try:
            payload["message_thread_id"] = int(thread)
        except ValueError:
            raise WidgetError("Invalid message_thread_id") from None
        if payload["message_thread_id"] <= 0:
            raise WidgetError("Invalid message_thread_id")
    notification = env.get("INPUT_DISABLE_NOTIFICATION", "")
    if notification:
        if "disable_notification" in payload:
            raise WidgetError("Set disable_notification in the file or action input, not both")
        if notification.lower() not in ("true", "false"):
            raise WidgetError("disable_notification must be true or false")
        payload["disable_notification"] = notification.lower() == "true"
    return token, payload


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(token, payload, opener=None):
    # Disable ambient proxies and redirects. Never retry or log URL/body/errors:
    # the URL contains the token and responses can echo caller-owned content.
    if opener is None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendWidget",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with opener.open(request, timeout=30) as response:
            result = json.loads(response.read(MAX_FILE_BYTES + 1))
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise WidgetError("Telegram rejected the widget; no retry was attempted")
    except WidgetError:
        raise
    except Exception:
        raise WidgetError("Widget send failed; delivery may be unknown. No retry was attempted") from None


def main():
    try:
        token, payload = prepare(os.environ)
        send(token, payload)
    except WidgetError as error:
        print("Widget error: " + str(error), file=sys.stderr)
        return 1
    print("Widget sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
