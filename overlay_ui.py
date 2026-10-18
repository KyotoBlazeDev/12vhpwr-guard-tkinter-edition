"""Optional draggable, scalable, always-on-top view of existing snapshots."""
import tkinter as tk


class Overlay:
    def __init__(self, owner):
        self.owner = owner
        self.window = tk.Toplevel(owner.root)
        self.window.title('12VHPWR Guard overlay')
        self.window.configure(background='#242424')
        self.window.attributes('-topmost', True)
        self.window.protocol('WM_DELETE_WINDOW', self.hide)
        self.canvas = tk.Canvas(self.window, background='#242424', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<ButtonPress-1>', self.begin_drag)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<Double-Button-1>', lambda event: owner.show_dashboard())
        self.canvas.bind('<Button-3>', lambda event: owner.show_display_settings())
        self.drag_origin = (0, 0)

    def begin_drag(self, event):
        self.drag_origin = (event.x_root-self.window.winfo_x(), event.y_root-self.window.winfo_y())

    def drag(self, event):
        self.window.geometry(f'+{event.x_root-self.drag_origin[0]}+{event.y_root-self.drag_origin[1]}')

    def hide(self):
        self.window.withdraw()

    def show(self):
        self.window.deiconify()
        self.window.lift()

    def render(self, title, status, currents, maximum, rows, scale):
        canvas = self.canvas
        canvas.delete('all')
        width, height = 420, 310 + len(rows)*24
        canvas.configure(width=int(width*scale), height=int(height*scale))
        def text(x, y, value, size=11, color='white', **kwargs):
            return canvas.create_text(x*scale, y*scale, text=value, fill=color,
                                      font=('Segoe UI', max(8, int(size*scale))), anchor='nw', **kwargs)
        text(16, 10, '12VHPWR GUARD', 16)
        settings = text(345, 10, '⚙', 16)
        close = text(388, 10, '×', 16)
        canvas.tag_bind(settings, '<Button-1>', lambda event: self.owner.show_display_settings())
        canvas.tag_bind(close, '<Button-1>', lambda event: self.hide())
        text(16, 45, title, 10, width=390*scale)
        for i, value in enumerate(currents):
            x, top, bottom = 18+i*65, 95, 220
            canvas.create_rectangle(x*scale, top*scale, (x+42)*scale, bottom*scale, fill='#383838', outline='')
            if value is not None:
                fraction = max(0, min(1, value/maximum))
                color = '#ff5454' if value >= self.owner.threshold_for_display() else '#3ed66b'
                canvas.create_rectangle(x*scale, (bottom-(bottom-top)*fraction)*scale,
                                        (x+42)*scale, bottom*scale, fill=color, outline='')
            text(x, 225, f'{value:.2f} A' if value is not None else '— A')
            text(x+10, 250, f'P{i+1}', color='#aaaaaa')
        for i, row in enumerate(rows):
            text(16, 280+i*24, row, 10, width=395*scale)
        text(16, height-24, status, 10, '#ffd778', width=390*scale)
