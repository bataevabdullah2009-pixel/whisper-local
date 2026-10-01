"""Native Flow-style dictation bar. Recording shows only a waveform and controls."""
from collections import deque
import math
import os
from pathlib import Path
import time
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QPropertyAnimation, QEasingCurve, Signal
from PySide6.QtGui import (QColor, QPainter, QPen, QFont, QFontDatabase, QIcon, QPixmap,
                          QTextLayout, QTextOption, QCursor, QPalette, QPolygonF)
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QPlainTextEdit, QFrame, QApplication, QStackedWidget, QSlider)
import platform_native as native
from setup_ui import ModelPage

GREEN, RED, INK, WHITE, MUTED = [QColor(x) for x in ('#1ED760','#FF453A','#171717','#F5F5F5','#A8A8A8')]

def prepare_fonts():
    directory=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'
    for name in ('segoeui.ttf','seguisb.ttf','segoeuib.ttf','SegUIVar.ttf'):
        if (directory/name).is_file(): QFontDatabase.addApplicationFont(str(directory/name))

def font(size,weight=QFont.Weight.Normal):
    result=QFont(QApplication.font() if native.IS_MAC else QFont('Segoe UI')); result.setPixelSize(size); result.setWeight(weight)
    return result

def app_icon(size=64):
    pixmap=QPixmap(size,size); pixmap.fill(Qt.GlobalColor.transparent)
    p=QPainter(pixmap); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(INK)
    p.drawRoundedRect(QRectF(0,0,size,size),size*.24,size*.24); p.setBrush(WHITE)
    for i,height in enumerate((.24,.56,.34,.68,.23)):
        p.drawRoundedRect(QRectF(size*(.2+i*.125),size*(.5-height/2),size*.066,size*height),size*.033,size*.033)
    p.end(); return QIcon(pixmap)

