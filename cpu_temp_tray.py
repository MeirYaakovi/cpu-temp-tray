import threading
import wmi
from PIL import Image, ImageDraw, ImageFont
import pystray

REFRESH_INTERVAL = 15
ICON_SIZE = 32


def get_cpu_temp():
    # Try native ACPI thermal zone (works when running as admin)
    try:
        w = wmi.WMI(namespace="root\\wmi")
        zones = w.MSAcpi_ThermalZoneTemperature()
        if zones:
            return max((z.CurrentTemperature / 10.0) - 273.15 for z in zones)
    except Exception:
        pass

    # Fallback: LibreHardwareMonitor WMI (if LHM is running in background)
    try:
        w = wmi.WMI(namespace="root\\LibreHardwareMonitor")
        sensors = w.Sensor()
        cpu_temps = [
            float(s.Value) for s in sensors
            if s.SensorType == "Temperature" and "CPU" in (s.Parent or "")
        ]
        if cpu_temps:
            return max(cpu_temps)
    except Exception:
        pass

    return None


def _load_font(size):
    for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_icon(temp):
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if temp is None:
        bg, label = (100, 100, 100, 220), "?"
    elif temp < 60:
        bg, label = (20, 170, 20, 230), str(int(round(temp)))
    elif temp < 80:
        bg, label = (210, 130, 0, 230), str(int(round(temp)))
    else:
        bg, label = (200, 20, 20, 230), str(int(round(temp)))

    draw.rounded_rectangle([1, 1, ICON_SIZE - 1, ICON_SIZE - 1], radius=6, fill=bg)

    font_size = 16 if len(label) <= 2 else 12
    font = _load_font(font_size)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((ICON_SIZE - tw) / 2 - bbox[0], (ICON_SIZE - th) / 2 - bbox[1]),
        label, fill="white", font=font,
    )
    return img


def _refresh_loop(icon, stop_event):
    while not stop_event.wait(REFRESH_INTERVAL):
        temp = get_cpu_temp()
        icon.icon = make_icon(temp)
        icon.title = f"CPU: {int(round(temp))}°C" if temp is not None else "CPU: N/A"


def on_quit(icon, _item):
    icon._stop_event.set()
    icon.stop()


def main():
    temp = get_cpu_temp()
    icon = pystray.Icon(
        "cpu_temp",
        make_icon(temp),
        title=f"CPU: {int(round(temp))}°C" if temp is not None else "CPU: N/A",
        menu=pystray.Menu(pystray.MenuItem("Quit", on_quit)),
    )
    stop_event = threading.Event()
    icon._stop_event = stop_event
    threading.Thread(target=_refresh_loop, args=(icon, stop_event), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
