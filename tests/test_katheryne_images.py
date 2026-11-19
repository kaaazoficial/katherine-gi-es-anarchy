from io import BytesIO

from PIL import Image
import pytest

from katherine_images import validate_avatar_image_bytes


def png_bytes(size=(32, 24)):
    output = BytesIO()
    Image.new("RGB", size, (40, 80, 120)).save(output, format="PNG")
    return output.getvalue()


def test_validates_real_png_bytes_and_reports_bounded_metadata():
    result = validate_avatar_image_bytes(png_bytes(), "image/png")
    assert result["format"] == "PNG"
    assert result["width"] == 32
    assert result["height"] == 24
    assert result["frames"] == 1


def test_rejects_mime_spoofing():
    with pytest.raises(ValueError, match="no coincide"):
        validate_avatar_image_bytes(png_bytes(), "image/jpeg")


def test_rejects_corrupted_image_data():
    with pytest.raises(ValueError, match="imagen válida"):
        validate_avatar_image_bytes(b"not an image", "image/png")


def test_rejects_images_with_excessive_pixel_count():
    output = BytesIO()
    Image.new("1", (6000, 5000)).save(output, format="PNG")
    with pytest.raises(ValueError, match="resolución"):
        validate_avatar_image_bytes(output.getvalue(), "image/png")