class Overlay(QWidget):
    cancelRequested=Signal(); finishRequested=Signal(); copyRequested=Signal(); settingsRequested=Signal()
    retryPasteRequested=Signal(); anchorChanged=Signal(object)
    def __init__(self):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setWindowTitle('Whisper Local — диктовка')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus); self.setMouseTracking(True); self.setFixedSize(300,96)
        self.mode,self.message,self.title='recording','','Слушаю'
        self.seconds=0.; self.levels=deque([0.]*28,maxlen=28); self.display_levels=[0.]*28
        self.wave_color=QColor(GREEN); self.style='flow'; self.anchor=None; self.session_screen=None
        self.demo=False; self.clock=time.monotonic(); self.hover=''
        self.drag_start=None; self.drag_window_start=None; self.dragging=False; self.press_hit=''
        self.timer=QTimer(self); self.timer.setInterval(16); self.timer.timeout.connect(self._tick)
        self.animation=QPropertyAnimation(self,b'windowOpacity',self); self.animation.setDuration(170)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide_timer=QTimer(self); self.hide_timer.setSingleShot(True); self.hide_timer.timeout.connect(self.hide)
    def set_wave_color(self,name):
        self.wave_color=QColor(RED if name=='red' else GREEN); self.update()
    def set_style(self,name):
        self.style='mini' if name=='mini' else 'flow'
        if self.isVisible(): self._position()
        self.update()
    def set_anchor(self,anchor):
        self.anchor=anchor if isinstance(anchor,dict) else None
        if self.isVisible(): self._position()
    def _screen(self):
        if self.anchor and self.anchor.get('screen'):
            for screen in QApplication.screens():
                if screen.name()==self.anchor['screen']: return screen
        if self.isVisible() and self.session_screen in QApplication.screens():
            return self.session_screen
        return QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
    def _clamp(self,point,screen):
        rect=screen.availableGeometry()
        return QPointF(max(rect.left(),min(point.x(),rect.right()-self.width()+1)),
                       max(rect.top(),min(point.y(),rect.bottom()-self.height()+1)))
    def _position(self):
        screen=self._screen(); self.session_screen=screen; rect=screen.availableGeometry()
        anchor=self.anchor or {}; x=anchor.get('x',.5); y=anchor.get('y',.96)
        try: x=max(0.,min(1.,float(x))); y=max(0.,min(1.,float(y)))
        except (TypeError,ValueError): x,y=.5,.96
        center=self.pill_rect().center()
        point=self._clamp(QPointF(rect.x()+rect.width()*x-center.x(),
                                  rect.y()+rect.height()*y-center.y()),screen)
        self.move(point.toPoint())
    def present(self,mode,message='',seconds=0,timeout=0,title=None):
        was_visible=self.isVisible(); self.hide_timer.stop()
        self.mode,self.message,self.seconds=mode,message,seconds
        titles={'recording':'Слушаю','processing':'Распознаю…','loading':'Загрузка…','ready':'Готово','result':'Текст вставлен','manual':'Вставьте текст','copied':'Скопировано','error':'Не удалось записать','canceled':'Отменено','empty':'Не расслышал'}
        self.title=title or titles.get(mode,mode); self.setAccessibleName(self.title); self.setAccessibleDescription(message)
        self.setFixedSize(428,230) if message else self.setFixedSize(300,96)
        if mode=='recording': self.levels=deque([0.]*28,maxlen=28); self.display_levels=[0.]*28
        self._position(); self.show()
        # Offscreen/minimal Qt handles are not HWNDs or NSViews.
        if QApplication.platformName() in ('windows','cocoa'):
            native.no_activate(int(self.winId()))
        self.timer.start()
        if not was_visible:
            self.animation.stop(); self.setWindowOpacity(0); self.animation.setStartValue(0.); self.animation.setEndValue(1.); self.animation.start()
        if timeout: self.hide_timer.start(int(timeout*1000))
        self.update()
    def hideEvent(self,event):
        self.timer.stop(); self.hide_timer.stop(); self.demo=False
        self.drag_start=None; self.drag_window_start=None; self.dragging=False; self.press_hit=''; self.session_screen=None
        super().hideEvent(event)
    def _tick(self):
        if self.demo:
            t=time.monotonic()-self.clock; self.levels.append(.12+abs(math.sin(t*4.1)*math.sin(t*2.3))*.76)
        for i,target in enumerate(self.levels): self.display_levels[i]+=(target-self.display_levels[i])*.32
        self.update()
    def sample(self,level,seconds): self.levels.append(level); self.seconds=seconds
    def preview(self):
        self.present('recording',timeout=5,title='Предпросмотр'); self.demo=True; self.clock=time.monotonic()
        self.setToolTip('Предпросмотр · микрофон выключен')
    def _text(self,p,rect,text,size=13,color=WHITE,weight=QFont.Weight.Normal):
        p.setFont(font(size,weight)); p.setPen(color); p.drawText(rect,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,text)
    def _shadow(self,p,rect,radius,strength=1):
        p.setPen(Qt.PenStyle.NoPen)
        for spread in range(17,0,-1):
            p.setBrush(QColor(0,0,0,int((1+(17-spread)*.28)*strength)))
            p.drawRoundedRect(rect.adjusted(-spread,-spread+5,spread,spread+5),radius+spread,radius+spread)
    def _cross(self,p,center,color=MUTED):
        p.setPen(QPen(color,1.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap)); x,y=center.x(),center.y()
        p.drawLine(QPointF(x-3.7,y-3.7),QPointF(x+3.7,y+3.7)); p.drawLine(QPointF(x+3.7,y-3.7),QPointF(x-3.7,y+3.7))
    def _check(self,p,center,color=WHITE):
        p.setPen(QPen(color,1.9,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)); x,y=center.x(),center.y()
        p.drawPolyline(QPolygonF([QPointF(x-4,y),QPointF(x-1,y+3),QPointF(x+5,y-4)]))
    def _wrapped(self,p,text,rect):
        layout=QTextLayout(text,font(15)); option=QTextOption(); option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option); layout.beginLayout(); lines=[]
        for _ in range(2):
            line=layout.createLine()
            if not line.isValid(): break
            line.setLineWidth(rect.width()); line.setPosition(QPointF(0,len(lines)*24)); lines.append(line)
        layout.endLayout(); p.setPen(INK)
        for i,line in enumerate(lines):
            if i==1:
                p.setFont(font(15)); text=p.fontMetrics().elidedText(text[line.textStart():],Qt.TextElideMode.ElideRight,int(rect.width()))
                p.drawText(QRectF(rect.x(),rect.y()+24,rect.width(),24),Qt.AlignmentFlag.AlignVCenter,text)
            else: line.draw(p,rect.topLeft())
    def pill_rect(self):
        width,height=(184,48) if self.style=='mini' else (252,44)
        return QRectF((self.width()-width)/2,self.height()-72,width,height)
    def paintEvent(self,event):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.message:
            card=QRectF(24,18,self.width()-48,120); self._shadow(p,card,14,.7)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#FFFFFF')); p.drawRoundedRect(card,14,14)
            self._text(p,QRectF(42,29,310,23),self.title,12,QColor('#686868'),QFont.Weight.DemiBold)
            self._wrapped(p,self.message,QRectF(42,60,self.width()-86,52))
            if self.mode in ('manual','copied'):
                p.setPen(QPen(QColor('#666666'),1.4)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(self.width()-54,35,10,12),2,2)
                p.drawLine(QPointF(self.width()-57,43),QPointF(self.width()-57,32)); p.drawLine(QPointF(self.width()-57,32),QPointF(self.width()-48,32))
        pill=self.pill_rect(); self._shadow(p,pill,22); p.setBrush(QColor('#171717')); p.setPen(QPen(QColor('#343434'),.8)); p.drawRoundedRect(pill,22,22)
        left,right,y=pill.left()+24,pill.right()-24,pill.center().y()
        if self.mode=='recording':
            for name,x in (('cancel',left),('finish',right)):
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#3B3B3B' if self.hover==name else '#292929')); p.drawEllipse(QRectF(x-12,y-12,24,24))
            self._cross(p,QPointF(left,y)); self._check(p,QPointF(right,y))
            levels=self.display_levels[-18:] if self.style=='mini' else self.display_levels
            start=pill.center().x()-(len(levels)-1)*4.65/2
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(self.wave_color)
            for i,level in enumerate(levels):
                height=2.4+min(1,math.sqrt(max(0,level)))*23
                p.drawRoundedRect(QRectF(start+i*4.65,y-height/2,2.45,height),1.23,1.23)
        elif self.mode in ('processing','loading'):
            p.setPen(QPen(QColor('#F2F2F2'),1.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(left+1,y-6,12,12),int(-time.monotonic()*230*16)%5760,240*16)
            self._text(p,QRectF(left+24,y-13,pill.width()-72,26),self.title,
                       12 if self.style=='mini' else 13); self._cross(p,QPointF(right,y))
        else:
            title={'result':'Вставлено','manual':'Вставить в поле','copied':'Скопировано',
                   'error':'Настройки' if self.style=='mini' else 'Открыть настройки',
                   'empty':'Повторите','canceled':'Отменено','ready':'Можно говорить'}.get(self.mode,self.title)
            if self.mode in ('result','copied','ready'): self._check(p,QPointF(left+5,y),self.wave_color)
            elif self.mode=='error': self._cross(p,QPointF(left+5,y),RED)
            elif self.mode=='manual': self._check(p,QPointF(left+5,y),self.wave_color)
            self._text(p,QRectF(left+21,y-13,pill.width()-51,26),title,
                       12 if self.style=='mini' else 13)
        p.end()
    def _hit(self,position):
        pill=self.pill_rect()
        if self.message and self.mode in ('manual','copied') and QRectF(self.width()-65,25,34,33).contains(position): return 'copy'
        if self.message and position.y()<55: return 'drag'
        if self.message and position.y()<138 and self.mode in ('manual','copied'): return 'copy'
        if not pill.contains(position): return ''
        if self.mode=='error': return 'settings'
        if self.mode=='manual': return 'retry_paste'
        if self.mode=='recording':
            if position.x()<pill.left()+46: return 'cancel'
            if position.x()>pill.right()-46: return 'finish'
        if self.mode in ('processing','loading') and position.x()>pill.right()-46: return 'cancel'
        return 'drag'
    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton: return
        self.press_hit=self._hit(event.position())
        if self.press_hit:
            self.drag_start=event.globalPosition().toPoint(); self.drag_window_start=self.pos(); self.dragging=False
    def mouseMoveEvent(self,event):
        if self.drag_start is not None and event.buttons()&Qt.MouseButton.LeftButton:
            delta=event.globalPosition().toPoint()-self.drag_start
            if self.dragging or abs(delta.x())+abs(delta.y())>=5:
                self.dragging=True
                screen=QApplication.screenAt(event.globalPosition().toPoint()) or self._screen()
                self.move(self._clamp(QPointF(self.drag_window_start+delta),screen).toPoint())
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        self.hover=self._hit(event.position()); self.setCursor(Qt.CursorShape.PointingHandCursor if self.hover else Qt.CursorShape.ArrowCursor)
        if self.hover=='drag': self.setCursor(Qt.CursorShape.OpenHandCursor)
        tips={'cancel':'Отменить · Esc','finish':'Завершить · отпустить Alt','copy':'Скопировать текст',
              'settings':'Открыть настройки','retry_paste':'Вставить в выбранное поле','drag':'Перетащите панель'}
        self.setToolTip(tips.get(self.hover,'Предпросмотр · микрофон выключен' if self.demo else '')); self.update()
    def mouseReleaseEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton or self.drag_start is None: return
        if self.dragging:
            point=self.pos()+self.pill_rect().center().toPoint()
            screen=QApplication.screenAt(point) or self._screen(); rect=screen.availableGeometry()
            self.anchor={'screen':screen.name(),'x':max(0.,min(1.,(point.x()-rect.x())/rect.width())),
                         'y':max(0.,min(1.,(point.y()-rect.y())/rect.height()))}
            self.anchorChanged.emit(self.anchor)
        elif self._hit(event.position())==self.press_hit:
            action={'copy':self.copyRequested,'settings':self.settingsRequested,'retry_paste':self.retryPasteRequested,
                    'cancel':self.cancelRequested,'finish':self.finishRequested}.get(self.press_hit)
            if self.demo and self.press_hit in ('cancel','finish'): self.hide()
            elif action: action.emit()
        self.drag_start=None; self.drag_window_start=None; self.dragging=False; self.press_hit=''
        self.setCursor(Qt.CursorShape.OpenHandCursor)
    def leaveEvent(self,event): self.hover=''; self.update()
    def nativeEvent(self,event_type,message):
        if native.IS_MAC: return super().nativeEvent(event_type,message)
        from ctypes import wintypes
        msg=wintypes.MSG.from_address(int(message))
        if msg.message==0x21: return True,3
        return super().nativeEvent(event_type,message)

STYLE='''
QWidget { background:#FFFFFF; color:#202020; font-family:'Segoe UI'; font-size:14px; }
QWidget#sidebar, QWidget#sidebar QLabel { background:#F4F3F0; }
QLabel#brand { font-size:23px; font-weight:700; }
QLabel#title { font-size:25px; font-weight:600; }
QLabel#description, QLabel#detail { color:#686868; font-size:12px; }
QLabel#description { font-size:13px; }
QLabel#section { font-size:14px; font-weight:600; }
QLabel#key { background:#F4F4F2; border:1px solid #DFDFDD; border-radius:7px; font-size:15px; }
QLabel#status { color:#486449; font-size:12px; }
QFrame#rule { background:#EAEAE7; max-height:1px; }
QPushButton { background:#F2F2EF; border:1px solid #E5E5E2; border-radius:7px; padding:8px 14px; min-height:20px; }
QPushButton:hover { background:#EAEAE6; } QPushButton:pressed { background:#DDDDD8; }
QPushButton:focus { border:2px solid #71716C; padding:7px 13px; }
QPushButton#primary { background:#242424; border:1px solid #242424; color:white; }
QPushButton#primary:hover { background:#414141; }
QPushButton:disabled { color:#888888; background:#F3F3F0; }
QPushButton#nav { border:none; background:transparent; text-align:left; border-radius:6px; padding:10px 12px; }
QPushButton#nav:hover { background:#EAE9E5; } QPushButton#nav:checked { background:#E6E5E0; font-weight:600; }
QPushButton#nav:focus { border:1px solid #9A9993; padding:9px 11px; }
QComboBox { background:#F6F6F3; border:1px solid #E2E2DE; border-radius:7px; padding:9px 27px 9px 12px; min-height:20px; }
QComboBox:focus { border:2px solid #71716C; padding:8px 26px 8px 11px; }
QComboBox::drop-down { border:none; width:28px; } QComboBox::down-arrow { image:none; }
QComboBox QAbstractItemView { background:white; color:#202020; selection-background-color:#E8E8E3; selection-color:#202020; border:1px solid #DDD; }
QCheckBox { spacing:10px; padding:8px 0; } QCheckBox::indicator { width:18px; height:18px; }
QSlider::groove:horizontal { height:5px; background:#DEDEDA; border-radius:2px; }
QSlider::sub-page:horizontal { background:#313131; border-radius:2px; }
QSlider::handle:horizontal { background:#202020; width:16px; height:16px; margin:-6px 0; border-radius:8px; }
QPlainTextEdit { background:#FAFAF8; border:1px solid #DDDDD8; border-radius:9px; padding:14px; font-size:15px; selection-background-color:#D5EBDD; selection-color:#171717; placeholder-text-color:#686868; }
QPlainTextEdit:focus { border:2px solid #71716C; padding:13px; }
QToolTip { background:#252525; color:white; border:none; padding:6px; }
'''

class ChoiceBox(QComboBox):
    def paintEvent(self,event):
        super().paintEvent(event); p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor('#686868'),1.5,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        x,y=self.width()-18,self.height()/2; p.drawPolyline(QPolygonF([QPointF(x-3.5,y-2),QPointF(x,y+1.5),QPointF(x+3.5,y-2)])); p.end()

def label(text,kind=None):
    result=QLabel(text)
    if kind: result.setObjectName(kind)
    result.setWordWrap(True); return result

class SettingsWindow(QWidget):
    changed=Signal(); previewRequested=Signal(); retryRequested=Signal(); copyRequested=Signal()
    previewSoundRequested=Signal(str); resetPositionRequested=Signal()
    recordRequested=Signal(); permissionsRequested=Signal()
    freeMemoryRequested=Signal(); precisionRequested=Signal(str)
    PAGE_NAMES=('Основные','Модель','Панель','Звуки','Система','Проверка диктовки','Память')
    def __init__(self,config):
        super().__init__(); self.config=config
        self.setWindowTitle('Whisper Local'); self.setWindowIcon(app_icon()); self.resize(900,700); self.setMinimumSize(860,680)
        self.setStyleSheet(STYLE.replace("font-family:'Segoe UI';", "" if native.IS_MAC else "font-family:'Segoe UI';"))
        root=QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)
        sidebar=QWidget(); sidebar.setObjectName('sidebar'); sidebar.setFixedWidth(200)
        rail=QVBoxLayout(sidebar); rail.setContentsMargins(18,27,18,24); rail.setSpacing(5)
        brand=QHBoxLayout(); logo=QLabel(); logo.setPixmap(app_icon(28).pixmap(28,28)); logo.setFixedSize(28,28)
        brand.addWidget(logo); brand.addWidget(label('Whisper','brand')); rail.addLayout(brand); rail.addSpacing(33)
        rail.addWidget(label('Настройки','detail')); rail.addSpacing(7); self.nav=[]
        for i,name in enumerate(self.PAGE_NAMES):
            button=QPushButton(name); button.setObjectName('nav'); button.setCheckable(True)
            button.clicked.connect(lambda checked=False,i=i:self.show_page(i)); rail.addWidget(button); self.nav.append(button)
        rail.addStretch(); rail.addWidget(label('Whisper Local','section')); rail.addWidget(label('Работает на вашем ПК','detail')); root.addWidget(sidebar)
        content=QWidget(); main=QVBoxLayout(content); main.setContentsMargins(38,33,38,25); main.setSpacing(16)
        self.heading=label('Основные','title'); main.addWidget(self.heading)
        self.status=label('Подготавливаю распознавание…','status'); main.addWidget(self.status)
        self.pages=QStackedWidget(); main.addWidget(self.pages,1)
        general=QWidget(); form=QVBoxLayout(general); form.setContentsMargins(0,6,0,0); form.setSpacing(0)
        key=label(native.HOTKEY_SHORT,'key'); key.setAlignment(Qt.AlignmentFlag.AlignCenter); key.setMinimumSize(70,38)
        self.add_row(form,'Горячая клавиша',f'Удерживайте {native.HOTKEY_NAME}, чтобы говорить.\nОтпустите, чтобы вставить текст.',key)
        self.microphone=ChoiceBox(); self.microphone.setAccessibleName('Микрофон'); self.microphone.setFixedWidth(250)
        self.microphone.setMinimumContentsLength(17); self.microphone.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        mic=QWidget(); micbox=QVBoxLayout(mic); micbox.setContentsMargins(0,0,0,0); micbox.setSpacing(5); micbox.addWidget(self.microphone)
        refresh=QPushButton('Обновить устройства'); refresh.setFixedHeight(29)
        refresh.setStyleSheet('padding:2px 8px; border:none; background:transparent; color:#686868; font-size:12px;'); refresh.clicked.connect(self.refresh_microphones)
        micbox.addWidget(refresh,0,Qt.AlignmentFlag.AlignRight); self.add_row(form,'Микрофон','Устройство для записи речи.',mic)
        self.language=ChoiceBox(); self.language.setAccessibleName('Язык диктовки'); self.language.setFixedWidth(225)
        for text,code in (('Русский','ru'),('Автоматически',None),('English','en')): self.language.addItem(text,code)
        self.language.setCurrentIndex(max(0,self.language.findData(config.get('language','ru')))); self.add_row(form,'Язык диктовки','На каком языке вы говорите.',self.language)
        form.addStretch(); self.pages.addWidget(general)
        self.model_page=ModelPage(config); self.pages.addWidget(self.model_page)
        self.model_page.practiceRequested.connect(lambda:self.show_page(5))
        panel=QWidget(); panel_layout=QVBoxLayout(panel); panel_layout.setContentsMargins(0,6,0,0); panel_layout.setSpacing(0)
        self.panel_style=ChoiceBox(); self.panel_style.setAccessibleName('Вид панели'); self.panel_style.setFixedWidth(225)
        self.panel_style.addItem('Обычная','flow'); self.panel_style.addItem('Мини','mini')
        self.panel_style.setCurrentIndex(max(0,self.panel_style.findData(config.get('bar_style','flow'))))
        self.add_row(panel_layout,'Вид панели','Два привычных размера плавающей панели.',self.panel_style)
        self.color=ChoiceBox(); self.color.setAccessibleName('Цвет волны'); self.color.setFixedWidth(225)
        self.color.addItem('Зелёный · Spotify','green'); self.color.addItem('Красный','red')
        self.color.setCurrentIndex(max(0,self.color.findData(config.get('wave_color','green'))))
        self.add_row(panel_layout,'Цвет волны','Акцент на панели записи.',self.color)
        reset=QPushButton('Сбросить положение'); reset.clicked.connect(self.resetPositionRequested)
        self.add_row(panel_layout,'Расположение','Перетащите панель мышью в любое удобное место. Позиция сохранится.',reset,rule=False)
        panel_layout.addStretch(); preview=QPushButton('Посмотреть панель'); preview.setObjectName('primary'); preview.clicked.connect(self.previewRequested)
        panel_layout.addWidget(preview,0,Qt.AlignmentFlag.AlignRight); self.pages.addWidget(panel)
        sound=QWidget(); sound_layout=QVBoxLayout(sound); sound_layout.setContentsMargins(0,7,0,0); sound_layout.setSpacing(0)
        self.sound_enabled=QCheckBox('Звуки диктовки'); self.sound_enabled.setChecked(config.get('sound_enabled',True))
        self.add_row(sound_layout,'Сигналы','При начале записи и после вставки текста.',self.sound_enabled)
        self.sound_style=ChoiceBox(); self.sound_style.setAccessibleName('Набор звуков'); self.sound_style.setFixedWidth(225)
        for name,code in (('Wispr Flow','flow'),('Console · мягкий','console'),('Air · воздушный','air')):
            self.sound_style.addItem(name,code)
        self.sound_style.setCurrentIndex(max(0,self.sound_style.findData(config.get('sound_style','flow'))))
        self.sound_description=label('','description')
        self.add_row(sound_layout,'Набор звуков','Выберите и прослушайте оба сигнала.',self.sound_style)
        sound_layout.addWidget(self.sound_description); sound_layout.addSpacing(6)
        self.sound_style.currentIndexChanged.connect(self._describe_sound)
        self._describe_sound()
        self.sound_volume=QSlider(Qt.Orientation.Horizontal); self.sound_volume.setRange(0,100)
        self.sound_volume.setValue(int(config.get('sound_volume',65))); self.sound_volume.setFixedWidth(166)
        self.volume_label=label(str(self.sound_volume.value())+'%','detail'); self.volume_label.setFixedWidth(45)
        volume_row=QWidget(); volume_layout=QHBoxLayout(volume_row); volume_layout.setContentsMargins(0,0,0,0)
        volume_layout.addWidget(self.sound_volume); volume_layout.addWidget(self.volume_label)
        self.add_row(sound_layout,'Громкость','Уровень сигналов относительно системной громкости.',volume_row,rule=False)
        sound_layout.addStretch()
        sound_buttons=QHBoxLayout(); sound_buttons.setSpacing(10); sound_buttons.addStretch()
        play_start=QPushButton('Прослушать начало'); play_start.clicked.connect(lambda:self.previewSoundRequested.emit('start'))
        play_insert=QPushButton('Прослушать вставку'); play_insert.setObjectName('primary')
        play_insert.clicked.connect(lambda:self.previewSoundRequested.emit('insert'))
        sound_buttons.addWidget(play_start); sound_buttons.addWidget(play_insert); sound_layout.addLayout(sound_buttons)
        self.pages.addWidget(sound)
        system=QWidget(); system_layout=QVBoxLayout(system); system_layout.setContentsMargins(0,8,0,0); system_layout.setSpacing(16)
        self.autostart=QCheckBox(f'Запускать при входе в {native.SYSTEM_NAME}'); self.autostart.setChecked(config.get('autostart',False)); system_layout.addWidget(self.autostart)
        system_layout.addWidget(label('После входа приложение работает в трее. Закрытие окна настроек не останавливает диктовку.','description')); system_layout.addSpacing(18)
        system_layout.addWidget(label('Локальное распознавание','section'))
        self.engine_label=label('Модель ещё не подготовлена. Откройте раздел «Модель».','description'); system_layout.addWidget(self.engine_label); system_layout.addSpacing(18)
        system_layout.addWidget(label('Ваши записи','section')); system_layout.addWidget(label('Микрофон включается только во время диктовки. Аудио обрабатывается на компьютере и не сохраняется. Последний текст доступен до выхода из приложения.','description')); system_layout.addStretch(); self.pages.addWidget(system)
        test=QWidget(); testing=QVBoxLayout(test); testing.setContentsMargins(0,8,0,0); testing.setSpacing(16)
        testing.addWidget(label(f'Нажмите в поле, удерживайте {native.HOTKEY_NAME} и скажите пару слов. Или запустите запись кнопкой ниже.','description'))
        self.scratch=QPlainTextEdit(); self.scratch.setAccessibleName('Поле для проверки диктовки'); self.scratch.setPlaceholderText('Здесь появятся ваши слова…')
        palette=self.scratch.palette(); palette.setColor(QPalette.ColorRole.PlaceholderText,QColor('#686868')); self.scratch.setPalette(palette)
        self.scratch.setMinimumHeight(200); testing.addWidget(self.scratch,1)
        self.record_button=QPushButton('Начать проверку микрофона'); self.record_button.setObjectName('primary'); self.record_button.clicked.connect(self.recordRequested); testing.addWidget(self.record_button)
        self.latest_label=label('Esc отменяет запись. Обычные сочетания клавиш продолжают работать.','detail'); testing.addWidget(self.latest_label)
        self.copy=QPushButton('Скопировать последний текст'); self.copy.setEnabled(False); self.copy.clicked.connect(self.copyRequested); testing.addWidget(self.copy,0,Qt.AlignmentFlag.AlignRight); self.pages.addWidget(test)
        memory=QWidget(); memory_layout=QVBoxLayout(memory); memory_layout.setContentsMargins(0,8,0,0); memory_layout.setSpacing(0)
        self.ram_usage=label('Измеряем…','section')
        self.add_row(memory_layout,'Оперативная память','Приложение и его процессы.\nОбщие страницы могут учитываться дважды.',self.ram_usage)
        self.vram_usage=label('Измеряем…','section'); self.vram_usage.setMaximumWidth(270)
        self.add_row(memory_layout,'Видеопамять','Отдельная память GPU и общая память\nиз RAM по счётчикам Windows.',self.vram_usage)
        self.idle_unload=ChoiceBox(); self.idle_unload.setAccessibleName('Выгрузка модели после простоя'); self.idle_unload.setFixedWidth(225)
        for title,seconds in (('Через 1 минуту',60),('Через 5 минут',300),('Через 10 минут',600),('Через 30 минут',1800),('Не выгружать',0)):
            self.idle_unload.addItem(title,seconds)
        self.idle_unload.setCurrentIndex(max(0,self.idle_unload.findData(config.get('idle_unload_seconds',300))))
        self.add_row(memory_layout,'После простоя','Модель загрузится при новой диктовке.\nЗапись начнётся сразу; результат может задержаться.',self.idle_unload)
        self.precision=ChoiceBox(); self.precision.setAccessibleName('Точность вычислений'); self.precision.setFixedWidth(225)
        self.precision.addItem('Автоматически','auto'); self.precision.addItem('INT8 · меньше памяти','int8')
        self.precision.setCurrentIndex(max(0,self.precision.findData(config.get('compute_type','auto'))))
        self.add_row(memory_layout,'Режим вычислений','INT8 может менять скорость и точность.\nСмена режима перезагрузит активную модель.',self.precision,rule=False)
        memory_layout.addStretch()
        self.free_memory=QPushButton('Освободить память'); self.free_memory.setObjectName('primary'); self.free_memory.setEnabled(False)
        self.free_memory.clicked.connect(self.freeMemoryRequested); memory_layout.addWidget(self.free_memory,0,Qt.AlignmentFlag.AlignRight)
        self.pages.addWidget(memory)
        self.retry=QPushButton('Перезапустить распознавание'); self.retry.clicked.connect(self.retryRequested); self.retry.hide(); main.addWidget(self.retry)
        self.permission_note=label('','description'); self.permission_note.hide(); main.addWidget(self.permission_note)
        if native.IS_MAC:
            permissions=QPushButton('Разрешить горячую клавишу в macOS'); permissions.clicked.connect(self.permissionsRequested); main.addWidget(permissions)
        main.addWidget(label('Изменения сохраняются автоматически.','detail')); root.addWidget(content,1)
        self.refresh_microphones(); self.show_page(0)
        for combo in (self.microphone,self.language,self.color,self.panel_style,self.sound_style,self.idle_unload): combo.currentIndexChanged.connect(self._save)
        self.precision.currentIndexChanged.connect(lambda:self.precisionRequested.emit(self.precision.currentData()))
        self.autostart.toggled.connect(self._save)
        self.sound_enabled.toggled.connect(self._save)
        self.sound_save_timer=QTimer(self); self.sound_save_timer.setSingleShot(True)
        self.sound_save_timer.timeout.connect(self._save)
        self.sound_volume.valueChanged.connect(self._volume_changed)
        self.sound_volume.sliderReleased.connect(self._save)
    def _volume_changed(self,value):
        self.volume_label.setText(str(value)+'%')
        self.sound_save_timer.start(180)
    def set_memory_usage(self,values):
        def amount(value):
            return 'Нет данных' if value is None else f'{value / 1024**2:.0f} МиБ'
        self.ram_usage.setText(amount(values.get('rss_bytes')))
        if native.IS_MAC:
            self.vram_usage.setText('CPU · отдельная VRAM не используется')
        else:
            dedicated,shared=values.get('dedicated_bytes'),values.get('shared_bytes')
            self.vram_usage.setText('Нет данных' if dedicated is None else f'{amount(dedicated)} отдельно\n{amount(shared)} из RAM')
    def _describe_sound(self):
        descriptions={'flow':'Сигналы из официальной веб-демонстрации Wispr Flow.',
                      'console':'Мягкие объёмные тона в духе игровых консолей.',
                      'air':'Тихие лёгкие сигналы с коротким затуханием.'}
        self.sound_description.setText(descriptions[self.sound_style.currentData()])
    def add_row(self,parent,title,description,control,rule=True):
        row=QHBoxLayout(); row.setContentsMargins(0,15,0,15); row.setSpacing(18); words=QVBoxLayout(); words.setSpacing(5)
        words.addWidget(label(title,'section')); words.addWidget(label(description,'description')); row.addLayout(words,1); row.addWidget(control,0,Qt.AlignmentFlag.AlignVCenter); parent.addLayout(row)
        if rule:
            line=QFrame(); line.setObjectName('rule'); line.setFixedHeight(1); parent.addWidget(line)
    def show_page(self,index):
        self.pages.setCurrentIndex(index); self.heading.setText(self.PAGE_NAMES[index])
        for i,button in enumerate(self.nav): button.setChecked(i==index)
    def refresh_microphones(self):
        import sounddevice as sd
        self.microphone.blockSignals(True); self.microphone.clear(); self.microphone.addItem(f'По умолчанию в {native.SYSTEM_NAME}',(None,None))
        try:
            for i,device in enumerate(sd.query_devices()):
                if device['max_input_channels'] and (native.IS_MAC or device['hostapi']==0): self.microphone.addItem(device['name'],(i,device['name']))
            chosen=self.config.get('microphone')
            for index in range(self.microphone.count()):
                if self.microphone.itemData(index)[0]==chosen: self.microphone.setCurrentIndex(index); break
        except Exception: self.status.setText('Не удалось получить список микрофонов.')
        self.microphone.blockSignals(False)
    def _save(self):
        self.sound_save_timer.stop()
        device,name=self.microphone.currentData() or (None,None)
        self.config.update(microphone=device,microphone_name=name,language=self.language.currentData(),
                           wave_color=self.color.currentData(),bar_style=self.panel_style.currentData(),
                           sound_enabled=self.sound_enabled.isChecked(),sound_style=self.sound_style.currentData(),
                           sound_volume=self.sound_volume.value(),autostart=self.autostart.isChecked(),
                           idle_unload_seconds=self.idle_unload.currentData())
        self.changed.emit()
    def set_status(self,text,error=False):
        self.status.setText(text); self.status.setStyleSheet('color:#A44332;' if error else 'color:#486449;'); self.retry.setVisible(error)
    def closeEvent(self,event): event.ignore(); self.hide()
