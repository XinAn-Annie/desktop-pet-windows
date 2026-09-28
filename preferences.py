"""Display preferences, local time and editable calendar categories."""
import ctypes
import datetime as dt
import os
import re
import uuid
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
from zoneinfo import ZoneInfo, available_timezones

DEFAULT_TAGS = {
    'ddl': {'name': 'DDL', 'color': '#ffd6d6'},
    'normal': {'name': '普通', 'color': '#fff1b8'},
    'regular': {'name': '规律', 'color': '#cfe3ff'},
    'meeting': {'name': '会议', 'color': '#ffd1e3'},
}
EN = dict(zip(
    ['日历与 DDL','运行中的程序','网址提醒','设置','桌宠设置','上传图片桌宠形象','桌宠启动设置','隐藏桌宠','显示桌宠','退出','今天','当天时间安排','解析并添加','编辑','完成/恢复','删除选中','添加网址','立即检查选中','删除订阅','刷新当前程序','时区','界面语言','应用设置','标签管理','新建标签','名称','颜色','调色盘','屏幕吸管','保存标签','普通','规律','会议','自动识别','保存','事项','类型','编辑日程','开始时间 HH:MM','结束时间 HH:MM','打开的窗口','进程','内存','网址','最新更新','本地摘要','检查状态'],
    ['Calendar & DDL','Running apps','Website alerts','Settings','Pet settings','Upload pet image','Startup settings','Hide pet','Show pet','Exit','Today','Daily schedule','Add events','Edit','Done / Restore','Delete selected','Add URL','Check selected','Unsubscribe','Refresh apps','Time zone','Language','Apply settings','Labels','New label','Name','Color','Palette','Screen eyedropper','Save label','Normal','Recurring','Meeting','Auto detect','Save','Event','Category','Edit event','Start HH:MM','End HH:MM','Window','Process','Memory','URL','Latest update','Local summary','Status']))
EN.update({
    '小桌宠工作台':'Desktop Pet Workbench',
    '打开工作台':'Open workbench', '显示/隐藏桌宠':'Show / hide pet',
    '桌宠启动方式':'Pet startup mode', '首次运行设置':'First-run settings',
    '登录 Windows 后自动显示桌宠':'Show pet when signing in to Windows',
    '使用独立快捷键启动器（桌宠不用常驻）':'Use independent hotkey launcher',
    '快捷键启动器会在登录后静默运行。按快捷键时：桌宠未运行则启动，已运行则显示/隐藏。':'The launcher runs at sign-in. The shortcut starts the pet or toggles its visibility.',
    '唤醒快捷键：':'Launch shortcut:', '默认：Win + Alt + O':'Default: Win + Alt + O',
    '订阅 RSS/Atom；普通网页会尝试检测公开订阅或检查页面文字变化。摘要在本机生成。':'Subscribe to RSS/Atom or monitor public webpage text. Summaries are generated locally.',
    '显示全天（取消勾选可放大时间轴）':'Fit full day (uncheck to zoom)',
    '截图 Ctrl+C 后，在输入框按 Ctrl+V':'Copy a screenshot, then paste into the input with Ctrl+V',
    '隐藏桌宠后，工作台仍会保留；退出会关闭桌宠和工作台。':'Hiding the pet keeps this window open. Exit closes both.',
    '时区切换改变日程与提醒使用的本地钟表时间，不换算已有事件。':'Schedules and reminders use the selected local clock. Existing times are not converted.',
    '添加文字或规律事件，例如：9月10-29每天上午2点上课':'Enter events (Chinese date parsing), e.g. 9月10-29每天上午2点上课',
})

def tr(text, settings):
    return EN.get(text, text) if settings.get('language') == 'en' else text

def local_now(settings):
    zone = settings.get('timezone', 'system')
    return dt.datetime.now().astimezone() if zone == 'system' else dt.datetime.now(ZoneInfo(zone))

def tags(settings):
    result = {k: dict(v) for k, v in DEFAULT_TAGS.items()}
    for key, tag in settings.get('tags', {}).items():
        if isinstance(tag, dict) and tag.get('name') and re.fullmatch(r'#[0-9a-fA-F]{6}', tag.get('color', '')):
            result[key] = dict(tag)
    return result

def foreground(color):
    r,g,b = (int(color[i:i+2],16) for i in (1,3,5))
    return '#111111' if .299*r+.587*g+.114*b > 145 else '#ffffff'

def enable_dpi():
    if os.name == 'nt':
        try:
            if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)): return
        except (AttributeError, OSError): pass
        try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError): pass

def translate_widgets(widget, settings):
    if isinstance(widget, tk.Toplevel): widget.title(tr(widget.title(), settings))
    if 'text' in widget.keys():
        widget.configure(text=tr(widget.cget('text'), settings))
    if isinstance(widget, ttk.Treeview):
        for col in widget['columns']:
            widget.heading(col, text=tr(widget.heading(col, 'text'), settings))
    for child in widget.winfo_children(): translate_widgets(child, settings)

