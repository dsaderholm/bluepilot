#!/usr/bin/env python3
"""FusionPilot: generate the boot artwork, so it can be regenerated instead of hand-edited.

The BluePilot assets it replaces were flat images with no source, which meant any change -- a
different blue, a tweak to the spacing -- had to go through an image editor. These are drawn from
this file, so the wordmark is text and the colors are constants.

Three targets, sized to match what they replace exactly:

    img_fusionpilot_boot.jpg        2160 x 1080   TICI / comma 3X boot splash
    img_fusionpilot_boot_mici.jpg    536 x 240    MICI boot splash
    spinner_fusionpilot.png         1024 x 1024   spinner overlay, transparent

Run from the repo root:

    python selfdrive/assets/make_fusionpilot_boot.py

Design is deliberately quiet. It is a boot screen: it is looked at for two seconds while the device
comes up, and the only jobs are saying which software this is and not being ugly at 3 in the
morning. Audiowide is the same face the home screen uses, so the two agree.
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

ASSETS = pathlib.Path(__file__).resolve().parent
FONT = ASSETS / "fonts" / "Audiowide-Regular.ttf"

# Ford blue rather than the home screen's pure (0,0,255): at 2160 px on a black field, full-primary
# blue fringes badly and reads as purple. This is close to Ford's own and survives the JPEG.
BLUE = (0, 94, 184)
WHITE = (235, 238, 242)
BG = (10, 12, 16)


def _fit(draw, text, font_path, target_w):
  """Largest size at which `text` still fits target_w. Measured, not guessed at from point size."""
  lo, hi = 8, 400
  while lo < hi:
    mid = (lo + hi + 1) // 2
    f = ImageFont.truetype(str(font_path), mid)
    if draw.textlength(text, font=f) <= target_w:
      lo = mid
    else:
      hi = mid - 1
  return ImageFont.truetype(str(font_path), lo)


def wordmark(w: int, h: int, subtitle: str | None) -> Image.Image:
  img = Image.new("RGB", (w, h), BG)
  d = ImageDraw.Draw(img)

  # "Fusion" white, "Pilot" blue -- the same two-tone the openpilot family uses, and it splits the
  # name where its meaning splits: the car, and the software.
  a, b = "Fusion", "Pilot"
  font = _fit(d, a + b, FONT, int(w * 0.66))
  wa, wb = d.textlength(a, font=font), d.textlength(b, font=font)
  asc, desc = font.getmetrics()

  x = (w - (wa + wb)) / 2
  y = (h - (asc + desc)) / 2 - (h * 0.04 if subtitle else 0)
  d.text((x, y), a, font=font, fill=WHITE)
  d.text((x + wa, y), b, font=font, fill=BLUE)

  # A hairline under the wordmark, matching its width. Cheap, and it stops the text floating.
  ry = y + asc + desc * 0.55
  d.rectangle([x, ry, x + wa + wb, ry + max(2, h // 360)], fill=BLUE)

  if subtitle:
    sf = ImageFont.truetype(str(FONT), max(12, int(font.size * 0.17)))
    sw = d.textlength(subtitle, font=sf)
    d.text(((w - sw) / 2, ry + h * 0.055), subtitle, font=sf, fill=(120, 130, 145))
  return img


def spinner(size: int) -> Image.Image:
  """Transparent square with the wordmark centered; the UI rotates a separate ring around it."""
  img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
  d = ImageDraw.Draw(img)
  a, b = "Fusion", "Pilot"
  font = _fit(d, a + b, FONT, int(size * 0.62))
  wa, wb = d.textlength(a, font=font), d.textlength(b, font=font)
  asc, desc = font.getmetrics()
  x = (size - (wa + wb)) / 2
  y = (size - (asc + desc)) / 2
  d.text((x, y), a, font=font, fill=WHITE + (255,))
  d.text((x + wa, y), b, font=font, fill=BLUE + (255,))
  return img


def main() -> None:
  out = [
    (ASSETS / "img_fusionpilot_boot.jpg", wordmark(2160, 1080, "2020 Ford Fusion  ·  Edge ADAS retrofit")),
    (ASSETS / "img_fusionpilot_boot_mici.jpg", wordmark(536, 240, None)),
    (ASSETS / "images" / "spinner_fusionpilot.png", spinner(1024)),
  ]
  for path, img in out:
    if path.suffix == ".jpg":
      img.save(path, quality=92, optimize=True)
    else:
      img.save(path, optimize=True)
    print(f"  {path.relative_to(ASSETS.parent.parent)}  {img.size[0]}x{img.size[1]}")


if __name__ == "__main__":
  main()
