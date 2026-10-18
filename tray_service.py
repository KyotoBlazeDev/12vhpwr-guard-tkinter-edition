"""Tray callbacks enqueue actions; Tk is only touched by its main thread."""
import threading


class TrayService:
    def __init__(self, commands):
        self.commands = commands
        self.icon = None
        self.thread = None
        self.error = None
        self.stopping = False
        self.last_color = None

    @staticmethod
    def color(title):
        for status, color in [('SHUTDOWN', '#ed4141'), ('MITIGATED', '#ef8a28'), ('OVER', '#e3b332'),
                              ('PAUSED', '#408ae6'), ('LIVE', '#43bf67')]:
            if status in title:
                return color
        return '#9966bb'

    @staticmethod
    def image(color):
        from PIL import Image, ImageDraw
        image = Image.new('RGBA', (64, 64), (30, 30, 30, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((12, 12, 52, 52), outline=color, width=5)
        draw.text((24, 23), 'G', fill='white')
        return image

    def start(self):
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem('Open dashboard', lambda *_: self.commands.put('show'), default=True),
                pystray.MenuItem('Show overlay', lambda *_: self.commands.put('overlay')),
                pystray.MenuItem('Pause / resume', lambda *_: self.commands.put('pause')),
                pystray.MenuItem('Exit', lambda *_: self.commands.put('exit')))
            self.icon = pystray.Icon('12vhpwr_guard_tk', self.image('#9966bb'), '12VHPWR Guard — starting', menu)
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            return True
        except Exception as error:
            self.error = str(error)
            return False

    def _run(self):
        try:
            self.icon.run()
        except Exception as error:
            self.error = str(error)
        finally:
            if not self.stopping:
                self.commands.put('show')

    def available(self):
        return bool(self.icon and self.thread and self.thread.is_alive() and not self.error)

    def update(self, title):
        if self.available():
            try:
                self.icon.title = title[:127]
                color = self.color(title)
                if color != self.last_color:
                    self.icon.icon = self.image(color)
                    self.last_color = color
            except Exception as error:
                self.error = str(error)
                self.commands.put('show')

    def stop(self):
        self.stopping = True
        if self.icon:
            try:
                self.icon.stop()
            except Exception as error:
                self.error = str(error)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
