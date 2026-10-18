"""Widget factories for themed TTK and actual classic Tk controls."""
import tkinter as tk
from tkinter import ttk


class ClassicGauge(tk.Canvas):
    def __init__(self, parent, length=65, maximum=20, **kwargs):
        super().__init__(parent, width=20, height=length, background="#dedede", highlightthickness=1)
        self.maximum = maximum
        self.value = 0
        self.bind('<Configure>', lambda event: self.redraw())

    def configure(self, cnf=None, **kwargs):
        self.maximum = kwargs.pop('maximum', self.maximum)
        self.value = kwargs.pop('value', self.value)
        if cnf or kwargs:
            super().configure(cnf, **kwargs)
        self.redraw()

    def redraw(self):
        self.delete('all')
        height = self.winfo_height()
        fraction = max(0, min(1, self.value / self.maximum))
        self.create_rectangle(0, height * (1-fraction), self.winfo_width(), height,
                              fill='#28a745', outline='')


class ClassicChoice(tk.OptionMenu):
    def __init__(self, parent, textvariable, values, width=18, **kwargs):
        self.variable = textvariable
        super().__init__(parent, textvariable, *values,
                         command=lambda value: self.event_generate('<<ComboboxSelected>>'))
        self.configure(width=width)

    def set_values(self, values):
        self['menu'].delete(0, 'end')
        for value in values:
            self['menu'].add_command(label=value, command=tk._setit(
                self.variable, value, lambda value: self.event_generate('<<ComboboxSelected>>')))


def set_choices(widget, values):
    if isinstance(widget, ClassicChoice):
        widget.set_values(values)
    else:
        widget.configure(values=values)


class Widgets:
    def __init__(self, classic=False):
        self.classic = classic

    def __getattr__(self, name):
        if not self.classic:
            return getattr(ttk, name)
        if name == 'Progressbar':
            return ClassicGauge
        if name == 'Combobox':
            return ClassicChoice
        def create(parent, **kwargs):
            style = kwargs.pop('style', '')
            padding = kwargs.pop('padding', None)
            if name in ('Frame', 'LabelFrame') and padding is not None:
                kwargs.update(padx=padding, pady=padding)
            if name == 'Label':
                kwargs.setdefault('anchor', 'w')
                if style:
                    kwargs['font'] = ('Segoe UI', 23 if style == 'Title.TLabel' else 13, 'bold')
                if isinstance(padding, tuple):
                    kwargs.update(padx=padding[0], pady=padding[1])
            if name == 'Scale':
                kwargs.update(orient='horizontal', showvalue=False, resolution=0.1)
            return getattr(tk, name)(parent, **kwargs)
        return create
