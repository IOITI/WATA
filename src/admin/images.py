"""
What an image is, from its first bytes: the pictures the admin keeps or passes on (a script's picture, a file
attached for the AI agent) are known so, never by their name or the type a browser says.
"""

PNG, JPEG, GIF, WEBP = "image/png", "image/jpeg", "image/gif", "image/webp"
_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", PNG), (b"\xff\xd8\xff", JPEG), (b"GIF87a", GIF), (b"GIF89a", GIF))


def image_type(data: bytes) -> str | None:
    """The media type of an image (PNG, JPEG, GIF, WebP); None for anything else."""
    for signature, media_type in _SIGNATURES:
        if data.startswith(signature):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    return None