class PreferencesPanel(ttk.Frame):
    def __init__(self, parent, settings, save, changed):
        super().__init__(parent)
        self.settings, self.save, self.changed = settings, save, changed
        self.zone = tk.StringVar(value=settings.get('timezone', 'system'))
        self.language = tk.StringVar(value='English' if settings.get('language') == 'en' else '简体中文')
        for row, (label, var, values) in enumerate((('时区', self.zone, ['system']+sorted(available_timezones())), ('界面语言', self.language, ['简体中文','English']))):
            ttk.Label(self,text=label).grid(row=row,column=0,sticky='w',pady=5)
            ttk.Combobox(self,textvariable=var,values=values,width=30,state='readonly').grid(row=row,column=1,sticky='ew')
        ttk.Label(self,text='时区切换改变日程与提醒使用的本地钟表时间，不换算已有事件。',wraplength=420).grid(row=2,column=0,columnspan=2,sticky='w',pady=5)
        ttk.Button(self,text='应用设置',command=self.apply).grid(row=3,column=1,sticky='e',pady=8)
        ttk.Label(self,text='标签管理').grid(row=4,column=0,sticky='w',pady=8)
        self.listbox = tk.Listbox(self,height=7,exportselection=False)
        self.listbox.grid(row=5,column=0,columnspan=2,sticky='ew')
        self.tag_keys=list(tags(settings))
        for key,tag in tags(settings).items():
            self.listbox.insert('end',tag['name']); self.listbox.itemconfigure('end',bg=tag['color'],fg=foreground(tag['color']))
        self.listbox.bind('<<ListboxSelect>>',self.select)
        self.key=None; self.name=tk.StringVar(); self.color=tk.StringVar(value='#cfe3ff')
        for row,(label,var) in enumerate((('名称',self.name),('颜色',self.color)),6):
            ttk.Label(self,text=label).grid(row=row,column=0,sticky='w',pady=5)
            ttk.Entry(self,textvariable=var).grid(row=row,column=1,sticky='ew')
        buttons=ttk.Frame(self); buttons.grid(row=8,column=0,columnspan=2,sticky='ew',pady=8)
        for label,command in [('新建标签',self.new),('调色盘',self.palette),('屏幕吸管',self.pick),('保存标签',self.save_tag)]:
            ttk.Button(buttons,text=label,command=command).pack(side='left',padx=2)
        self.preview=tk.Label(self,text='Aa',bg=self.color.get(),width=10)
        self.preview.grid(row=9,column=0,columnspan=2,sticky='ew')
        self.color.trace_add('write',self.preview_color)
        self.columnconfigure(1,weight=1)

    def preview_color(self,*_):
        if re.fullmatch(r'#[0-9a-fA-F]{6}',self.color.get()):
            self.preview.configure(bg=self.color.get(),fg=foreground(self.color.get()))

    def select(self,_=None):
        selection=self.listbox.curselection()
        if selection:
            self.key=self.tag_keys[selection[0]]; tag=tags(self.settings)[self.key]
            self.name.set(tag['name']); self.color.set(tag['color'])

    def new(self):
        self.listbox.selection_clear(0,'end'); self.key=None; self.name.set(''); self.color.set('#cfe3ff')

    def palette(self):
        result=colorchooser.askcolor(parent=self,initialcolor=self.color.get() if re.fullmatch(r'#[0-9a-fA-F]{6}',self.color.get()) else '#ffffff')[1]
        if result: self.color.set(result)

    def pick(self):
        # Capture first, so the picker does not sample its own overlay.
        from PIL import ImageGrab
        try: screenshot=ImageGrab.grab(all_screens=True)
        except OSError as exc: messagebox.showerror('Screen capture',str(exc),parent=self); return
        x=y=0
        if os.name=='nt': x,y=ctypes.windll.user32.GetSystemMetrics(76),ctypes.windll.user32.GetSystemMetrics(77)
        overlay=tk.Toplevel(self); overlay.overrideredirect(True); overlay.attributes('-topmost',True)
        overlay.geometry(f'{screenshot.width}x{screenshot.height}{x:+d}{y:+d}')
        overlay.attributes('-alpha',0.01); overlay.configure(cursor='crosshair')
        def close(_=None): overlay.grab_release(); overlay.destroy()
        def sample(event):
            px,py=event.x_root-x,event.y_root-y
            if 0<=px<screenshot.width and 0<=py<screenshot.height:
                rgb=screenshot.getpixel((px,py))[:3]; self.color.set('#%02x%02x%02x'%rgb)
            close()
        overlay.bind('<Button-1>',sample); overlay.bind('<Escape>',close); overlay.bind('<Button-3>',close)
        overlay.grab_set(); overlay.focus_force()

    def apply(self):
        if self.zone.get()!='system': ZoneInfo(self.zone.get())
        self.settings.update(timezone=self.zone.get(),language='en' if self.language.get()=='English' else 'zh-CN')
        self.save(self.settings); self.changed()

    def save_tag(self):
        name,color=self.name.get().strip(),self.color.get().strip()
        if not name or len(name)>30 or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):
            messagebox.showwarning('Invalid label / 标签无效','名称需为 1–30 个字符，颜色格式为 #RRGGBB。',parent=self); return
        if any(v['name']==name for k,v in tags(self.settings).items() if k!=self.key):
            messagebox.showwarning('Duplicate / 重复','标签名称不能重复。',parent=self); return
        key=self.key or 'custom_'+uuid.uuid4().hex
        self.settings.setdefault('tags',{})[key]={'name':name,'color':color}
        self.save(self.settings); self.changed()
