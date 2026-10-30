"""The colour roles the weather icons are drawn in.

The icon PNGs are authored in these exact RGB values, so a pixel's colour *is*
its role: the renderer looks the pixel up here and paints the colour configured
at `news.weather_icon.<role>` instead. That keeps the art recolourable from
colors/scoreboard.json without a second file mapping pixels to meanings, and it
leaves the PNGs readable in an image viewer rather than a sheet of sentinel
magenta.

tools/generate_weather_icons.py draws the icons from these same values, so the
two cannot drift.
"""

SUN = (255, 190, 40)
MOON = (200, 215, 255)
CLOUD = (205, 215, 230)
CLOUD_ALT = (120, 132, 150)
RAIN = (90, 150, 255)
SNOW = (235, 245, 255)
BOLT = (255, 230, 70)
MIST = (150, 160, 175)

# Authored colour -> the key under news.weather_icon that recolours it.
ICON_ROLES = {
    SUN: "sun",
    MOON: "moon",
    CLOUD: "cloud",
    CLOUD_ALT: "cloud_alt",
    RAIN: "rain",
    SNOW: "snow",
    BOLT: "bolt",
    MIST: "mist",
}
