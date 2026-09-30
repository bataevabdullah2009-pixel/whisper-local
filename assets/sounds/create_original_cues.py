"""Rebuild the original Console and Air cues. Run manually; never used at runtime.

Soft console-interface timbres, not samples or melodies from PlayStation.
"""
from pathlib import Path
import wave
import numpy as np

RATE=48000
OUT=Path(__file__).resolve().parent

def note(track, hz, onset, duration, gain, pan=0, decay=.13, attack=.012):
    t=np.arange(int(duration*RATE))/RATE
    envelope=(1-np.exp(-t/attack))**2*np.exp(-t/decay)
    envelope*=np.minimum(1,(duration-t)/.035).clip(0,1)**2
    # Mostly the fundamental; quiet, quickly decaying upper partials add texture.
    voice=np.sin(2*np.pi*hz*t)
    voice+=.14*np.sin(2*np.pi*hz*2.002*t)*np.exp(-t/.075)
    voice+=.025*np.sin(2*np.pi*hz*3*t)*np.exp(-t/.04)
    voice*=envelope*gain
    start=int(onset*RATE); end=min(len(track),start+len(t))
    balance=np.array([np.cos((pan+1)*np.pi/4),np.sin((pan+1)*np.pi/4)])
    track[start:end]+=voice[:end-start,None]*balance

def room(track, amount):
    dry=track.copy()
    # Asymmetric early reflections give a small stereo space, not a long jingle.
    for delay,gain in ((.019,.25),(.033,.19),(.057,.12),(.083,.07),(.119,.035)):
        n=int(delay*RATE)
        track[n:,0]+=dry[:-n,1]*gain*amount
        n+=int(.007*RATE)
        track[n:,1]+=dry[:-n,0]*gain*amount

def write(name,track,peak):
    track-=track.mean(axis=0)
    # Quiet peak ceiling; no limiter, compression or hard clipping.
    track*=peak/max(np.max(np.abs(track)),1e-9)
    n=int(.012*RATE)
    track[:n]*=np.linspace(0,1,n)[:,None]
    track[-n:]*=np.linspace(1,0,n)[:,None]
    with wave.open(str(OUT/name),'wb') as wav:
        wav.setnchannels(2); wav.setsampwidth(2); wav.setframerate(RATE)
        wav.writeframes(np.round(track*32767).astype('<i2').tobytes())

def console(kind):
    audio=np.zeros((int(RATE*(.48 if kind=='start' else .58)),2))
    note(audio,523.25,0,.36,.8,-.16)
    note(audio,261.625,.002,.23,.18,0,decay=.08)
    if kind=='start':
        note(audio,783.99,.022,.32,.27,.24,decay=.11)
    else:
        note(audio,659.255,.022,.39,.23,.22,decay=.14)
        note(audio,1046.5,.052,.36,.18,-.2,decay=.12,attack=.018)
    room(audio,.8)
    write(f'console-{kind}.wav',audio,.32)

def air(kind):
    audio=np.zeros((int(RATE*(.28 if kind=='start' else .36)),2))
    note(audio,392 if kind=='start' else 523.25,0,.22,.65,-.12,decay=.062,attack=.009)
    note(audio,783.99 if kind=='start' else 1046.5,.013,.23,.16,.3,decay=.065,attack=.014)
    if kind=='insert': note(audio,659.255,.032,.27,.16,-.22,decay=.085,attack=.017)
    # A very quiet, low-passed breath softens the onset.
    rng=np.random.default_rng(6030+(kind=='insert'))
    noise=rng.normal(size=len(audio))
    spectrum=np.fft.rfft(noise)
    frequencies=np.fft.rfftfreq(len(audio),1/RATE)
    spectrum*=np.exp(-(frequencies/1400)**4)*(1-np.exp(-(frequencies/250)**2))
    breath=np.fft.irfft(spectrum,n=len(audio))
    t=np.arange(len(audio))/RATE
    breath*=.016*(1-np.exp(-t/.012))*np.exp(-t/.028)
    audio+=breath[:,None]
    room(audio,.35)
    write(f'air-{kind}.wav',audio,.24)

if __name__=='__main__':
    for kind in ('start','insert'): console(kind); air(kind)
