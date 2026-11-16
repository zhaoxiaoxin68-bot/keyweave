"""在内存中合成并播放 Windows 提示音，以音频振幅控制音量。"""

import io
import math
import os
import struct
import wave


def play_sound(event: str, volume: int) -> None:
    """同步播放约 70 毫秒提示音；由界面线程调用，静音或非 Windows 环境直接返回。"""
    if os.name != "nt" or volume <= 0:
        return
    import winsound

    frequency = {"on": 880, "off": 440, "switch": 660}[event]
    samples = bytearray()
    sample_rate = 16000
    count = int(sample_rate * 0.07)
    amplitude = 5000 * volume / 100
    for index in range(count):
        envelope = min(1, index / 80, (count - index) / 80)
        value = int(amplitude * envelope * math.sin(2 * math.pi * frequency * index / sample_rate))
        samples.extend(struct.pack("<h", value))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples)
    # SND_MEMORY 不支持异步播放，因此使用同步调用。
    winsound.PlaySound(buffer.getvalue(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
